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
  tolerancia (16:15); los minutos fuera de horario se cuentan sobre las 16:15. Es un indicador
  OPERATIVO del vehículo: no es un registro de asistencia ni sustenta horas extra del personal.
- En las rutas INTERNO la hora programada es la de SALIDA del CD: la primera ruta del día la
  atiende la salida a ruta que cierra la Pre-Ruta (servicios.primera_ruta_pendiente).
- Domingos y feriados no son laborables: no se programa y no hay jornada.
- Una avería deja al vehículo NO DISPONIBLE (Incidencia abierta) hasta que se habilite.

Turnos (decisión del usuario del 15/10/2026): cada placa puede tener por fecha una jornada DÍA y una NOCHE, cada
una con su conductor, sus rutas, su Pre-Ruta y su Post-Ruta.
- La franja de la hora de SALIDA decide el turno (`turno_de`): desde las 20:00 es turno noche de esa fecha; hasta
  las 06:15 es turno noche del día anterior (si ese día hubo turno noche permitido); el resto es turno día.
- Turno noche: Pre-Ruta 20:00 (meta 60 min) y cierre 06:15 + 15 min del día siguiente; sin «fin anticipado».
  De lunes a viernes; el sábado es excepcional (aviso al programar); nunca la noche de un feriado ni la anterior.
- Cada ruta queda ligada a su jornada (Programacion.jornada). Portería trabaja con la jornada EN CURSO de la placa
  (`en_curso`): solo ofrece las rutas de ese turno, y la Post-Ruta cierra solo las de ese turno.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from . import auditoria, tiempo
from .models import (ConfiguracionAccesos, Feriado, Incidencia, Jornada, Programacion, RegistroAuditoria,
                     TipoIncidencia, Tramo, Transporte)

Estado = Jornada.Estado
Accion = RegistroAuditoria.Accion
Clase = Tramo.Clase
DIA = Jornada.Turno.DIA
NOCHE = Jornada.Turno.NOCHE

MOTIVO_CREACION = "Pre-Ruta automática: primera programación INTERNO del día"
MOTIVO_CREACION_NOCHE = "Pre-Ruta automática: primera programación INTERNO del turno noche"


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


# --- TURNOS DÍA Y NOCHE ---

def noche_permitida(fecha):
    """¿Puede haber turno noche que empiece en `fecha`? De lunes a sábado (el sábado es excepcional), nunca la
    noche de un feriado ni la anterior a un feriado (decisión del usuario del 15/10/2026)."""
    if fecha.weekday() == 6:
        return False
    return not Feriado.objects.filter(fecha__in=[fecha, fecha + timedelta(days=1)]).exists()


def turno_de(fecha, hora, config):
    """(fecha de la jornada, turno) de una ruta INTERNO por la franja de su hora de salida: desde la Pre-Ruta noche
    (20:00) es turno noche de esa fecha; hasta el cierre noche (06:15) es turno noche del día anterior, si ese día
    hubo turno noche permitido (si no, es una ruta de madrugada del turno día); el resto, turno día."""
    if hora >= config.hora_pre_ruta_noche:
        return fecha, NOCHE
    if hora <= config.hora_cierre_noche:
        anterior = fecha - timedelta(days=1)
        if noche_permitida(anterior):
            return anterior, NOCHE
    return fecha, DIA


def motivo_no_programable(fecha, hora, tipo, config):
    """Por qué no se puede programar en esa fecha y hora (domingo, feriado o una noche sin turno), o None. Las
    salidas de madrugada de un turno noche permitido sí se programan aunque caigan en domingo (vienen del sábado)."""
    if tipo == Programacion.Tipo.INTERNO and hora is not None:
        dia, turno = turno_de(fecha, hora, config)
        if turno == NOCHE and dia != fecha:
            return None
        if turno == NOCHE and es_laborable(fecha) and not noche_permitida(fecha):
            siguiente = fecha + timedelta(days=1)
            return (f"El {tiempo.fecha_larga(siguiente)} es feriado: no hay turno noche la noche anterior a un "
                    "feriado.")
    motivo = motivo_no_laborable(fecha)
    return f"{motivo} No se programa en domingos ni feriados." if motivo else None


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
    """Hora programada de la Pre-Ruta (06:30 del día de la jornada; 20:00 en el turno noche)."""
    return tiempo.combinar(jornada.fecha, jornada.hora_inicio)


def limite_pre_ruta(jornada):
    return inicio(jornada) + timedelta(minutes=jornada.meta_pre_ruta_min)


def fecha_cierre(jornada):
    """Día del cierre: el de la jornada, o el siguiente en el turno noche (termina de madrugada)."""
    return jornada.fecha + timedelta(days=1) if jornada.es_noche else jornada.fecha


def cierre(jornada):
    """Hora de cierre operativo (16:00 del día de la jornada; 06:15 del día siguiente en el turno noche)."""
    return tiempo.combinar(fecha_cierre(jornada), jornada.hora_cierre)


def limite_cierre(jornada):
    """Cierre operativo más la tolerancia (16:15; 06:30 en el turno noche)."""
    return cierre(jornada) + timedelta(minutes=jornada.tolerancia_cierre_min)


def turno_terminado(jornada, ahora):
    """Ya pasó el día en que el turno debía cerrar (para «Sin Post-Ruta» y los indicadores de días terminados)."""
    return ahora.date() > fecha_cierre(jornada)


def vencida_sin_salir(jornada, ahora):
    """Planificada que ya no saldrá («No salió»): el turno día al terminar su día; el turno noche al pasar su
    cierre (06:30), cuando empieza el turno día."""
    return ahora > limite_cierre(jornada) if jornada.es_noche else turno_terminado(jornada, ahora)


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
    hasta las 16:15 = en horario, después = fuera de horario. Las cerradas por avería se separan. En el turno noche
    no hay «fin anticipado» (volver de madrugada es lo normal): hasta las 06:30 es en horario."""
    if jornada.motivo_cierre == Jornada.MotivoCierre.AVERIA:
        return POR_AVERIA
    if not jornada.post_ruta_en:
        return None
    momento = tiempo.a_lima(jornada.post_ruta_en)
    if momento < cierre(jornada) and not jornada.es_noche:
        return ANTICIPADO
    if momento <= limite_cierre(jornada):
        return EN_HORARIO
    return FUERA_DE_HORARIO


def minutos_sobre_cierre(jornada):
    """Minutos fuera de horario del retorno: se cuentan sobre el horario de referencia con su tolerancia
    (16:15), decisión del usuario del 14/10/2026 (antes se contaban desde las 16:00). Solo en retornos fuera de
    horario. Es un indicador operativo del vehículo, no de horas del personal."""
    if calificacion_cierre(jornada) != FUERA_DE_HORARIO:
        return 0
    return int((jornada.post_ruta_en - limite_cierre(jornada)).total_seconds() // 60)


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
        if turno_terminado(jornada, ahora):
            return EstadoJornada('SIN_POST_RUTA', 'Sin Post-Ruta', alerta=True)
        if ahora > limite_cierre(jornada):
            return EstadoJornada('SIN_RETORNO', 'Sin retorno', alerta=True)
        return EstadoJornada('EN_RUTA', 'En ruta')
    # Planificada: todavía no salió a ruta.
    if no_disponible:
        return EstadoJornada('NO_DISPONIBLE', 'No disponible', alerta=True)
    if ahora < inicio(jornada):
        return EstadoJornada('POR_INICIAR', 'Por iniciar')
    if vencida_sin_salir(jornada, ahora):
        return EstadoJornada('SIN_SALIDA', 'No salió', alerta=True)
    if dentro:
        if ahora > limite_pre_ruta(jornada):
            return EstadoJornada('PRE_RUTA_EXCEDIDA', 'Pre-Ruta excedida', alerta=True)
        return EstadoJornada('EN_PRE_RUTA', 'En Pre-Ruta')
    return EstadoJornada('POR_LLEGAR', 'Por llegar')


def _anotar(jornadas, ahora):
    """Estado visible (`estado_v`), minutos de Pre-Ruta, exceso sobre el cierre y calificación de cada jornada."""
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


def _con_relaciones(queryset):
    return queryset.select_related('transporte__tamano', 'conductor', 'cd', 'incidencia__tipo')


def jornadas_del_dia(fecha, ahora, cd_ids=None):
    """Jornadas no anuladas que empiezan en la fecha (turno día y turno noche) con su estado visible (`estado_v`),
    los minutos de Pre-Ruta y el exceso sobre el cierre, para la agenda, el dashboard y la Trazabilidad."""
    jornadas = list(_con_relaciones(Jornada.objects.filter(fecha=fecha).exclude(estado=Estado.ANULADA))
                    .order_by('transporte__placa_normalizada', 'turno'))
    if cd_ids is not None:
        jornadas = [j for j in jornadas if j.cd_id in cd_ids]
    return _anotar(jornadas, ahora)


HORAS_EN_VIVO_TRAS_EL_CIERRE = 6


def _sigue_en_vivo(jornada, ahora):
    """Turno noche de ayer que todavía importa hoy: en curso, por salir dentro de su ventana, o cerrado hoy hasta
    unas horas después de su cierre (06:30 + 6 h): por la tarde ya no se mezcla con los turnos de hoy."""
    if jornada.estado == Estado.EN_RUTA:
        return True
    if jornada.estado == Estado.PLANIFICADA:
        return not vencida_sin_salir(jornada, ahora)
    cerrada_en = jornada.post_ruta_en or (jornada.incidencia.inicio if jornada.incidencia_id else None)
    return bool(cerrada_en and tiempo.a_lima(cerrada_en).date() == ahora.date()
                and ahora <= limite_cierre(jornada) + timedelta(hours=HORAS_EN_VIVO_TRAS_EL_CIERRE))


def jornadas_en_vivo(ahora, cd_ids=None):
    """Las jornadas de hoy (día y noche) más el turno noche de ayer si sigue en curso o cerró hoy (vuelve de
    madrugada): para el tablero «Hoy» y la pantalla de TV."""
    hoy = ahora.date()
    ayer = [j for j in _con_relaciones(Jornada.objects.filter(fecha=hoy - timedelta(days=1), turno=NOCHE)
                                       .exclude(estado=Estado.ANULADA))
            if (cd_ids is None or j.cd_id in cd_ids) and _sigue_en_vivo(j, ahora)]
    del_dia = list(_con_relaciones(Jornada.objects.filter(fecha=hoy).exclude(estado=Estado.ANULADA)))
    if cd_ids is not None:
        del_dia = [j for j in del_dia if j.cd_id in cd_ids]
    jornadas = sorted(ayer + del_dia, key=lambda j: (j.transporte.placa_normalizada, inicio(j)))
    return _anotar(jornadas, ahora)


# --- JORNADA EN CURSO DE UNA PLACA (portería) ---

def jornada_vigente(transporte_id, fecha, turno=DIA):
    """Jornada no anulada de la placa en la fecha y turno (por defecto, el turno día), o None."""
    return (Jornada.objects.filter(transporte_id=transporte_id, fecha=fecha, turno=turno).exclude(estado=Estado.ANULADA)
            .select_related('cd', 'conductor').first())


def _alrededor(transporte_id, ahora):
    """(turno noche de ayer, turno día de hoy, turno noche de hoy) de la placa, no anuladas (o None)."""
    hoy = ahora.date()
    ayer = hoy - timedelta(days=1)
    lista = (Jornada.objects.filter(transporte_id=transporte_id, fecha__in=[ayer, hoy]).exclude(estado=Estado.ANULADA)
             .select_related('cd', 'conductor'))
    por_clave = {(j.fecha, j.turno): j for j in lista}
    return por_clave.get((ayer, NOCHE)), por_clave.get((hoy, DIA)), por_clave.get((hoy, NOCHE))


def inicio_operativo(jornada, config):
    """Desde cuándo una salida de la unidad ya cuenta como salida a ruta de esta jornada planificada: la ventana de
    sugerencia (60 min) antes de su Pre-Ruta o de su primera ruta, lo que ocurra primero."""
    primera = pendientes(jornada).filter(tipo=Programacion.Tipo.INTERNO).first()
    momento = inicio(jornada)
    if primera is not None:
        momento = min(momento, tiempo.combinar(primera.fecha, primera.hora))
    return momento - timedelta(minutes=config.ventana_sugerencia_min)


def en_curso(transporte_id, ahora, config=None):
    """La jornada que portería atiende ahora para la placa, o None (entre turnos o sin turno):
    1. el turno noche de ayer, si sigue en ruta o todavía puede salir (hasta su cierre, 06:30);
    2. un turno de hoy en ruta (si vuelve después de que empezó el siguiente, su Post-Ruta lo abre);
    3. el turno noche de hoy, planificado, desde una hora antes de su Pre-Ruta o de su primera ruta;
    4. el turno día de hoy, planificado (desde la medianoche, como siempre).
    Una jornada en ruta de días anteriores (sin Post-Ruta) ya no se atiende: la corrige el Administrador."""
    noche_ayer, dia, noche = _alrededor(transporte_id, ahora)
    if noche_ayer and (noche_ayer.estado == Estado.EN_RUTA
                       or (noche_ayer.estado == Estado.PLANIFICADA and ahora <= limite_cierre(noche_ayer))):
        return noche_ayer
    if noche and noche.estado == Estado.EN_RUTA:
        return noche
    if dia and dia.estado == Estado.EN_RUTA:
        return dia
    if noche and noche.estado == Estado.PLANIFICADA and ahora >= inicio_operativo(noche, config or ConfiguracionAccesos.obtener()):
        return noche
    if dia and dia.estado == Estado.PLANIFICADA:
        return dia
    return None


def siguiente(jornada):
    """El turno que sigue de la placa (noche del mismo día o día siguiente) si está planificado: se muestra al
    registrar la Post-Ruta («Próximo turno: noche 20:00»)."""
    fecha, turno = (jornada.fecha, NOCHE) if not jornada.es_noche else (jornada.fecha + timedelta(days=1), DIA)
    return (Jornada.objects.filter(transporte_id=jornada.transporte_id, fecha=fecha, turno=turno,
                                   estado=Estado.PLANIFICADA).select_related('conductor').first())


def para_reingreso(transporte_id, ahora):
    """Sin jornada en curso: la última jornada ya cerrada (hoy o el turno noche de ayer), a la que se liga el reingreso
    de una unidad que salió después de su Post-Ruta. None si no hay."""
    cerradas = [j for j in _alrededor(transporte_id, ahora) if j and j.estado == Estado.CERRADA and inicio(j) <= ahora]
    return max(cerradas, key=inicio) if cerradas else None


# --- SINCRONIZACIÓN CON LAS PROGRAMACIONES ---

def _horarios(turno, config):
    if turno == NOCHE:
        return dict(hora_inicio=config.hora_pre_ruta_noche, meta_pre_ruta_min=config.meta_pre_ruta_noche_min,
                    hora_cierre=config.hora_cierre_noche, tolerancia_cierre_min=config.tolerancia_cierre_noche_min)
    return dict(hora_inicio=config.hora_pre_ruta, meta_pre_ruta_min=config.meta_pre_ruta_min,
                hora_cierre=config.hora_cierre_operativo, tolerancia_cierre_min=config.tolerancia_cierre_min)


def _obtener_o_crear(programacion, fecha, turno, config, ahora):
    """Jornada (anulada o no) de la placa en esa fecha y turno; si no existe, la crea con esta programación."""
    filtro = dict(transporte_id=programacion.transporte_id, fecha=fecha, turno=turno)
    jornada = Jornada.objects.filter(**filtro).first()
    if jornada is not None:
        return jornada
    horarios = _horarios(turno, config)
    jornada = Jornada(cd_id=programacion.cd_id, conductor_id=programacion.conductor_id,
                      programada_tarde=ahora > tiempo.combinar(fecha, horarios['hora_inicio']), **filtro, **horarios)
    try:
        with transaction.atomic():
            jornada.save()
    except IntegrityError:
        # Otra petición la creó al mismo tiempo. Lectura con bloqueo: en MySQL (REPEATABLE READ) una lectura
        # normal dentro de esta transacción todavía no vería la fila que la otra acaba de confirmar.
        return Jornada.objects.select_for_update().get(**filtro)
    motivo = MOTIVO_CREACION_NOCHE if turno == NOCHE else MOTIVO_CREACION
    auditoria.registrar(None, Accion.CREAR, jornada, despues=auditoria.foto(jornada),
                        motivo=f"{motivo} ({programacion.codigo}).", cd=jornada.cd)
    return jornada


def _sincronizar_jornada(jornada):
    """Mantiene la jornada de acuerdo con sus rutas INTERNO activas: la reactiva si estaba anulada, sigue el CD y
    el conductor de la primera mientras no tenga movimiento, y la anula si ya no queda ninguna. Una jornada que ya
    tuvo movimiento (en ruta o cerrada) nunca se toca: son hechos."""
    primera = (Programacion.objects.filter(jornada=jornada, tipo=Programacion.Tipo.INTERNO)
               .exclude(estado=Programacion.Estado.CANCELADA).order_by('fecha', 'hora', 'pk').first())
    if primera is None:
        if jornada.estado == Estado.PLANIFICADA and not jornada.tramos.exists():
            foto = auditoria.foto(jornada)
            jornada.estado = Estado.ANULADA
            jornada.save()
            texto = "en el turno noche." if jornada.es_noche else "ese día."
            auditoria.registrar_modificacion(None, jornada, foto, motivo=f"Sin programaciones INTERNO activas {texto}")
        return jornada
    if jornada.estado in (Estado.ANULADA, Estado.PLANIFICADA):
        foto = auditoria.foto(jornada)
        reactivada = jornada.estado == Estado.ANULADA
        jornada.estado = Estado.PLANIFICADA
        if not jornada.tramos.exists():
            # Todavía sin movimiento: CD y conductor previstos siguen a la primera programación del turno.
            jornada.cd_id = primera.cd_id
            jornada.conductor_id = primera.conductor_id
        if auditoria.diferencias(foto, auditoria.foto(jornada))[1]:
            jornada.save()
            auditoria.registrar_modificacion(
                None, jornada, foto,
                motivo=f"Reactivada con {primera.codigo}." if reactivada else
                f"Primera ruta del {'turno noche' if jornada.es_noche else 'día'}: {primera.codigo}.")
    return jornada


def sincronizar(programacion, anterior_id=None, ahora=None):
    """Liga la programación INTERNO a su jornada (por la franja de su hora de salida: turno día o noche) y
    sincroniza esa jornada y la anterior si cambió (`anterior_id`). Una ruta cancelada o ya atendida se queda en su
    jornada (son hechos); las de otros tipos no tienen jornada. Devuelve la jornada de la programación (o None)."""
    destino = None
    if programacion.tipo == Programacion.Tipo.INTERNO:
        atendida = programacion.estado in (Programacion.Estado.EN_PLANTA, Programacion.Estado.CERRADA)
        if (programacion.esta_cancelada or atendida) and programacion.jornada_id:
            destino = Jornada.objects.get(pk=programacion.jornada_id)
        elif not programacion.esta_cancelada:
            config = ConfiguracionAccesos.obtener()
            fecha, turno = turno_de(programacion.fecha, programacion.hora, config)
            destino = _obtener_o_crear(programacion, fecha, turno, config, ahora or tiempo.ahora())
    destino_id = destino.pk if destino else None
    if programacion.jornada_id != destino_id or anterior_id != destino_id:
        Programacion.objects.filter(pk=programacion.pk).update(jornada_id=destino_id)
        programacion.jornada_id = destino_id
    if destino is not None:
        _sincronizar_jornada(destino)
    if anterior_id and anterior_id != destino_id:
        _sincronizar_jornada(Jornada.objects.get(pk=anterior_id))
    return destino


# --- PROGRAMACIONES PENDIENTES DE LA JORNADA ---

def pendientes(jornada):
    """Rutas del turno que la placa todavía puede atender (en cualquier CD), en orden de salida."""
    return (Programacion.objects.filter(jornada=jornada, estado=Programacion.Estado.PROGRAMADA,
                                        no_atendida_por_cierre=False)
            .select_related('cd').order_by('fecha', 'hora', 'pk'))


def pendientes_del_dia(transporte_id, fecha):
    """Programaciones de la fecha que la placa todavía puede atender (en cualquier CD, de cualquier turno)."""
    return (Programacion.objects.filter(transporte_id=transporte_id, fecha=fecha,
                                        estado=Programacion.Estado.PROGRAMADA, no_atendida_por_cierre=False)
            .select_related('cd').order_by('hora'))


def marcar_no_atendidas(jornada, usuario, motivo):
    """Al cerrar la jornada (Post-Ruta), sus pendientes quedan «No atendida por cierre de jornada». Solo las de ese
    turno: las del otro turno de la placa siguen pendientes."""
    marcadas = list(pendientes(jornada))
    for programacion in marcadas:
        foto = auditoria.foto(programacion)
        programacion.no_atendida_por_cierre = True
        programacion.save(update_fields=['no_atendida_por_cierre', 'modificado_en'])
        auditoria.registrar_modificacion(usuario, programacion, foto, motivo=motivo)
    return marcadas


def desmarcar_no_atendidas(jornada, usuario, motivo):
    """Si se anula la Post-Ruta, las programaciones del turno vuelven a quedar pendientes."""
    marcadas = list(Programacion.objects.filter(jornada=jornada, estado=Programacion.Estado.PROGRAMADA,
                                                no_atendida_por_cierre=True))
    for programacion in marcadas:
        foto = auditoria.foto(programacion)
        programacion.no_atendida_por_cierre = False
        programacion.save(update_fields=['no_atendida_por_cierre', 'modificado_en'])
        auditoria.registrar_modificacion(usuario, programacion, foto, motivo=motivo)
    return marcadas


def pendientes_por_averia(transporte_id, ahora, jornada=None):
    """Lo que una avería deja sin atender: las pendientes del turno en curso y todas las de la placa en la fecha
    (incluidas las de sus turnos de hoy, también las de madrugada del turno noche), decisión del 15/10/2026."""
    hoy = ahora.date()
    turnos_de_hoy = Jornada.objects.filter(transporte_id=transporte_id, fecha=hoy)
    condicion = Q(transporte_id=transporte_id, fecha=hoy) | Q(jornada__in=turnos_de_hoy)
    if jornada is not None:
        condicion |= Q(jornada=jornada)
    return (Programacion.objects.filter(condicion, estado=Programacion.Estado.PROGRAMADA, no_atendida_por_cierre=False)
            .select_related('cd').order_by('fecha', 'hora', 'pk').distinct())


def cancelar_por_averia(transporte_id, ahora, incidencia, usuario, jornada=None):
    """Cancela las pendientes por la avería del vehículo (el sistema lo hace aunque ya pasó la hora)."""
    canceladas = list(pendientes_por_averia(transporte_id, ahora, jornada))
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
    return cancelar_por_averia(jornada.transporte_id, ahora, incidencia, usuario, jornada)


def registrar_no_disponibilidad(*, transporte_id, tipo_id, descripcion, usuario, ahora, cd=None):
    """Despacho o Administrador, desde la ficha del transporte (avería en ruta o detectada en el CD).
    Cierra por avería la jornada en curso y cancela las pendientes de ese turno y de los turnos de hoy.
    Devuelve (incidencia, canceladas)."""
    with transaction.atomic():
        transporte = bloquear_transporte(transporte_id)
        incidencia = abrir_incidencia(transporte=transporte, tipo_id=tipo_id, descripcion=descripcion,
                                      usuario=usuario, ahora=ahora, cd=cd)
        jornada = en_curso(transporte.pk, ahora)
        if jornada is None:
            jornada = jornada_vigente(transporte.pk, ahora.date())
        if jornada and jornada.estado in (Estado.PLANIFICADA, Estado.EN_RUTA):
            canceladas = cerrar_jornada_por_averia(jornada, incidencia, usuario, ahora)
        else:
            canceladas = cancelar_por_averia(transporte.pk, ahora, incidencia, usuario)
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
    sin_post_ruta = [j for j in (Jornada.objects.filter(estado=Estado.EN_RUTA, fecha__lt=ahora.date(),
                                                        fecha__gte=ahora.date() - timedelta(days=dias_atras))
                                 .select_related('transporte').order_by('-fecha', 'turno'))
                     if turno_terminado(j, ahora)]
    for jornada in sin_post_ruta:
        turno = " (turno noche)" if jornada.es_noche else ""
        avisos.append(AvisoFlota('fa-flag', f"{jornada.transporte} sin Post-Ruta",
                                 f"Jornada del {jornada.fecha:%d/%m}{turno}: no registró su retorno al CD",
                                 'accesos:tramos_lista',
                                 f"desde={jornada.fecha.isoformat()}&hasta={ahora.date().isoformat()}"
                                 f"&q={jornada.transporte.placa_normalizada}"))
    return avisos
