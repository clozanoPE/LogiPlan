"""
Jornada de la flota propia (Pre-Ruta y Post-Ruta), días no laborables e incidencias de no
disponibilidad (F6). Como en servicios.py, todo lo que depende de la hora recibe `ahora`
(hora de Lima) como parámetro.

- La jornada se crea sola con la primera programación INTERNO de una placa en un día
  (`sincronizar`, llamado desde Programacion.save) y se anula si se cancelan todas.
- Pre-Ruta: empieza a la hora configurada (06:30) y termina con la primera salida. Su tramo se
  graba recién cuando la unidad sale (servicios.registrar_salida); antes, su estado se calcula.
- Post-Ruta: la entrada que registra Vigilancia cuando la placa ya no tiene programaciones
  pendientes. Su calificación compara la hora de retorno con el cierre operativo (16:00) y su
  tolerancia (16:15). Es un indicador OPERATIVO del vehículo: no es un registro de asistencia
  ni sustenta horas extra del personal.
- Domingos y feriados no son laborables: no se programa y no hay jornada.
- Una avería deja al vehículo NO DISPONIBLE (Incidencia abierta) hasta que se habilite.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from . import auditoria, tiempo
from .models import (ConfiguracionAccesos, Feriado, Incidencia, Jornada, Programacion, RegistroAuditoria,
                     TipoIncidencia, Tramo, Transporte)

Estado = Jornada.Estado
Accion = RegistroAuditoria.Accion
Clase = Tramo.Clase

MOTIVO_CREACION = "Pre-Ruta automática: primera programación INTERNO del día"


# --- DÍAS NO LABORABLES ---

def motivo_no_laborable(fecha):
    """Texto que explica por qué la fecha no es laborable (domingo o feriado), o None si lo es."""
    if fecha.weekday() == 6:
        return f"El {fecha:%d/%m/%Y} es domingo: no es un día laborable."
    feriado = Feriado.objects.filter(fecha=fecha).first()
    if feriado:
        return f"El {tiempo.fecha_larga(fecha)} es feriado ({feriado.descripcion}): no es un día laborable."
    return None


def es_laborable(fecha):
    return motivo_no_laborable(fecha) is None


def no_laborables_entre(desde, hasta):
    """{fecha: 'Domingo' o descripción del feriado} de los días no laborables del rango."""
    dias = {f: d for f, d in Feriado.objects.filter(fecha__range=(desde, hasta)).values_list('fecha', 'descripcion')}
    actual = desde
    while actual <= hasta:
        if actual.weekday() == 6:
            dias.setdefault(actual, 'Domingo')
        actual += timedelta(days=1)
    return dias


# --- DISPONIBILIDAD DEL VEHÍCULO ---

def incidencia_abierta(transporte_id):
    return Incidencia.objects.filter(transporte_id=transporte_id, abierta=True).select_related('tipo').first()


def ids_no_disponibles():
    return set(Incidencia.objects.filter(abierta=True).values_list('transporte_id', flat=True))


def texto_no_disponible(incidencia):
    return (f"{incidencia.transporte} no está disponible desde el "
            f"{tiempo.a_lima(incidencia.inicio):%d/%m/%Y %H:%M} ({incidencia.codigo}, {incidencia.tipo}).")


def bloquear_transporte(transporte_id):
    """Serializa las operaciones sobre una misma placa (portería, avería, habilitación)."""
    return Transporte.objects.select_for_update().select_related('tamano').get(pk=transporte_id)


# --- MOMENTOS Y CALIFICACIONES DE LA JORNADA ---

def inicio(jornada):
    """Hora programada de la Pre-Ruta (06:30 del día de la jornada)."""
    return tiempo.combinar(jornada.fecha, jornada.hora_inicio)


def limite_pre_ruta(jornada):
    return inicio(jornada) + timedelta(minutes=jornada.meta_pre_ruta_min)


def cierre(jornada):
    """Hora de cierre operativo (16:00 del día de la jornada)."""
    return tiempo.combinar(jornada.fecha, jornada.hora_cierre)


def limite_cierre(jornada):
    """Cierre operativo más la tolerancia (16:15)."""
    return cierre(jornada) + timedelta(minutes=jornada.tolerancia_cierre_min)


ANTICIPADO = 'ANTICIPADO'
EN_HORARIO = 'EN_HORARIO'
FUERA_DE_HORARIO = 'FUERA_DE_HORARIO'
POR_AVERIA = 'POR_AVERIA'

ETIQUETAS_CIERRE = {
    ANTICIPADO: 'Fin anticipado',
    EN_HORARIO: 'En horario',
    FUERA_DE_HORARIO: 'Fuera de horario',
    POR_AVERIA: 'Cierre por avería',
}


def calificacion_cierre(jornada):
    """Retorno (Post-Ruta) frente al cierre operativo: antes de las 16:00 = fin anticipado (informativo),
    hasta las 16:15 = en horario, después = fuera de horario. Las cerradas por avería se separan."""
    if jornada.motivo_cierre == Jornada.MotivoCierre.AVERIA:
        return POR_AVERIA
    if not jornada.post_ruta_en:
        return None
    momento = tiempo.a_lima(jornada.post_ruta_en)
    if momento < cierre(jornada):
        return ANTICIPADO
    if momento <= limite_cierre(jornada):
        return EN_HORARIO
    return FUERA_DE_HORARIO


def minutos_sobre_cierre(jornada):
    """Exceso sobre el cierre operativo, solo en retornos fuera de horario: se cuenta desde las 16:00
    (la tolerancia no suma). Es un indicador operativo del vehículo, no de horas del personal."""
    if calificacion_cierre(jornada) != FUERA_DE_HORARIO:
        return 0
    return int((jornada.post_ruta_en - cierre(jornada)).total_seconds() // 60)


def minutos_pre_ruta(jornada):
    """Alistamiento: desde el inicio real de la Pre-Ruta hasta la primera salida."""
    if not (jornada.inicio_real and jornada.primera_salida):
        return None
    return max(0, int((jornada.primera_salida - jornada.inicio_real).total_seconds() // 60))


def pre_ruta_a_tiempo(jornada):
    minutos = minutos_pre_ruta(jornada)
    return None if minutos is None else minutos <= jornada.meta_pre_ruta_min


# --- ESTADO VISIBLE ---

@dataclass(frozen=True)
class EstadoJornada:
    codigo: str
    etiqueta: str
    alerta: bool = False


def estado_visible(jornada, ahora, *, dentro=False, en_visita=False, no_disponible=False):
    """Estado para mostrar en pantalla. `dentro`: la placa tiene un tramo abierto; `en_visita`: ese tramo
    es una visita programada; `no_disponible`: tiene una incidencia abierta."""
    hoy = ahora.date()
    if jornada.estado == Estado.ANULADA:
        return EstadoJornada('ANULADA', 'Anulada')
    if jornada.estado == Estado.CERRADA:
        calificacion = calificacion_cierre(jornada)
        if calificacion is None:
            return EstadoJornada('CERRADA', 'Cerrada')
        return EstadoJornada(calificacion, ETIQUETAS_CIERRE[calificacion],
                             alerta=calificacion in (FUERA_DE_HORARIO, POR_AVERIA))
    if jornada.estado == Estado.EN_RUTA:
        if en_visita:
            return EstadoJornada('EN_VISITA', 'En planta (visita)')
        if jornada.fecha < hoy:
            return EstadoJornada('SIN_POST_RUTA', 'Sin Post-Ruta', alerta=True)
        if ahora > limite_cierre(jornada):
            return EstadoJornada('SIN_RETORNO', 'Sin retorno', alerta=True)
        return EstadoJornada('EN_RUTA', 'En ruta')
    # Planificada: todavía no salió a ruta.
    if no_disponible:
        return EstadoJornada('NO_DISPONIBLE', 'No disponible', alerta=True)
    if jornada.fecha > hoy or ahora < inicio(jornada):
        return EstadoJornada('POR_INICIAR', 'Por iniciar')
    if jornada.fecha < hoy:
        return EstadoJornada('SIN_SALIDA', 'No salió', alerta=True)
    if dentro:
        if ahora > limite_pre_ruta(jornada):
            return EstadoJornada('PRE_RUTA_EXCEDIDA', 'Pre-Ruta excedida', alerta=True)
        return EstadoJornada('EN_PRE_RUTA', 'En Pre-Ruta')
    return EstadoJornada('POR_LLEGAR', 'Por llegar')


def jornadas_del_dia(fecha, ahora, cd_ids=None):
    """Jornadas no anuladas de la fecha con su estado visible (`estado_v`), los minutos de Pre-Ruta y el
    exceso sobre el cierre, para el tablero «Hoy», el dashboard y la pantalla de TV."""
    jornadas = list(Jornada.objects.filter(fecha=fecha).exclude(estado=Estado.ANULADA)
                    .select_related('transporte__tamano', 'conductor', 'cd', 'incidencia__tipo')
                    .order_by('transporte__placa_normalizada'))
    if cd_ids is not None:
        jornadas = [j for j in jornadas if j.cd_id in cd_ids]
    abiertos = {t.transporte_id: t for t in Tramo.objects.filter(abierto=True, transporte_id__in=[j.transporte_id for j in jornadas])}
    no_disponibles = ids_no_disponibles()
    for jornada in jornadas:
        tramo = abiertos.get(jornada.transporte_id)
        jornada.tramo_abierto = tramo
        jornada.estado_v = estado_visible(jornada, ahora, dentro=tramo is not None,
                                          en_visita=bool(tramo and tramo.es_programado),
                                          no_disponible=jornada.transporte_id in no_disponibles)
        jornada.minutos_pre_ruta = minutos_pre_ruta(jornada)
        jornada.minutos_sobre_cierre = minutos_sobre_cierre(jornada)
        jornada.calificacion = calificacion_cierre(jornada)
    return jornadas


# --- SINCRONIZACIÓN CON LAS PROGRAMACIONES ---

def jornada_vigente(transporte_id, fecha):
    """Jornada no anulada de la placa en la fecha, o None."""
    return (Jornada.objects.filter(transporte_id=transporte_id, fecha=fecha).exclude(estado=Estado.ANULADA)
            .select_related('cd', 'conductor').first())


def _primera_interna(transporte_id, fecha):
    return (Programacion.objects.filter(transporte_id=transporte_id, fecha=fecha, tipo=Programacion.Tipo.INTERNO)
            .exclude(estado=Programacion.Estado.CANCELADA).order_by('hora', 'pk').first())


def sincronizar(transporte_id, fecha, ahora=None):
    """Mantiene la jornada de la placa en esa fecha de acuerdo con sus programaciones INTERNO activas:
    la crea con la primera (una sola por día), la reactiva si estaba anulada y la anula si ya no queda
    ninguna. Una jornada que ya tuvo movimiento (en ruta o cerrada) nunca se toca: son hechos."""
    primera = _primera_interna(transporte_id, fecha)
    jornada = Jornada.objects.filter(transporte_id=transporte_id, fecha=fecha).first()
    if primera is None:
        if jornada and jornada.estado == Estado.PLANIFICADA and not jornada.tramos.exists():
            foto = auditoria.foto(jornada)
            jornada.estado = Estado.ANULADA
            jornada.save()
            auditoria.registrar_modificacion(None, jornada, foto, motivo="Sin programaciones INTERNO activas ese día.")
        return jornada

    if jornada is None:
        config = ConfiguracionAccesos.obtener()
        ahora = ahora or tiempo.ahora()
        jornada = Jornada(transporte_id=transporte_id, fecha=fecha, cd_id=primera.cd_id,
                          conductor_id=primera.conductor_id, hora_inicio=config.hora_pre_ruta,
                          meta_pre_ruta_min=config.meta_pre_ruta_min, hora_cierre=config.hora_cierre_operativo,
                          tolerancia_cierre_min=config.tolerancia_cierre_min,
                          programada_tarde=ahora > tiempo.combinar(fecha, config.hora_pre_ruta))
        try:
            with transaction.atomic():
                jornada.save()
        except IntegrityError:
            # Otra petición la creó al mismo tiempo. Lectura con bloqueo: en MySQL (REPEATABLE READ) una lectura
            # normal dentro de esta transacción todavía no vería la fila que la otra acaba de confirmar.
            return Jornada.objects.select_for_update().get(transporte_id=transporte_id, fecha=fecha)
        auditoria.registrar(None, Accion.CREAR, jornada, despues=auditoria.foto(jornada),
                            motivo=f"{MOTIVO_CREACION} ({primera.codigo}).", cd=jornada.cd)
        return jornada

    if jornada.estado in (Estado.ANULADA, Estado.PLANIFICADA):
        foto = auditoria.foto(jornada)
        reactivada = jornada.estado == Estado.ANULADA
        jornada.estado = Estado.PLANIFICADA
        if not jornada.tramos.exists():
            # Todavía sin movimiento: CD y conductor previstos siguen a la primera programación del día.
            jornada.cd_id = primera.cd_id
            jornada.conductor_id = primera.conductor_id
        if auditoria.diferencias(foto, auditoria.foto(jornada))[1]:
            jornada.save()
            auditoria.registrar_modificacion(
                None, jornada, foto,
                motivo=f"Reactivada con {primera.codigo}." if reactivada else f"Primera ruta del día: {primera.codigo}.")
    return jornada


# --- PROGRAMACIONES PENDIENTES DE LA JORNADA ---

def pendientes(transporte_id, fecha):
    """Programaciones del día que la placa todavía puede atender (en cualquier CD)."""
    return (Programacion.objects.filter(transporte_id=transporte_id, fecha=fecha,
                                        estado=Programacion.Estado.PROGRAMADA, no_atendida_por_cierre=False)
            .select_related('cd').order_by('hora'))


def marcar_no_atendidas(transporte_id, fecha, usuario, motivo):
    """Al cerrar la jornada (Post-Ruta), las pendientes quedan «No atendida por cierre de jornada»."""
    marcadas = list(pendientes(transporte_id, fecha))
    for programacion in marcadas:
        foto = auditoria.foto(programacion)
        programacion.no_atendida_por_cierre = True
        programacion.save(update_fields=['no_atendida_por_cierre', 'modificado_en'])
        auditoria.registrar_modificacion(usuario, programacion, foto, motivo=motivo)
    return marcadas


def desmarcar_no_atendidas(transporte_id, fecha, usuario, motivo):
    """Si se anula la Post-Ruta, las programaciones vuelven a quedar pendientes."""
    marcadas = list(Programacion.objects.filter(transporte_id=transporte_id, fecha=fecha,
                                                estado=Programacion.Estado.PROGRAMADA, no_atendida_por_cierre=True))
    for programacion in marcadas:
        foto = auditoria.foto(programacion)
        programacion.no_atendida_por_cierre = False
        programacion.save(update_fields=['no_atendida_por_cierre', 'modificado_en'])
        auditoria.registrar_modificacion(usuario, programacion, foto, motivo=motivo)
    return marcadas


def cancelar_por_averia(transporte_id, fecha, incidencia, usuario, ahora):
    """Cancela las pendientes del día por la avería del vehículo (el sistema lo hace aunque ya pasó la hora)."""
    canceladas = list(pendientes(transporte_id, fecha))
    motivo = f"Avería del vehículo: {incidencia.codigo} ({incidencia.tipo})."
    for programacion in canceladas:
        foto = auditoria.foto(programacion)
        programacion.estado = Programacion.Estado.CANCELADA
        programacion.cancelado_por = usuario
        programacion.cancelado_en = ahora
        programacion.motivo_cancelacion = motivo
        programacion.incidencia = incidencia
        programacion.modificado_por = usuario
        programacion.save()
        auditoria.registrar_modificacion(usuario, programacion, foto, accion=Accion.CANCELAR, motivo=motivo)
    return canceladas


def programaciones_afectadas(desde, transporte_ids=None):
    """Programaciones pendientes desde `desde` de vehículos no disponibles: Despacho debe cambiarles la
    placa o cancelarlas (no se cancelan solas, por si la avería se resuelve antes)."""
    ids = ids_no_disponibles() if transporte_ids is None else set(transporte_ids) & ids_no_disponibles()
    return (Programacion.objects.filter(transporte_id__in=ids, fecha__gte=desde, estado=Programacion.Estado.PROGRAMADA,
                                        no_atendida_por_cierre=False)
            .select_related('cd', 'transporte').order_by('fecha', 'hora'))


# --- INCIDENCIAS (AVERÍA = NO DISPONIBLE) ---

def abrir_incidencia(*, transporte, tipo_id, descripcion, usuario, ahora, cd=None,
                     origen=Incidencia.Origen.MANUAL):
    """Registra la no disponibilidad. Debe llamarse con el transporte bloqueado (bloquear_transporte)."""
    descripcion = (descripcion or '').strip()
    tipo = TipoIncidencia.objects.filter(pk=tipo_id, activo=True).first() if tipo_id else None
    if tipo is None:
        raise ValidationError("Elija el tipo de avería o falla.")
    if not descripcion:
        raise ValidationError("Describa la avería o falla.")
    abierta = incidencia_abierta(transporte.pk)
    if abierta:
        raise ValidationError(texto_no_disponible(abierta))
    incidencia = Incidencia(transporte=transporte, tipo=tipo, descripcion=descripcion, origen=origen, cd=cd,
                            inicio=ahora, registrada_por=usuario)
    try:
        with transaction.atomic():
            incidencia.save()
    except IntegrityError:
        raise ValidationError("El vehículo ya quedó no disponible en otra pantalla. Actualice la página.")
    auditoria.registrar(usuario, Accion.INCIDENCIA, incidencia, despues=auditoria.foto(incidencia),
                        motivo=descripcion, cd=cd)
    return incidencia


def cerrar_jornada_por_averia(jornada, incidencia, usuario, ahora):
    foto = auditoria.foto(jornada)
    jornada.estado = Estado.CERRADA
    jornada.motivo_cierre = Jornada.MotivoCierre.AVERIA
    jornada.incidencia = incidencia
    jornada.save()
    auditoria.registrar_modificacion(usuario, jornada, foto, motivo=f"Jornada cerrada por avería ({incidencia.codigo}).")
    return cancelar_por_averia(jornada.transporte_id, jornada.fecha, incidencia, usuario, ahora)


def registrar_no_disponibilidad(*, transporte_id, tipo_id, descripcion, usuario, ahora, cd=None):
    """Despacho o Administrador, desde la ficha del transporte (avería en ruta o detectada en el CD).
    Cierra la jornada de hoy por avería y cancela sus pendientes. Devuelve (incidencia, canceladas)."""
    with transaction.atomic():
        transporte = bloquear_transporte(transporte_id)
        incidencia = abrir_incidencia(transporte=transporte, tipo_id=tipo_id, descripcion=descripcion,
                                      usuario=usuario, ahora=ahora, cd=cd)
        jornada = jornada_vigente(transporte.pk, ahora.date())
        canceladas = []
        if jornada and jornada.estado in (Estado.PLANIFICADA, Estado.EN_RUTA):
            canceladas = cerrar_jornada_por_averia(jornada, incidencia, usuario, ahora)
        else:
            canceladas = cancelar_por_averia(transporte.pk, ahora.date(), incidencia, usuario, ahora)
    return incidencia, canceladas


def habilitar(incidencia, *, usuario, ahora, observacion='', fin=None):
    """Cierra la incidencia: el vehículo vuelve a estar disponible para programar."""
    fin = fin or ahora
    if not incidencia.esta_abierta:
        raise ValidationError("El vehículo ya está habilitado.")
    if fin > ahora:
        raise ValidationError("La hora de habilitación no puede ser futura.")
    if fin < incidencia.inicio:
        raise ValidationError("La habilitación no puede ser anterior al inicio de la avería.")
    with transaction.atomic():
        bloquear_transporte(incidencia.transporte_id)
        foto = auditoria.foto(incidencia)
        incidencia.fin = fin
        incidencia.abierta = None
        incidencia.cerrada_por = usuario
        incidencia.observacion_cierre = (observacion or '').strip()
        incidencia.save()
        auditoria.registrar_modificacion(usuario, incidencia, foto, accion=Accion.HABILITAR,
                                         motivo=incidencia.observacion_cierre or "Vehículo habilitado.")
    return incidencia


# --- AVISOS PARA LA CAMPANITA (Administrador y Despacho) ---

@dataclass(frozen=True)
class AvisoFlota:
    icono: str
    titulo: str
    texto: str
    url_nombre: str
    url_query: str = ''


def avisos_flota(ahora, dias_atras=7):
    """Lo que requiere acción: vehículos no disponibles (con sus programaciones afectadas) y jornadas de días
    anteriores que quedaron sin Post-Ruta (las corrige el Administrador)."""
    avisos = []
    afectadas = {}
    for programacion in programaciones_afectadas(ahora.date()):
        afectadas[programacion.transporte_id] = afectadas.get(programacion.transporte_id, 0) + 1
    for incidencia in Incidencia.objects.filter(abierta=True).select_related('transporte', 'tipo').order_by('inicio'):
        texto = f"{incidencia.tipo} · desde el {tiempo.a_lima(incidencia.inicio):%d/%m %H:%M}"
        if afectadas.get(incidencia.transporte_id):
            n = afectadas[incidencia.transporte_id]
            texto += f" · {n} programación{'es' if n > 1 else ''} por reasignar"
        avisos.append(AvisoFlota('fa-screwdriver-wrench', f"{incidencia.transporte} no disponible", texto,
                                 'accesos:flota_disponibilidad'))
    sin_post_ruta = (Jornada.objects.filter(estado=Estado.EN_RUTA, fecha__lt=ahora.date(),
                                            fecha__gte=ahora.date() - timedelta(days=dias_atras))
                     .select_related('transporte').order_by('-fecha'))
    for jornada in sin_post_ruta:
        avisos.append(AvisoFlota('fa-flag', f"{jornada.transporte} sin Post-Ruta",
                                 f"Jornada del {jornada.fecha:%d/%m}: no registró su retorno al CD",
                                 'accesos:tramos_lista',
                                 f"desde={jornada.fecha.isoformat()}&hasta={ahora.date().isoformat()}"
                                 f"&q={jornada.transporte.placa_normalizada}"))
    return avisos
