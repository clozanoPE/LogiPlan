"""
Reglas de negocio que dependen de la hora actual. Reciben `ahora` (datetime consciente de
zona) como parámetro para poder probarlas sin depender del reloj; las vistas les pasan
`tiempo.ahora()`. Referencias R1..R9: documento de diseño aprobado.
"""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q

from . import auditoria, jornadas, tiempo
from .models import (Conductor, Incidencia, Jornada, Programacion, RegistroAuditoria, Tramo, Transporte,
                     normalizar_placa)
from .permisos import ADMINISTRADOR, tiene_rol

Estado = Programacion.Estado

# Estados visibles: los guardados más «Pendiente» / «No atendida», que dependen del día.
PENDIENTE = 'PENDIENTE'
EN_PLANTA = Estado.EN_PLANTA.value
CERRADA = Estado.CERRADA.value
NO_ATENDIDA = 'NO_ATENDIDA'
CANCELADA = Estado.CANCELADA.value

ETIQUETAS_ESTADO = {
    PENDIENTE: 'Pendiente',
    EN_PLANTA: 'En planta',
    CERRADA: 'Cerrada',
    NO_ATENDIDA: 'No atendida',
    CANCELADA: 'Cancelada',
}

KM_MAXIMO = 9_999_999
MENSAJE_ESTADO_CAMBIO = "El estado del vehículo cambió mientras se registraba. Vuelva a escanear la placa."

# Para listas de programaciones: trae el tramo vigente sin una consulta por fila.
PREFETCH_TRAMO_VIGENTE = Prefetch('tramos', queryset=Tramo.objects.filter(vigente=True),
                                  to_attr='tramos_vigentes')


def tramo_vigente(programacion):
    """Tramo válido de la programación (una programación genera un solo tramo), o None."""
    if hasattr(programacion, 'tramos_vigentes'):
        return programacion.tramos_vigentes[0] if programacion.tramos_vigentes else None
    return programacion.tramos.filter(vigente=True).first()


def activas(queryset=None):
    """Programaciones no canceladas (programadas, en planta o cerradas)."""
    return (queryset if queryset is not None else Programacion.objects.all()).exclude(estado=Estado.CANCELADA)


def sincronizar_estado(programacion):
    """Deriva el estado guardado del tramo vigente: sin tramo = Programada, abierto = En planta,
    con salida = Cerrada. Las canceladas no cambian. Se llama tras cada cambio en sus tramos."""
    if programacion is None or programacion.esta_cancelada:
        return programacion
    tramo = programacion.tramos.filter(vigente=True).first()
    nuevo = Estado.PROGRAMADA if tramo is None else (Estado.EN_PLANTA if tramo.esta_abierto else Estado.CERRADA)
    if programacion.estado != nuevo:
        programacion.estado = nuevo
        programacion.save(update_fields=['estado', 'modificado_en'])
    return programacion


def momento(programacion):
    """Fecha y hora programadas como datetime de Lima."""
    return tiempo.combinar(programacion.fecha, programacion.hora)


# --- R1 / R2: horario y edición ---

def validar_horario_futuro(fecha, hora, ahora):
    """R1: no se programa en una fecha y hora que ya pasaron."""
    if tiempo.combinar(fecha, hora) <= ahora:
        raise ValidationError(
            f"No se puede programar para el {fecha:%d/%m/%Y} a las {hora:%H:%M}: esa hora ya pasó "
            f"(ahora son las {ahora:%H:%M} del {ahora:%d/%m/%Y}, hora de Lima)."
        )


def motivo_no_editable(programacion, ahora):
    """R2: devuelve por qué no se puede editar, o None si se puede."""
    if programacion.esta_cancelada:
        return "La programación está cancelada."
    if programacion.estado != Estado.PROGRAMADA:
        return "El vehículo ya registró su ingreso con esta programación."
    if programacion.no_atendida_por_cierre:
        return "Quedó No atendida: la unidad cerró su jornada (Post-Ruta) sin atenderla."
    if momento(programacion) <= ahora:
        return "La hora programada ya pasó; si el vehículo aún debe ingresar, registre una nueva programación."
    return None


def validar_placa_sin_duplicar(programacion):
    """La misma placa no puede tener dos programaciones activas el mismo día a la misma hora."""
    repetida = Programacion.objects.filter(
        transporte_id=programacion.transporte_id, fecha=programacion.fecha, hora=programacion.hora,
    ).exclude(estado=Estado.CANCELADA).exclude(pk=programacion.pk)
    if repetida.exists():
        raise ValidationError(
            f"La placa ya tiene la programación {repetida.first().codigo} ese día a las {programacion.hora:%H:%M}."
        )


# --- R3: cancelación ---

def motivo_no_cancelable(programacion, usuario, ahora):
    """R3: Despacho cancela hasta la hora programada; después, solo el Administrador."""
    if programacion.esta_cancelada:
        return "La programación ya está cancelada."
    if programacion.estado != Estado.PROGRAMADA:
        return "Ya tiene un ingreso registrado; para corregirlo, el Administrador debe anular el tramo."
    if programacion.no_atendida_por_cierre:
        return "Quedó No atendida: la unidad cerró su jornada (Post-Ruta) sin atenderla."
    if momento(programacion) <= ahora and not tiene_rol(usuario, ADMINISTRADOR):
        return "La hora programada ya pasó: solo el Administrador puede cancelarla."
    return None


def cancelar(programacion, usuario, motivo, ahora):
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indique el motivo de la cancelación.")
    impedimento = motivo_no_cancelable(programacion, usuario, ahora)
    if impedimento:
        raise ValidationError(impedimento)
    with transaction.atomic():
        foto_antes = auditoria.foto(programacion)
        programacion.estado = Programacion.Estado.CANCELADA
        programacion.cancelado_por = usuario
        programacion.cancelado_en = ahora
        programacion.motivo_cancelacion = motivo
        programacion.modificado_por = usuario
        programacion.save()
        auditoria.registrar_modificacion(usuario, programacion, foto_antes,
                                         accion=RegistroAuditoria.Accion.CANCELAR, motivo=motivo)
    return programacion


# --- R4: estado visible ---

def estado_visible(programacion, hoy):
    """R4: el estado guardado; una programada se muestra Pendiente o, si su día ya terminó (o la unidad
    cerró su jornada sin atenderla), No atendida."""
    if programacion.estado != Estado.PROGRAMADA:
        return programacion.estado
    return NO_ATENDIDA if programacion.fecha < hoy or programacion.no_atendida_por_cierre else PENDIENTE


def filtrar_por_estado_visible(queryset, codigo, hoy):
    """Filtra en la base de datos por un estado visible (para las consultas con filtro de estado)."""
    if codigo == PENDIENTE:
        return queryset.filter(estado=Estado.PROGRAMADA, fecha__gte=hoy, no_atendida_por_cierre=False)
    if codigo == NO_ATENDIDA:
        return queryset.filter(Q(fecha__lt=hoy) | Q(no_atendida_por_cierre=True), estado=Estado.PROGRAMADA)
    if codigo in (EN_PLANTA, CERRADA, CANCELADA):
        return queryset.filter(estado=codigo)
    return queryset


# --- R5 / R6 / R7: avisos (no bloquean) ---

def _minutos(hora):
    return hora.hour * 60 + hora.minute


def avisos(programacion, config):
    """Advertencias que se muestran después de guardar; nunca impiden guardar."""
    mensajes = []
    activas_del_dia = (activas().filter(fecha=programacion.fecha).exclude(pk=programacion.pk)
                       .select_related('cd', 'transporte'))
    minuto = _minutos(programacion.hora)

    # R5: capacidad del CD con una duración estimada de atención. También ocupan el CD las unidades propias
    # que a esa hora siguen en su Pre-Ruta o ya volvieron a pernoctar (después del cierre operativo).
    duracion = config.duracion_estimada_atencion_min
    cercanas = [p for p in activas_del_dia
                if p.cd_id == programacion.cd_id and abs(_minutos(p.hora) - minuto) < duracion]
    flota = [j for j in (Jornada.objects.filter(fecha=programacion.fecha, cd_id=programacion.cd_id)
                         .exclude(estado=Jornada.Estado.ANULADA).exclude(transporte_id=programacion.transporte_id))
             if programacion.hora < jornadas.limite_pre_ruta(j).time() or programacion.hora >= j.hora_cierre]
    capacidad = programacion.cd.capacidad_simultanea
    total = len(cercanas) + 1 + len(flota)
    if total > capacidad:
        detalle = f", incluidas {len(flota)} unidades propias en Pre-Ruta o pernocta" if flota else ""
        mensajes.append(
            f"Capacidad: hay {total} vehículos en {programacion.cd} a menos de {duracion} min de las "
            f"{programacion.hora:%H:%M}{detalle} (capacidad referencial: {capacidad})."
        )

    # R6: la misma placa programada cerca de ese horario (en cualquier CD).
    ventana = config.ventana_sugerencia_min
    for otra in activas_del_dia:
        if otra.transporte_id == programacion.transporte_id and abs(_minutos(otra.hora) - minuto) < ventana:
            mensajes.append(
                f"La placa {programacion.transporte} también está programada a las {otra.hora:%H:%M} "
                f"en {otra.cd} ({otra.codigo})."
            )

    # R7 (bloqueante desde F6) se valida en el formulario. Aviso: una ruta INTERNO durante la Pre-Ruta.
    if programacion.tipo == Programacion.Tipo.INTERNO:
        jornada = jornadas.jornada_vigente(programacion.transporte_id, programacion.fecha)
        if jornada and programacion.hora < jornadas.limite_pre_ruta(jornada).time():
            mensajes.append(
                f"A las {programacion.hora:%H:%M} la unidad estará en su Pre-Ruta (desde las {jornada.hora_inicio:%H:%M}): "
                "esta programación se atenderá en su primer retorno al CD.")
    return mensajes


# --- PORTERÍA (F3): R10–R17, con la jornada de la flota propia (F6) ---

ENTRADA = 'ENTRADA'
SALIDA = 'SALIDA'
NO_PROGRAMADO = 'NO_PROGRAMADO'
OTRO_CD = 'OTRO_CD'
INICIO_JORNADA = 'INICIO_JORNADA'  # unidad propia que entra al CD sin haber dormido en él
POST_RUTA = 'POST_RUTA'            # entrada de fin de jornada (ya no le quedan programaciones)

# Tipos de salida (ResultadoPorteria.tipo_salida).
SALIDA_NORMAL = 'NORMAL'
SALIDA_PRE_RUTA = 'PRE_RUTA'                  # cierra su Pre-Ruta (había entrado desde afuera)
SALIDA_PRE_RUTA_AUTOMATICA = 'PRE_RUTA_AUTO'  # durmió en el CD: se graba la Pre-Ruta desde las 06:30
SALIDA_SIN_PRE_RUTA = 'SIN_PRE_RUTA'          # su primera salida cierra una visita abierta desde otro día
SALIDA_TRAS_CIERRE = 'TRAS_CIERRE'            # sale después de su Post-Ruta sin jornada: pide motivo
SALIDA_NO_DISPONIBLE = 'NO_DISPONIBLE'        # vehículo en avería (p. ej. al taller): pide motivo
SALIDAS_CON_MOTIVO = (SALIDA_TRAS_CIERRE, SALIDA_NO_DISPONIBLE)


@dataclass
class Candidata:
    programacion: Programacion
    minutos: int  # hora programada menos hora actual (negativo = la hora ya pasó)
    en_ventana: bool


@dataclass
class ResultadoPorteria:
    accion: str
    mensaje: str = ''
    transporte: Transporte = None
    tramo_abierto: Tramo = None
    candidatas: list = field(default_factory=list)
    jornada: Jornada = None
    tipo_salida: str = SALIDA_NORMAL
    pendientes: list = field(default_factory=list)  # pendientes del día en cualquier CD (Post-Ruta)
    permite_post_ruta: bool = False                 # ENTRADA: también puede cerrar la jornada
    permite_averia: bool = False                    # ENTRADA / POST_RUTA: ingreso por avería
    reingreso: bool = False                         # POST_RUTA con la jornada ya cerrada

    @property
    def sugerida(self):
        """R14: la candidata más cercana dentro de la ventana; si ninguna lo está, no hay sugerida."""
        return next((c for c in self.candidatas if c.en_ventana), None)

    @property
    def salida_pide_motivo(self):
        return self.tipo_salida in SALIDAS_CON_MOTIVO


def _minutos_entre(desde, hasta):
    return round((hasta - desde).total_seconds() / 60)


def candidatas_entrada(transporte, cd, ahora, ventana_min):
    """R13: programaciones de hoy de esa placa en ese CD, activas y sin tramo válido, por cercanía."""
    programaciones = (Programacion.objects
                      .filter(transporte=transporte, cd=cd, fecha=ahora.date(), estado=Programacion.Estado.PROGRAMADA,
                              no_atendida_por_cierre=False)
                      .exclude(tramos__vigente=True)
                      .select_related('conductor__tipo_documento', 'cd'))
    candidatas = []
    for programacion in programaciones:
        minutos = _minutos_entre(ahora, momento(programacion))
        candidatas.append(Candidata(programacion, minutos, abs(minutos) <= ventana_min))
    return sorted(candidatas, key=lambda c: abs(c.minutos))


def tipo_salida(tramo, jornada, no_disponible):
    """Qué significa la salida del tramo abierto según la jornada de hoy de la placa."""
    if no_disponible:
        return SALIDA_NO_DISPONIBLE
    if jornada is not None and jornada.estado == Jornada.Estado.PLANIFICADA:
        if tramo.clase == Tramo.Clase.PRE_RUTA and tramo.jornada_id == jornada.pk:
            return SALIDA_PRE_RUTA
        if tramo.clase == Tramo.Clase.POST_RUTA:
            return SALIDA_PRE_RUTA_AUTOMATICA
        if tramo.clase == Tramo.Clase.PROGRAMADO and tramo.fecha_local_entrada < jornada.fecha:
            return SALIDA_SIN_PRE_RUTA
    if tramo.clase == Tramo.Clase.POST_RUTA:
        return SALIDA_TRAS_CIERRE
    return SALIDA_NORMAL


def evaluar_porteria(texto, cd, ahora, config):
    """R10–R16: con la placa (digitada o leída del QR) decide ENTRADA, SALIDA, el inicio o el fin de la
    jornada de una unidad propia, o el bloqueo."""
    placa = normalizar_placa(texto)
    transporte = Transporte.objects.select_related('tamano').filter(placa_normalizada=placa).first() if placa else None
    if transporte is None:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. (La placa {texto.strip().upper()} no está registrada.)")

    hoy = ahora.date()
    jornada = jornadas.jornada_vigente(transporte.pk, hoy)
    incidencia = jornadas.incidencia_abierta(transporte.pk)
    abierto = (Tramo.objects.filter(transporte=transporte, abierto=True)
               .select_related('cd', 'programacion', 'conductor').first())
    if abierto and abierto.cd_id != cd.pk:
        return ResultadoPorteria(
            OTRO_CD, f"{transporte} figura DENTRO de CD {abierto.cd} desde el "
                     f"{tiempo.a_lima(abierto.entrada_en):%d/%m/%Y %H:%M}. No se puede registrar. "
                     "Comuníquese con el Administrador.",
            transporte=transporte, tramo_abierto=abierto)
    if abierto:
        return ResultadoPorteria(SALIDA, transporte=transporte, tramo_abierto=abierto, jornada=jornada,
                                 tipo_salida=tipo_salida(abierto, jornada, incidencia is not None))

    if not transporte.activo:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. ({transporte} está inactivo.)", transporte=transporte)
    if jornada is not None and jornada.estado == Jornada.Estado.CERRADA:
        # Ya cerró su jornada y vuelve (p. ej. salió después de la Post-Ruta): ingresa a pernoctar.
        return ResultadoPorteria(POST_RUTA, transporte=transporte, jornada=jornada, reingreso=True)
    if incidencia is not None:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no disponible por avería: {jornadas.texto_no_disponible(incidencia)} "
                           "Despacho debe habilitarlo antes de registrar su ingreso.", transporte=transporte)
    if jornada is not None and jornada.estado == Jornada.Estado.PLANIFICADA:
        return ResultadoPorteria(INICIO_JORNADA, transporte=transporte, jornada=jornada)

    candidatas = candidatas_entrada(transporte, cd, ahora, config.ventana_sugerencia_min)
    if jornada is not None:  # en ruta
        pendientes_dia = list(jornadas.pendientes(transporte.pk, hoy))
        if candidatas:
            return ResultadoPorteria(ENTRADA, transporte=transporte, candidatas=candidatas, jornada=jornada,
                                     pendientes=pendientes_dia, permite_post_ruta=True, permite_averia=True)
        return ResultadoPorteria(POST_RUTA, transporte=transporte, jornada=jornada, pendientes=pendientes_dia,
                                 permite_averia=True)
    if not candidatas:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. ({transporte} no tiene programaciones "
                           f"pendientes hoy en CD {cd}.)", transporte=transporte)
    return ResultadoPorteria(ENTRADA, transporte=transporte, candidatas=candidatas)


_bloquear_transporte = jornadas.bloquear_transporte


def _miles(numero):
    return f"{numero:,}".replace(',', '.')


def _validar_km(km):
    if km is None or not (0 <= km <= KM_MAXIMO):
        raise ValidationError(f"Ingrese el kilometraje (entre 0 y {_miles(KM_MAXIMO)}).")


def _ultimo_km(transporte):
    """Último kilometraje registrado de la placa (para detectar uno menor)."""
    return (Tramo.objects.filter(transporte=transporte, vigente=True, km_entrada__isnull=False)
            .order_by('-entrada_en').values_list('km_entrada', flat=True).first())


def _revisar_km(transporte, km, avisos):
    ultimo = _ultimo_km(transporte)
    if ultimo is not None and km < ultimo:
        avisos.append(f"El kilometraje ({_miles(km)}) es menor al último registrado ({_miles(ultimo)}). "
                      "Quedó marcado para revisión.")
        return True
    return False


def _conductor_elegido(conductor_id, por_defecto):
    if conductor_id and conductor_id != por_defecto.pk:
        conductor = Conductor.objects.filter(pk=conductor_id, activo=True).first()
        if conductor is None:
            raise ValidationError("El conductor elegido no existe o está inactivo.")
        return conductor
    return por_defecto


def _sin_tramo_abierto(transporte):
    if Tramo.objects.filter(transporte=transporte, abierto=True).exists():
        raise ValidationError(MENSAJE_ESTADO_CAMBIO)


def _momento_local(momento_):
    lima = tiempo.a_lima(momento_)
    return lima.date(), lima.time().replace(microsecond=0)


def _nuevo_tramo(*, clase, transporte, cd, conductor, entrada, km, obs, usuario, tipo, programacion=None,
                 jornada=None, desviacion=None, **marcas):
    fecha, hora = _momento_local(entrada)
    return Tramo(clase=clase, programacion=programacion, jornada=jornada, transporte=transporte, cd=cd,
                 conductor=conductor, entrada_en=entrada, fecha_local_entrada=fecha, hora_local_entrada=hora,
                 km_entrada=km, obs_entrada=(obs or '').strip() or 'Ninguna', vigilante_entrada=usuario,
                 desviacion_llegada_min=desviacion, tamano_snapshot=transporte.tamano, tipo_snapshot=tipo,
                 dia_no_laborable=not jornadas.es_laborable(fecha), **marcas)


def _guardar_tramo_nuevo(tramo):
    try:
        with transaction.atomic():
            tramo.save()
    except IntegrityError:
        raise ValidationError(MENSAJE_ESTADO_CAMBIO)


def registrar_entrada(*, transporte_id, programacion_id, cd, km, obs, conductor_id, usuario, ahora, config,
                      confirmar_fuera_de_ventana=False):
    """R12–R15. Devuelve (tramo, avisos). Lanza ValidationError si algo impide la entrada."""
    _validar_km(km)
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        # Todo se vuelve a evaluar dentro del bloqueo: otra tablet pudo registrar mientras tanto.
        _sin_tramo_abierto(transporte)
        jornada = jornadas.jornada_vigente(transporte.pk, ahora.date())
        if jornada is not None and jornada.estado == Jornada.Estado.PLANIFICADA:
            raise ValidationError(MENSAJE_ESTADO_CAMBIO)  # primero debe iniciar su jornada
        if jornadas.incidencia_abierta(transporte.pk):
            raise ValidationError("El vehículo no está disponible (avería). Despacho debe habilitarlo.")
        candidatas = candidatas_entrada(transporte, cd, ahora, config.ventana_sugerencia_min)
        candidata = next((c for c in candidatas if c.programacion.pk == programacion_id), None)
        if candidata is None:
            raise ValidationError("La programación elegida ya no está disponible. Vuelva a escanear la placa.")
        if not candidata.en_ventana and not confirmar_fuera_de_ventana:
            raise ValidationError(f"La programación elegida está fuera de la ventana de ±{config.ventana_sugerencia_min} "
                                  "min: confírmela para registrar la entrada.", code='confirmar_fuera_de_ventana')
        programacion = candidata.programacion
        conductor = _conductor_elegido(conductor_id, programacion.conductor)

        avisos = []
        tramo = _nuevo_tramo(
            clase=Tramo.Clase.PROGRAMADO, programacion=programacion, jornada=jornada, transporte=transporte, cd=cd,
            conductor=conductor, entrada=ahora, km=km, obs=obs, usuario=usuario, tipo=programacion.tipo,
            desviacion=-candidata.minutos, fuera_de_ventana=not candidata.en_ventana,
            km_inconsistente=_revisar_km(transporte, km, avisos))
        _guardar_tramo_nuevo(tramo)
        sincronizar_estado(programacion)  # -> En planta
        auditoria.registrar(usuario, RegistroAuditoria.Accion.ENTRADA, tramo, despues=auditoria.foto(tramo), cd=cd)
    return tramo, avisos


def _jornada_para_registrar(transporte, ahora, *estados):
    jornada = jornadas.jornada_vigente(transporte.pk, ahora.date())
    if jornada is None or jornada.estado not in estados:
        raise ValidationError(MENSAJE_ESTADO_CAMBIO)
    return jornada


def _actualizar_jornada(jornada, usuario, motivo, **cambios):
    foto = auditoria.foto(jornada)
    for campo, valor in cambios.items():
        setattr(jornada, campo, valor)
    jornada.save()
    auditoria.registrar_modificacion(usuario, jornada, foto, motivo=motivo)


def registrar_inicio_jornada(*, transporte_id, cd, km, obs, conductor_id, usuario, ahora):
    """Unidad propia con jornada que ENTRA al CD (no durmió en él): su Pre-Ruta empieza ahora."""
    _validar_km(km)
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        _sin_tramo_abierto(transporte)
        jornada = _jornada_para_registrar(transporte, ahora, Jornada.Estado.PLANIFICADA)
        if jornadas.incidencia_abierta(transporte.pk):
            raise ValidationError("El vehículo no está disponible (avería). Despacho debe habilitarlo.")
        avisos = []
        tramo = _nuevo_tramo(
            clase=Tramo.Clase.PRE_RUTA, jornada=jornada, transporte=transporte, cd=cd,
            conductor=_conductor_elegido(conductor_id, jornada.conductor), entrada=ahora, km=km, obs=obs,
            usuario=usuario, tipo=Programacion.Tipo.INTERNO, km_inconsistente=_revisar_km(transporte, km, avisos))
        _guardar_tramo_nuevo(tramo)
        auditoria.registrar(usuario, RegistroAuditoria.Accion.ENTRADA, tramo, despues=auditoria.foto(tramo), cd=cd,
                            motivo="Inicio de jornada: la unidad ingresa al CD (Pre-Ruta).")
        _actualizar_jornada(jornada, usuario, "Inicio de jornada desde afuera del CD.",
                            inicio_real=ahora, ingreso_desde_afuera=True, cd=cd)
    return tramo, avisos


def _pre_ruta_cerrada(*, jornada, transporte, cd, desde, ahora, km, usuario, obs_entrada, obs_salida):
    """Graba la Pre-Ruta ya cerrada: desde `desde` (06:30 o la salida si fue antes) hasta la salida."""
    tramo = _nuevo_tramo(clase=Tramo.Clase.PRE_RUTA, jornada=jornada, transporte=transporte, cd=cd,
                         conductor=jornada.conductor, entrada=desde, km=km, obs=obs_entrada, usuario=None,
                         tipo=Programacion.Tipo.INTERNO)
    _cerrar(tramo, salida=ahora, obs=obs_salida, usuario=usuario)
    _guardar_tramo_nuevo(tramo)
    auditoria.registrar(usuario, RegistroAuditoria.Accion.SALIDA, tramo, despues=auditoria.foto(tramo), cd=cd,
                        motivo=f"Salida a ruta: Pre-Ruta desde las {tiempo.a_lima(desde):%H:%M}.")
    anticipada = ahora < jornadas.inicio(jornada)
    _actualizar_jornada(jornada, usuario, "Salida a ruta (fin de la Pre-Ruta).", estado=Jornada.Estado.EN_RUTA,
                        inicio_real=desde, primera_salida=ahora, salida_anticipada=anticipada, cd=cd)
    return tramo


def registrar_salida(*, transporte_id, tramo_id, cd, obs, usuario, ahora):
    """R12: cierra el tramo abierto de la placa en este CD. Si es la primera salida del día de una unidad
    propia que durmió en el CD, cierra la pernocta a la hora de la Pre-Ruta y graba la Pre-Ruta.
    Devuelve el tramo que quedó registrado con la salida."""
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        tramo = Tramo.objects.filter(pk=tramo_id, transporte=transporte, abierto=True).select_related('jornada').first()
        if tramo is None:
            raise ValidationError(MENSAJE_ESTADO_CAMBIO)
        if tramo.cd_id != cd.pk:
            raise ValidationError(f"El vehículo figura dentro de CD {tramo.cd}; no se puede registrar su salida aquí.")
        jornada = jornadas.jornada_vigente(transporte.pk, ahora.date())
        tipo = tipo_salida(tramo, jornada, jornadas.incidencia_abierta(transporte.pk) is not None)
        if tipo in SALIDAS_CON_MOTIVO and not (obs or '').strip():
            raise ValidationError("Indique el motivo de la salida (la unidad ya cerró su jornada o no está disponible).",
                                  code='motivo_salida')

        if tipo == SALIDA_PRE_RUTA_AUTOMATICA:
            # Durmió en el CD: la pernocta termina a la hora de la Pre-Ruta (o ahora, si sale antes).
            desde = min(ahora, max(jornadas.inicio(jornada), tramo.entrada_en))
            foto_antes = auditoria.foto(tramo)
            _cerrar(tramo, salida=desde, obs="Fin de la pernocta: inicio de la Pre-Ruta", usuario=None)
            tramo.cierre_automatico = True
            tramo.save()
            auditoria.registrar_modificacion(None, tramo, foto_antes, accion=RegistroAuditoria.Accion.SALIDA,
                                             motivo="Cierre automático por inicio de la Pre-Ruta.")
            return _pre_ruta_cerrada(jornada=jornada, transporte=transporte, cd=cd, desde=desde, ahora=ahora,
                                     km=tramo.km_entrada, usuario=usuario, obs_entrada='Pre-Ruta automática',
                                     obs_salida=obs)

        foto_antes = auditoria.foto(tramo)
        _cerrar(tramo, salida=ahora, obs=obs, usuario=usuario)
        tramo.dia_no_laborable = tramo.dia_no_laborable or not jornadas.es_laborable(ahora.date())
        tramo.salida_tras_cierre = tipo == SALIDA_TRAS_CIERRE
        tramo.save()
        sincronizar_estado(tramo.programacion)  # -> Cerrada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.SALIDA)
        if tipo == SALIDA_PRE_RUTA:
            _actualizar_jornada(jornada, usuario, "Salida a ruta (fin de la Pre-Ruta).",
                                estado=Jornada.Estado.EN_RUTA, primera_salida=ahora)
        elif tipo == SALIDA_SIN_PRE_RUTA:
            _actualizar_jornada(jornada, usuario, "Salida a ruta cerrando una visita abierta desde otro día.",
                                estado=Jornada.Estado.EN_RUTA, primera_salida=ahora, salida_sin_pre_ruta=True)
        elif tipo == SALIDA_TRAS_CIERRE and tramo.jornada_id:
            _actualizar_jornada(tramo.jornada, usuario, "Salió del CD después de su Post-Ruta.", pernocto_fuera=True)
    return tramo


def registrar_salida_sin_registro(*, transporte_id, cd, obs, usuario, ahora):
    """Unidad propia con jornada que SALE sin tener un tramo abierto (durmió en el CD pero su ingreso no
    quedó registrado, p. ej. el primer día de uso): se graba su Pre-Ruta hasta esta salida."""
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        _sin_tramo_abierto(transporte)
        jornada = _jornada_para_registrar(transporte, ahora, Jornada.Estado.PLANIFICADA)
        if jornadas.incidencia_abierta(transporte.pk):
            raise ValidationError("El vehículo no está disponible (avería). Despacho debe habilitarlo.")
        desde = min(ahora, jornadas.inicio(jornada))
        return _pre_ruta_cerrada(jornada=jornada, transporte=transporte, cd=cd, desde=desde, ahora=ahora,
                                 km=_ultimo_km(transporte), usuario=usuario,
                                 obs_entrada='Pre-Ruta automática (sin registro de ingreso previo)', obs_salida=obs)


def registrar_post_ruta(*, transporte_id, cd, km, obs, conductor_id, usuario, ahora, averia_tipo_id=None,
                        averia_descripcion=''):
    """Entrada de fin de jornada de una unidad propia (Post-Ruta). Las programaciones pendientes del día
    quedan «No atendida por cierre de jornada»; con avería, se cancelan, se abre la incidencia y el
    vehículo queda no disponible. Devuelve (tramo, avisos)."""
    _validar_km(km)
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        _sin_tramo_abierto(transporte)
        jornada = _jornada_para_registrar(transporte, ahora, Jornada.Estado.EN_RUTA, Jornada.Estado.CERRADA)
        reingreso = jornada.estado == Jornada.Estado.CERRADA
        ultimo = (Tramo.objects.filter(jornada=jornada, vigente=True).select_related('conductor')
                  .order_by('-entrada_en').first())
        conductor = _conductor_elegido(conductor_id, ultimo.conductor if ultimo else jornada.conductor)
        avisos = []
        incidencia = None
        if averia_tipo_id:
            incidencia = jornadas.abrir_incidencia(transporte=transporte, tipo_id=averia_tipo_id,
                                                   descripcion=averia_descripcion, usuario=usuario, ahora=ahora,
                                                   cd=cd, origen=Incidencia.Origen.PORTERIA)
        tramo = _nuevo_tramo(clase=Tramo.Clase.POST_RUTA, jornada=jornada, transporte=transporte, cd=cd,
                             conductor=conductor, entrada=ahora, km=km, obs=obs, usuario=usuario,
                             tipo=Programacion.Tipo.INTERNO, km_inconsistente=_revisar_km(transporte, km, avisos))
        _guardar_tramo_nuevo(tramo)
        if incidencia:
            motivo = "Ingreso por avería (Post-Ruta)."
        elif reingreso:
            motivo = "Reingreso al CD después de cerrar la jornada."
        else:
            motivo = "Post-Ruta: fin de jornada."
        auditoria.registrar(usuario, RegistroAuditoria.Accion.ENTRADA, tramo, despues=auditoria.foto(tramo), cd=cd,
                            motivo=motivo)
        if not reingreso:
            _actualizar_jornada(jornada, usuario, motivo, estado=Jornada.Estado.CERRADA, post_ruta_en=ahora,
                                incidencia=incidencia,
                                motivo_cierre=Jornada.MotivoCierre.AVERIA if incidencia else Jornada.MotivoCierre.NORMAL)
        if incidencia:
            canceladas = jornadas.cancelar_por_averia(transporte.pk, ahora.date(), incidencia, usuario, ahora)
            if canceladas:
                avisos.append(f"{len(canceladas)} programación(es) pendiente(s) de hoy se cancelaron por la avería.")
            futuras = jornadas.programaciones_afectadas(ahora.date(), [transporte.pk]).count()
            if futuras:
                avisos.append(f"Hay {futuras} programación(es) de días siguientes con esta placa: Despacho debe "
                              "cambiarles la placa o cancelarlas.")
        elif not reingreso:
            marcadas = jornadas.marcar_no_atendidas(transporte.pk, ahora.date(), usuario,
                                                    "No atendida: la unidad cerró su jornada (Post-Ruta).")
            if marcadas:
                avisos.append(f"{len(marcadas)} programación(es) pendiente(s) quedaron como No atendidas por cierre "
                              "de jornada.")
    return tramo, avisos


def _cerrar(tramo, *, salida, obs, usuario):
    tramo.salida_en = salida
    tramo.fecha_local_salida = tiempo.a_lima(salida).date()
    tramo.obs_salida = (obs or '').strip() or 'Ninguna'
    tramo.vigilante_salida = usuario
    tramo.abierto = None
    tramo.minutos_permanencia = max(0, int((salida - tramo.entrada_en).total_seconds() // 60))


def minutos_dentro(tramo, ahora):
    """Permanencia en minutos: la registrada si salió, o la transcurrida hasta ahora si sigue dentro."""
    if tramo.salida_en:
        return tramo.minutos_permanencia
    return max(0, int((ahora - tramo.entrada_en).total_seconds() // 60))


def es_prolongada(tramo, ahora, config):
    """R19: más que el umbral (240 min por defecto). Es solo un indicador, no reclasifica nada."""
    return minutos_dentro(tramo, ahora) > config.umbral_prolongada_min


def numero_tramo_del_dia(tramo):
    """1.º, 2.º, 3.º… tramo válido de esa placa en esa fecha, por hora de entrada (no se guarda)."""
    return Tramo.objects.filter(transporte_id=tramo.transporte_id, fecha_local_entrada=tramo.fecha_local_entrada,
                                vigente=True, entrada_en__lte=tramo.entrada_en).count()


PUNTUAL = 'PUNTUAL'
ADELANTADO = 'ADELANTADO'
RETRASADO = 'RETRASADO'


def puntualidad(desviacion_min, tolerancia_min):
    """R21: ADELANTADO, PUNTUAL o RETRASADO según la tolerancia vigente (None si el tramo no tiene hora
    programada: Pre-Ruta y Post-Ruta)."""
    if desviacion_min is None:
        return None
    if desviacion_min < -tolerancia_min:
        return ADELANTADO
    if desviacion_min > tolerancia_min:
        return RETRASADO
    return PUNTUAL


def formato_duracion(minutos):
    if minutos is None:
        return '—'
    horas, resto = divmod(int(minutos), 60)
    return f"{horas} h {resto:02d} min" if horas else f"{resto} min"


# --- CORRECCIONES DEL ADMINISTRADOR (F4): R24–R27, y de la jornada (F6) ---

def _exigir_motivo(motivo):
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indique el motivo; queda registrado en la auditoría.")
    return motivo


def _recalcular(tramo):
    """Actualiza los datos copiados del tramo después de una corrección."""
    entrada_lima = tiempo.a_lima(tramo.entrada_en)
    tramo.fecha_local_entrada = entrada_lima.date()
    tramo.hora_local_entrada = entrada_lima.time().replace(microsecond=0)
    if tramo.programacion_id:
        tramo.desviacion_llegada_min = _minutos_entre(momento(tramo.programacion), tramo.entrada_en)
        tramo.tipo_snapshot = tramo.programacion.tipo
    if tramo.salida_en:
        tramo.fecha_local_salida = tiempo.a_lima(tramo.salida_en).date()
        tramo.minutos_permanencia = max(0, int((tramo.salida_en - tramo.entrada_en).total_seconds() // 60))


def _sincronizar_jornada_con_tramo(tramo, usuario, motivo):
    """Tras corregir un tramo de la jornada, sus horas pasan a la jornada (inicio, salida, Post-Ruta)."""
    jornada = tramo.jornada
    if jornada is None:
        return
    if tramo.clase == Tramo.Clase.PRE_RUTA:
        cambios = {'inicio_real': tramo.entrada_en, 'primera_salida': tramo.salida_en}
    elif tramo.clase == Tramo.Clase.POST_RUTA and jornada.estado == Jornada.Estado.CERRADA and jornada.post_ruta_en:
        primera_post = (Tramo.objects.filter(jornada=jornada, clase=Tramo.Clase.POST_RUTA, vigente=True)
                        .order_by('entrada_en').first())
        if primera_post is None or primera_post.pk != tramo.pk:
            return
        cambios = {'post_ruta_en': tramo.entrada_en}
    else:
        return
    _actualizar_jornada(jornada, usuario, motivo, **cambios)


def programaciones_para_corregir(tramo):
    """Programaciones a las que se puede reasignar el tramo: misma placa, activas y sin otro tramo válido."""
    ocupadas = Tramo.objects.filter(vigente=True).exclude(pk=tramo.pk).values('programacion_id')
    return (Programacion.objects.filter(Q(estado=Estado.PROGRAMADA) | Q(pk=tramo.programacion_id),
                                        transporte_id=tramo.transporte_id)
            .exclude(pk__in=ocupadas).select_related('cd').order_by('-fecha', '-hora'))


def corregir_tramo(tramo, usuario, *, motivo, ahora, entrada_en, salida_en, km_entrada, obs_entrada, obs_salida,
                   programacion, conductor):
    """R25: corrige un tramo válido; recalcula permanencia, desviación y fechas locales. Los tramos de la
    jornada (Pre-Ruta y Post-Ruta) no tienen programación: `programacion` se ignora y sus horas
    actualizan la jornada."""
    motivo = _exigir_motivo(motivo)
    de_jornada = tramo.es_de_jornada
    if not tramo.vigente:
        raise ValidationError("El tramo está anulado; no se puede corregir.")
    if entrada_en > ahora:
        raise ValidationError("La hora de entrada no puede ser futura.")
    if tramo.salida_en and salida_en is None:
        raise ValidationError("Indique la hora de salida (el tramo ya está cerrado).")
    if salida_en is not None:
        if not tramo.salida_en:
            raise ValidationError("El tramo sigue abierto: use el cierre administrativo para registrar su salida.")
        if salida_en < entrada_en:
            raise ValidationError("La salida no puede ser anterior a la entrada.")
        if salida_en > ahora:
            raise ValidationError("La hora de salida no puede ser futura.")
    if not de_jornada and (programacion is None or programacion.transporte_id != tramo.transporte_id
                           or programacion.esta_cancelada
                           or (programacion.pk != tramo.programacion_id and programacion.estado != Estado.PROGRAMADA)):
        raise ValidationError("La programación elegida no corresponde a esta placa o está cancelada.")
    if km_entrada is None and not de_jornada:
        raise ValidationError("Indique el kilometraje.")
    if km_entrada is not None and not (0 <= km_entrada <= KM_MAXIMO):
        raise ValidationError("Kilometraje fuera de rango.")

    with transaction.atomic():
        _bloquear_transporte(tramo.transporte_id)
        foto_antes = auditoria.foto(tramo)
        programacion_anterior = tramo.programacion
        tramo.entrada_en = entrada_en
        tramo.salida_en = salida_en
        tramo.km_entrada = km_entrada
        tramo.obs_entrada = (obs_entrada or '').strip() or 'Ninguna'
        if tramo.salida_en:
            tramo.obs_salida = (obs_salida or '').strip() or 'Ninguna'
        if not de_jornada:
            tramo.programacion = programacion
            tramo.cd_id = programacion.cd_id
        tramo.conductor = conductor
        tramo.corregido = True
        _recalcular(tramo)
        try:
            with transaction.atomic():
                tramo.save()
        except IntegrityError:
            raise ValidationError("Esa programación ya tiene otro tramo válido.")
        if not de_jornada:
            sincronizar_estado(programacion)
            if programacion_anterior.pk != programacion.pk:
                sincronizar_estado(programacion_anterior)  # vuelve a quedar Programada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.CORREGIR,
                                         motivo=motivo)
        _sincronizar_jornada_con_tramo(tramo, usuario, f"Corrección del tramo: {motivo}")
    return tramo


def anular_tramo(tramo, usuario, *, motivo):
    """R24: el tramo deja de contar (no se borra) y su programación vuelve a quedar pendiente. Si es la
    Post-Ruta de la jornada, la jornada vuelve a quedar en ruta y sus pendientes vuelven a estar
    pendientes; si es la Pre-Ruta, la jornada vuelve a quedar planificada."""
    motivo = _exigir_motivo(motivo)
    if not tramo.vigente:
        raise ValidationError("El tramo ya está anulado.")
    with transaction.atomic():
        _bloquear_transporte(tramo.transporte_id)
        foto_antes = auditoria.foto(tramo)
        tramo.vigente = None
        tramo.abierto = None
        tramo.save()
        sincronizar_estado(tramo.programacion)  # vuelve a quedar Programada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.ANULAR,
                                         motivo=motivo)
        jornada = tramo.jornada
        if jornada is not None:
            texto = f"Se anuló su tramo de {tramo.get_clase_display()}: {motivo}"
            otros = Tramo.objects.filter(jornada=jornada, vigente=True)
            if (tramo.clase == Tramo.Clase.POST_RUTA and jornada.estado == Jornada.Estado.CERRADA
                    and not otros.filter(clase=Tramo.Clase.POST_RUTA).exists()):
                if jornada.motivo_cierre == Jornada.MotivoCierre.AVERIA:
                    _actualizar_jornada(jornada, usuario, texto, post_ruta_en=None)
                else:
                    _actualizar_jornada(jornada, usuario, texto, estado=Jornada.Estado.EN_RUTA, post_ruta_en=None,
                                        motivo_cierre='')
                    jornadas.desmarcar_no_atendidas(jornada.transporte_id, jornada.fecha, usuario,
                                                    "Vuelve a estar pendiente: se anuló la Post-Ruta.")
            elif tramo.clase == Tramo.Clase.PRE_RUTA and not otros.exists():
                _actualizar_jornada(jornada, usuario, texto, estado=Jornada.Estado.PLANIFICADA, inicio_real=None,
                                    primera_salida=None, ingreso_desde_afuera=False, salida_anticipada=False)
    return tramo


def cierre_administrativo(tramo, usuario, *, salida_en, motivo, ahora):
    """R26: registra la salida olvidada de un tramo abierto, con hora estimada."""
    motivo = _exigir_motivo(motivo)
    if not tramo.esta_abierto:
        raise ValidationError("El tramo no está abierto.")
    if salida_en < tramo.entrada_en:
        raise ValidationError("La salida no puede ser anterior a la entrada.")
    if salida_en > ahora:
        raise ValidationError("La hora de salida no puede ser futura.")
    with transaction.atomic():
        _bloquear_transporte(tramo.transporte_id)
        foto_antes = auditoria.foto(tramo)
        _cerrar(tramo, salida=salida_en, obs=f"Cierre administrativo: {motivo}", usuario=usuario)
        tramo.cierre_administrativo = True
        tramo.save()
        sincronizar_estado(tramo.programacion)  # -> Cerrada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.CIERRE_ADMIN,
                                         motivo=motivo)
        jornada = tramo.jornada
        if tramo.clase == Tramo.Clase.PRE_RUTA and jornada and jornada.estado == Jornada.Estado.PLANIFICADA:
            _actualizar_jornada(jornada, usuario, f"Cierre administrativo de la Pre-Ruta: {motivo}",
                                estado=Jornada.Estado.EN_RUTA, primera_salida=salida_en)
    return tramo


def convertir_en_post_ruta(tramo, usuario, *, fin_atencion, motivo, ahora):
    """La última visita del día quedó abierta porque la unidad se quedó a dormir sin registrar su Post-Ruta:
    la visita se cierra a la hora en que terminó la atención y desde esa hora empieza la Post-Ruta (abierta,
    como cualquier pernocta). La jornada de ese día queda cerrada."""
    motivo = _exigir_motivo(motivo)
    if not (tramo.vigente and tramo.esta_abierto and tramo.es_programado):
        raise ValidationError("Solo se convierte una visita programada que sigue abierta.")
    jornada = jornadas.jornada_vigente(tramo.transporte_id, tramo.fecha_local_entrada)
    if jornada is None or jornada.estado != Jornada.Estado.EN_RUTA:
        raise ValidationError("La placa no tiene una jornada en ruta ese día.")
    if fin_atencion < tramo.entrada_en:
        raise ValidationError("El fin de la atención no puede ser anterior a la entrada.")
    if fin_atencion > ahora:
        raise ValidationError("El fin de la atención no puede ser futuro.")
    with transaction.atomic():
        transporte = _bloquear_transporte(tramo.transporte_id)
        foto_antes = auditoria.foto(tramo)
        _cerrar(tramo, salida=fin_atencion, obs=f"Fin de la atención (corrección): {motivo}", usuario=usuario)
        tramo.cierre_administrativo = True
        tramo.save()
        sincronizar_estado(tramo.programacion)
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.CIERRE_ADMIN,
                                         motivo=motivo)
        post = _nuevo_tramo(clase=Tramo.Clase.POST_RUTA, jornada=jornada, transporte=transporte, cd=tramo.cd,
                            conductor=tramo.conductor, entrada=fin_atencion, km=tramo.km_entrada,
                            obs="Post-Ruta registrada por corrección del Administrador", usuario=None,
                            tipo=Programacion.Tipo.INTERNO, corregido=True)
        _guardar_tramo_nuevo(post)
        auditoria.registrar(usuario, RegistroAuditoria.Accion.CORREGIR, post, despues=auditoria.foto(post),
                            cd=tramo.cd, motivo=motivo)
        _actualizar_jornada(jornada, usuario, f"Post-Ruta registrada por corrección: {motivo}",
                            estado=Jornada.Estado.CERRADA, post_ruta_en=fin_atencion,
                            motivo_cierre=Jornada.MotivoCierre.NORMAL)
        jornadas.marcar_no_atendidas(jornada.transporte_id, jornada.fecha, usuario,
                                     "No atendida: la unidad cerró su jornada (Post-Ruta).")
    return post
