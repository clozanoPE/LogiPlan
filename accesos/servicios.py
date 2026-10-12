"""
Reglas de negocio que dependen de la hora actual. Reciben `ahora` (datetime consciente de
zona) como parámetro para poder probarlas sin depender del reloj; las vistas les pasan
`tiempo.ahora()`. Referencias R1..R9: documento de diseño aprobado.
"""
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q

from . import auditoria, tiempo
from .models import Conductor, Programacion, RegistroAuditoria, Tramo, Transporte, normalizar_placa
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
    if programacion.esta_cancelada:
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
    """R4: el estado guardado; una programada se muestra Pendiente o, si su día ya terminó, No atendida."""
    if programacion.estado != Estado.PROGRAMADA:
        return programacion.estado
    return NO_ATENDIDA if programacion.fecha < hoy else PENDIENTE


def filtrar_por_estado_visible(queryset, codigo, hoy):
    """Filtra en la base de datos por un estado visible (para las consultas con filtro de estado)."""
    if codigo == PENDIENTE:
        return queryset.filter(estado=Estado.PROGRAMADA, fecha__gte=hoy)
    if codigo == NO_ATENDIDA:
        return queryset.filter(estado=Estado.PROGRAMADA, fecha__lt=hoy)
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

    # R5: capacidad del CD con una duración estimada de atención.
    duracion = config.duracion_estimada_atencion_min
    cercanas = [p for p in activas_del_dia
                if p.cd_id == programacion.cd_id and abs(_minutos(p.hora) - minuto) < duracion]
    capacidad = programacion.cd.capacidad_simultanea
    if len(cercanas) + 1 > capacidad:
        mensajes.append(
            f"Capacidad: hay {len(cercanas) + 1} vehículos programados en {programacion.cd} a menos de "
            f"{duracion} min de las {programacion.hora:%H:%M} (capacidad referencial: {capacidad})."
        )

    # R6: la misma placa programada cerca de ese horario (en cualquier CD).
    ventana = config.ventana_sugerencia_min
    for otra in activas_del_dia:
        if otra.transporte_id == programacion.transporte_id and abs(_minutos(otra.hora) - minuto) < ventana:
            mensajes.append(
                f"La placa {programacion.transporte} también está programada a las {otra.hora:%H:%M} "
                f"en {otra.cd} ({otra.codigo})."
            )

    # R7: tipo INTERNO con un transporte que no es interno.
    if programacion.tipo == Programacion.Tipo.INTERNO and not programacion.transporte.es_interno:
        mensajes.append(f"La programación es INTERNO pero {programacion.transporte} no está marcado como transporte interno.")
    return mensajes


# --- PORTERÍA (F3): R10–R17 ---

ENTRADA = 'ENTRADA'
SALIDA = 'SALIDA'
NO_PROGRAMADO = 'NO_PROGRAMADO'
OTRO_CD = 'OTRO_CD'


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

    @property
    def sugerida(self):
        """R14: la candidata más cercana dentro de la ventana; si ninguna lo está, no hay sugerida."""
        return next((c for c in self.candidatas if c.en_ventana), None)


def _minutos_entre(desde, hasta):
    return round((hasta - desde).total_seconds() / 60)


def candidatas_entrada(transporte, cd, ahora, ventana_min):
    """R13: programaciones de hoy de esa placa en ese CD, activas y sin tramo válido, por cercanía."""
    programaciones = (Programacion.objects
                      .filter(transporte=transporte, cd=cd, fecha=ahora.date(), estado=Programacion.Estado.PROGRAMADA)
                      .exclude(tramos__vigente=True)
                      .select_related('conductor__tipo_documento', 'cd'))
    candidatas = []
    for programacion in programaciones:
        minutos = _minutos_entre(ahora, momento(programacion))
        candidatas.append(Candidata(programacion, minutos, abs(minutos) <= ventana_min))
    return sorted(candidatas, key=lambda c: abs(c.minutos))


def evaluar_porteria(texto, cd, ahora, config):
    """R10–R16: con la placa (digitada o leída del QR) decide ENTRADA, SALIDA o bloqueo."""
    placa = normalizar_placa(texto)
    transporte = Transporte.objects.select_related('tamano').filter(placa_normalizada=placa).first() if placa else None
    if transporte is None:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. (La placa {texto.strip().upper()} no está registrada.)")

    abierto = (Tramo.objects.filter(transporte=transporte, abierto=True)
               .select_related('cd', 'programacion', 'conductor').first())
    if abierto and abierto.cd_id != cd.pk:
        return ResultadoPorteria(
            OTRO_CD, f"{transporte} figura DENTRO de CD {abierto.cd} desde el "
                     f"{tiempo.a_lima(abierto.entrada_en):%d/%m/%Y %H:%M}. No se puede registrar. "
                     "Comuníquese con el Administrador.",
            transporte=transporte, tramo_abierto=abierto)
    if abierto:
        return ResultadoPorteria(SALIDA, transporte=transporte, tramo_abierto=abierto)

    if not transporte.activo:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. ({transporte} está inactivo.)", transporte=transporte)
    candidatas = candidatas_entrada(transporte, cd, ahora, config.ventana_sugerencia_min)
    if not candidatas:
        return ResultadoPorteria(
            NO_PROGRAMADO, f"Vehículo no programado. No puede ingresar. ({transporte} no tiene programaciones "
                           f"pendientes hoy en CD {cd}.)", transporte=transporte)
    return ResultadoPorteria(ENTRADA, transporte=transporte, candidatas=candidatas)


def _bloquear_transporte(transporte_id):
    """Serializa las operaciones de portería de una misma placa (dos tablets a la vez)."""
    return Transporte.objects.select_for_update().select_related('tamano').get(pk=transporte_id)


def _miles(numero):
    return f"{numero:,}".replace(',', '.')


def registrar_entrada(*, transporte_id, programacion_id, cd, km, obs, conductor_id, usuario, ahora, config,
                      confirmar_fuera_de_ventana=False):
    """R12–R15. Devuelve (tramo, avisos). Lanza ValidationError si algo impide la entrada."""
    if km is None or not (0 <= km <= KM_MAXIMO):
        raise ValidationError(f"Ingrese el kilometraje (entre 0 y {_miles(KM_MAXIMO)}).")
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        # Todo se vuelve a evaluar dentro del bloqueo: otra tablet pudo registrar mientras tanto.
        if Tramo.objects.filter(transporte=transporte, abierto=True).exists():
            raise ValidationError(MENSAJE_ESTADO_CAMBIO)
        candidatas = candidatas_entrada(transporte, cd, ahora, config.ventana_sugerencia_min)
        candidata = next((c for c in candidatas if c.programacion.pk == programacion_id), None)
        if candidata is None:
            raise ValidationError("La programación elegida ya no está disponible. Vuelva a escanear la placa.")
        if not candidata.en_ventana and not confirmar_fuera_de_ventana:
            raise ValidationError(f"La programación elegida está fuera de la ventana de ±{config.ventana_sugerencia_min} "
                                  "min: confírmela para registrar la entrada.", code='confirmar_fuera_de_ventana')
        programacion = candidata.programacion

        conductor = programacion.conductor
        if conductor_id and conductor_id != conductor.pk:
            conductor = Conductor.objects.filter(pk=conductor_id, activo=True).first()
            if conductor is None:
                raise ValidationError("El conductor elegido no existe o está inactivo.")

        ultimo = Tramo.objects.filter(transporte=transporte, vigente=True).order_by('-entrada_en').first()
        avisos = []
        km_inconsistente = bool(ultimo and km < ultimo.km_entrada)
        if km_inconsistente:
            avisos.append(f"El kilometraje ({_miles(km)}) es menor al último registrado ({_miles(ultimo.km_entrada)}). "
                          "Quedó marcado para revisión.")

        tramo = Tramo(
            programacion=programacion, transporte=transporte, cd=cd, conductor=conductor,
            entrada_en=ahora, fecha_local_entrada=ahora.date(), hora_local_entrada=ahora.time().replace(microsecond=0),
            km_entrada=km, obs_entrada=(obs or '').strip() or 'Ninguna', vigilante_entrada=usuario,
            desviacion_llegada_min=-candidata.minutos,
            tamano_snapshot=transporte.tamano, tipo_snapshot=programacion.tipo,
            fuera_de_ventana=not candidata.en_ventana, km_inconsistente=km_inconsistente,
        )
        try:
            with transaction.atomic():
                tramo.save()
        except IntegrityError:
            raise ValidationError(MENSAJE_ESTADO_CAMBIO)
        sincronizar_estado(programacion)  # -> En planta
        auditoria.registrar(usuario, RegistroAuditoria.Accion.ENTRADA, tramo, despues=auditoria.foto(tramo), cd=cd)
    return tramo, avisos


def registrar_salida(*, transporte_id, tramo_id, cd, obs, usuario, ahora):
    """R12: cierra el tramo abierto de la placa en este CD."""
    with transaction.atomic():
        transporte = _bloquear_transporte(transporte_id)
        tramo = Tramo.objects.filter(pk=tramo_id, transporte=transporte, abierto=True).first()
        if tramo is None:
            raise ValidationError(MENSAJE_ESTADO_CAMBIO)
        if tramo.cd_id != cd.pk:
            raise ValidationError(f"El vehículo figura dentro de CD {tramo.cd}; no se puede registrar su salida aquí.")
        foto_antes = auditoria.foto(tramo)
        _cerrar(tramo, salida=ahora, obs=obs, usuario=usuario)
        tramo.save()
        sincronizar_estado(tramo.programacion)  # -> Cerrada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.SALIDA)
    return tramo


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
    """R21: ADELANTADO, PUNTUAL o RETRASADO según la tolerancia vigente."""
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


# --- CORRECCIONES DEL ADMINISTRADOR (F4): R24–R27 ---

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
    tramo.desviacion_llegada_min = _minutos_entre(momento(tramo.programacion), tramo.entrada_en)
    tramo.tipo_snapshot = tramo.programacion.tipo
    if tramo.salida_en:
        tramo.fecha_local_salida = tiempo.a_lima(tramo.salida_en).date()
        tramo.minutos_permanencia = max(0, int((tramo.salida_en - tramo.entrada_en).total_seconds() // 60))


def programaciones_para_corregir(tramo):
    """Programaciones a las que se puede reasignar el tramo: misma placa, activas y sin otro tramo válido."""
    ocupadas = Tramo.objects.filter(vigente=True).exclude(pk=tramo.pk).values('programacion_id')
    return (Programacion.objects.filter(Q(estado=Estado.PROGRAMADA) | Q(pk=tramo.programacion_id),
                                        transporte_id=tramo.transporte_id)
            .exclude(pk__in=ocupadas).select_related('cd').order_by('-fecha', '-hora'))


def corregir_tramo(tramo, usuario, *, motivo, ahora, entrada_en, salida_en, km_entrada, obs_entrada, obs_salida,
                   programacion, conductor):
    """R25: corrige un tramo válido; recalcula permanencia, desviación y fechas locales."""
    motivo = _exigir_motivo(motivo)
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
    if programacion.transporte_id != tramo.transporte_id or programacion.esta_cancelada or (
            programacion.pk != tramo.programacion_id and programacion.estado != Estado.PROGRAMADA):
        raise ValidationError("La programación elegida no corresponde a esta placa o está cancelada.")
    if not (0 <= km_entrada <= KM_MAXIMO):
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
        sincronizar_estado(programacion)
        if programacion_anterior.pk != programacion.pk:
            sincronizar_estado(programacion_anterior)  # vuelve a quedar Programada
        auditoria.registrar_modificacion(usuario, tramo, foto_antes, accion=RegistroAuditoria.Accion.CORREGIR,
                                         motivo=motivo)
    return tramo


def anular_tramo(tramo, usuario, *, motivo):
    """R24: el tramo deja de contar (no se borra) y su programación vuelve a quedar pendiente."""
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
    return tramo
