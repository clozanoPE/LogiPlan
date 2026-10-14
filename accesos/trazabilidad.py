"""
Trazabilidad de una placa en un día (14/10/2026): todo lo que pasó con el vehículo, en orden, con la lectura
de lo programado frente a lo real.

- Rutas INTERNO: la hora programada es la de SALIDA del CD. La primera ruta la atiende la salida a ruta que
  cierra la Pre-Ruta; las siguientes, la salida de cada visita. Desde la tolerancia (15 min) es retraso.
- Clientes y proveedores: se califica la llegada frente a la hora programada.
- Post-Ruta: el retorno se compara con el cierre operativo (16:00) y su tolerancia (16:15); los minutos fuera
  de horario se cuentan sobre las 16:15. Es un indicador operativo del VEHÍCULO, no de asistencia.

Todo se arma en Python con los tramos válidos de la placa que tocan ese día (también la pernocta que venía
del día anterior y la que sigue al día siguiente, como contexto) y sus programaciones.
"""
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q

from . import jornadas, servicios, tiempo
from .models import Jornada, Programacion, Tramo

Clase = Tramo.Clase
OK, AVISO, CRITICO, INFO = 'ok', 'aviso', 'critico', 'info'


@dataclass
class Evento:
    momento: datetime
    tipo: str            # pre_inicio, salida_ruta, entrada, salida, post_ruta, fin_pernocta, salida_tras_cierre,
                         # programada (sin atender), pre_en_curso
    titulo: str
    icono: str
    detalle: str = ''
    programada: object = None   # hora programada (time) con la que se compara
    referencia: str = ''        # «salida programada», «llegada programada», «horario de referencia»
    desviacion: int = None
    lectura: str = ''           # «10 min antes», «15 min tarde»
    nivel: str = INFO
    etiqueta: str = ''          # A tiempo, Retraso, En horario, Fuera de horario…
    km: int = None
    cd: object = None
    conductor: str = ''
    vigilante: str = ''
    obs: str = ''
    tramo: Tramo = None
    programacion: Programacion = None
    otro_dia: bool = False
    marcas: list = field(default_factory=list)

    @property
    def hora(self):
        return f"{tiempo.a_lima(self.momento):%H:%M}"

    @property
    def fecha(self):
        return tiempo.a_lima(self.momento).date()


def _calificar(desviacion, tolerancia, de_salida):
    """(nivel, etiqueta) de una desviación frente a su hora programada."""
    puntualidad = servicios.puntualidad(desviacion, tolerancia)
    if puntualidad is None:
        return INFO, ''
    if puntualidad == servicios.RETRASADO:
        return CRITICO, 'Retraso'
    if puntualidad == servicios.ADELANTADO and not de_salida:
        return INFO, 'Adelantado'
    return OK, 'A tiempo'


def _marcas(tramo):
    return [texto for texto, activa in (('Corregido', tramo.corregido), ('Km menor al anterior', tramo.km_inconsistente),
                                        ('Fuera de ventana', tramo.fuera_de_ventana),
                                        ('Cierre administrativo', tramo.cierre_administrativo),
                                        ('Día no laborable', tramo.dia_no_laborable)) if activa]


def _texto_programacion(p):
    return f"{p.codigo} · {p.get_tipo_display()} · {p.razon_social}" + (f" · {p.motivo}" if p.motivo else '')


def _eventos_de_tramo(t, jornada, tolerancia):
    """Uno o dos eventos (entrada y salida) del tramo, con su lectura."""
    eventos = []
    p = t.programacion if t.programacion_id else None
    base = dict(cd=t.cd, conductor=t.conductor.nombre_completo, tramo=t, programacion=p, marcas=_marcas(t))
    vigilante_entrada = str(t.vigilante_entrada) if t.vigilante_entrada_id else 'Sistema'
    vigilante_salida = str(t.vigilante_salida) if t.vigilante_salida_id else ''

    if t.clase == Clase.PRE_RUTA:
        ingreso = bool(t.vigilante_entrada_id)
        eventos.append(Evento(
            momento=t.entrada_en, tipo='pre_inicio', icono='fa-sun',
            titulo='Ingreso al CD · inicio de la Pre-Ruta' if ingreso else 'Inicio de la Pre-Ruta',
            detalle=('Llegó desde afuera: la Pre-Ruta empieza al ingresar.' if ingreso else
                     f"Referencia {t.jornada.hora_inicio:%H:%M}: la unidad durmió en el CD." if t.jornada_id else ''),
            km=t.km_entrada if ingreso else None, vigilante=vigilante_entrada,
            obs=t.obs_entrada if ingreso else '', **base))
        if t.salida_en:
            minutos = t.minutos_permanencia
            meta = t.jornada.meta_pre_ruta_min if t.jornada_id else None
            detalle = f"Pre-Ruta de {servicios.formato_duracion(minutos)}" + (f" (meta {meta} min)" if meta else '')
            evento = Evento(momento=t.salida_en, tipo='salida_ruta', icono='fa-truck-fast',
                            titulo='Salida a ruta', detalle=detalle, km=None if ingreso else t.km_entrada,
                            vigilante=vigilante_salida, obs=t.obs_salida, **base)
            if p:
                evento.titulo = f"Salida a ruta · ruta de las {p.hora:%H:%M}"
                evento.programada, evento.referencia = p.hora, 'salida programada'
                evento.desviacion = t.desviacion_salida_min
                evento.lectura = servicios.texto_desviacion(t.desviacion_salida_min)
                evento.nivel, evento.etiqueta = _calificar(t.desviacion_salida_min, tolerancia, True)
            if meta and minutos is not None and minutos > meta:
                evento.marcas = evento.marcas + [f"Pre-Ruta excedida (+{minutos - meta} min)"]
            eventos.append(evento)
        return eventos

    if t.clase == Clase.POST_RUTA:
        averia = bool(jornada and jornada.motivo_cierre == Jornada.MotivoCierre.AVERIA and t.jornada_id == jornada.pk
                      and jornada.post_ruta_en == t.entrada_en)
        es_cierre = bool(t.jornada_id and t.jornada.post_ruta_en == t.entrada_en)
        evento = Evento(momento=t.entrada_en, tipo='post_ruta', icono='fa-flag-checkered',
                        titulo='Ingreso por avería (Post-Ruta)' if averia else
                        ('Post-Ruta · fin de jornada' if es_cierre else 'Reingreso al CD (jornada ya cerrada)'),
                        km=t.km_entrada, vigilante=vigilante_entrada, obs=t.obs_entrada, **base)
        if es_cierre and t.jornada_id:
            j = t.jornada
            limite = jornadas.limite_cierre(j)
            evento.referencia = f"horario de referencia {j.hora_cierre:%H:%M}–{limite:%H:%M}"
            calificacion = jornadas.calificacion_cierre(j)
            if calificacion == jornadas.FUERA_DE_HORARIO:
                minutos = jornadas.minutos_sobre_cierre(j)
                evento.desviacion, evento.lectura = minutos, f"{servicios.formato_duracion(minutos)} tarde (sobre las {limite:%H:%M})"
                evento.nivel, evento.etiqueta = CRITICO, 'Fuera de horario'
            elif calificacion == jornadas.EN_HORARIO:
                evento.nivel, evento.etiqueta = OK, 'En horario'
            elif calificacion == jornadas.ANTICIPADO:
                evento.nivel, evento.etiqueta = INFO, 'Fin anticipado'
            elif calificacion == jornadas.POR_AVERIA:
                evento.nivel, evento.etiqueta = AVISO, 'Cierre por avería'
            evento.detalle = 'La unidad queda en el CD (pernocta).' if t.esta_abierto or t.cierre_automatico else ''
        eventos.append(evento)
        if t.salida_en:
            if t.cierre_automatico:
                eventos.append(Evento(momento=t.salida_en, tipo='fin_pernocta', icono='fa-moon',
                                      titulo='Fin de la pernocta · empieza la Pre-Ruta',
                                      detalle='Cierre automático al iniciar la Pre-Ruta del día.', **base))
            else:
                eventos.append(Evento(momento=t.salida_en, tipo='salida_tras_cierre', icono='fa-right-from-bracket',
                                      titulo='Salida después del fin de jornada', nivel=AVISO, etiqueta='Con motivo',
                                      vigilante=vigilante_salida, obs=t.obs_salida, **base))
        return eventos

    # Visita programada (cliente, proveedor o ruta INTERNO).
    interno = t.califica_salida
    entrada = Evento(momento=t.entrada_en, tipo='entrada', icono='fa-right-to-bracket', titulo='Entrada al CD',
                     detalle=_texto_programacion(p) if p else '', km=t.km_entrada, vigilante=vigilante_entrada,
                     obs=t.obs_entrada, **base)
    if p and not interno:
        entrada.programada, entrada.referencia = p.hora, 'llegada programada'
        entrada.desviacion = t.desviacion_llegada_min
        entrada.lectura = servicios.texto_desviacion(t.desviacion_llegada_min)
        entrada.nivel, entrada.etiqueta = _calificar(t.desviacion_llegada_min, tolerancia, False)
    eventos.append(entrada)
    if t.salida_en:
        salida = Evento(momento=t.salida_en, tipo='salida_ruta' if interno else 'salida',
                        icono='fa-truck-fast' if interno else 'fa-right-from-bracket',
                        titulo=f"Salida a ruta · ruta de las {p.hora:%H:%M}" if interno and p else 'Salida del CD',
                        detalle=f"En el CD {servicios.formato_duracion(t.minutos_permanencia)}",
                        vigilante=vigilante_salida, obs=t.obs_salida, **base)
        if interno and p:
            salida.programada, salida.referencia = p.hora, 'salida programada'
            salida.desviacion = t.desviacion_salida_min
            salida.lectura = servicios.texto_desviacion(t.desviacion_salida_min)
            salida.nivel, salida.etiqueta = _calificar(t.desviacion_salida_min, tolerancia, True)
        eventos.append(salida)
    return eventos


ESTADOS_SIN_ATENDER = {
    servicios.PENDIENTE: (INFO, 'Pendiente'),
    servicios.NO_ATENDIDA: (AVISO, 'No atendida'),
    servicios.CANCELADA: (INFO, 'Cancelada'),
}


def del_dia(transporte, fecha, ahora, config, cd_ids=None):
    """Eventos de la placa en la fecha (en orden) y su resumen. `cd_ids`: limita a esos CD (Vigilancia)."""
    tolerancia = config.tolerancia_puntualidad_min
    jornada = jornadas.jornada_vigente(transporte.pk, fecha)
    toca_el_dia = Q(fecha_local_entrada=fecha) | Q(fecha_local_salida=fecha)
    if fecha <= ahora.date():
        toca_el_dia |= Q(abierto=True, fecha_local_entrada__lt=fecha)  # sigue dentro desde otro día
    tramos = (Tramo.objects.filter(transporte=transporte, vigente=True).filter(toca_el_dia)
              .select_related('programacion', 'cd', 'conductor', 'vigilante_entrada', 'vigilante_salida', 'jornada')
              .order_by('entrada_en', 'id'))
    programaciones = (Programacion.objects.filter(transporte=transporte, fecha=fecha)
                      .select_related('cd', 'conductor').order_by('hora', 'pk'))
    if cd_ids is not None:
        tramos = tramos.filter(cd_id__in=cd_ids)
        programaciones = programaciones.filter(cd_id__in=cd_ids)
    tramos, programaciones = list(tramos), list(programaciones)

    eventos = []
    for t in tramos:
        eventos.extend(_eventos_de_tramo(t, jornada, tolerancia))
    atendidas = {t.programacion_id for t in tramos if t.programacion_id}
    hoy = ahora.date()
    for p in programaciones:
        if p.pk in atendidas:
            continue
        estado = servicios.estado_visible(p, hoy)
        nivel, etiqueta = ESTADOS_SIN_ATENDER.get(estado, (INFO, servicios.ETIQUETAS_ESTADO.get(estado, '')))
        if estado == servicios.NO_ATENDIDA and p.no_atendida_por_cierre:
            etiqueta = 'No atendida (cierre de jornada)'
        detalle = _texto_programacion(p)
        if estado == servicios.CANCELADA:
            detalle += f" · Motivo: {p.motivo_cancelacion}"
        interno = p.tipo == Programacion.Tipo.INTERNO
        eventos.append(Evento(momento=servicios.momento(p), tipo='programada', icono='fa-calendar-xmark' if estado != servicios.PENDIENTE else 'fa-calendar',
                              titulo=f"{'Ruta' if interno else 'Cita'} de las {p.hora:%H:%M} sin atender",
                              detalle=detalle, programada=p.hora,
                              referencia='salida programada' if interno else 'llegada programada',
                              nivel=nivel, etiqueta=etiqueta, cd=p.cd, conductor=p.conductor.nombre_completo,
                              programacion=p))
    # Pre-Ruta en curso (todavía no se graba): la jornada está planificada y ya pasó su hora de inicio.
    if jornada and jornada.estado == Jornada.Estado.PLANIFICADA and fecha == hoy and ahora >= jornadas.inicio(jornada):
        dentro = any(t.esta_abierto for t in tramos)
        estado = jornadas.estado_visible(jornada, ahora, dentro=dentro)
        eventos.append(Evento(momento=jornadas.inicio(jornada), tipo='pre_en_curso', icono='fa-sun',
                              titulo=f"Pre-Ruta: {estado.etiqueta.lower()}", nivel=AVISO if estado.alerta else INFO,
                              detalle=f"Desde las {jornada.hora_inicio:%H:%M}; meta {jornada.meta_pre_ruta_min} min. "
                                      "Se registra al salir a ruta.", cd=jornada.cd,
                              conductor=jornada.conductor.nombre_completo))
    orden_tipo = {'fin_pernocta': 0, 'pre_inicio': 1, 'pre_en_curso': 1}
    eventos.sort(key=lambda e: (e.momento, orden_tipo.get(e.tipo, 2)))
    for e in eventos:
        e.otro_dia = e.fecha != fecha
    return {'eventos': eventos, 'jornada': jornada, 'resumen': _resumen(eventos, programaciones, tramos, jornada, fecha,
                                                                         ahora, tolerancia)}


def _resumen(eventos, programaciones, tramos, jornada, fecha, ahora, tolerancia):
    activas = [p for p in programaciones if not p.esta_cancelada]
    atendidas = [p for p in activas if p.estado in (Programacion.Estado.EN_PLANTA, Programacion.Estado.CERRADA)]
    salidas = [e for e in eventos if e.tipo == 'salida_ruta' and e.desviacion is not None and not e.otro_dia]
    retrasos = [e for e in salidas if e.etiqueta == 'Retraso']
    lecturas = [e for e in eventos if e.km is not None and not e.otro_dia]
    km_recorridos = None
    if len(lecturas) >= 2 and lecturas[-1].km >= lecturas[0].km:
        km_recorridos = lecturas[-1].km - lecturas[0].km
    en_cd = sum(t.minutos_permanencia or 0 for t in tramos if t.clase == Clase.PROGRAMADO and t.salida_en
                and t.fecha_local_entrada == fecha)
    resumen = {
        'programadas': len(activas), 'atendidas': len(atendidas),
        'no_atendidas': sum(1 for p in activas if servicios.estado_visible(p, ahora.date()) == servicios.NO_ATENDIDA),
        'canceladas': len(programaciones) - len(activas),
        'salidas': len(salidas), 'salidas_retrasadas': len(retrasos), 'salidas_a_tiempo': len(salidas) - len(retrasos),
        'retraso_total': sum(e.desviacion for e in retrasos),
        'km_inicial': lecturas[0].km if lecturas else None, 'km_final': lecturas[-1].km if lecturas else None,
        'km_recorridos': km_recorridos, 'minutos_en_cd': en_cd, 'tiempo_en_cd': servicios.formato_duracion(en_cd),
        'tolerancia': tolerancia,
    }
    if jornada:
        resumen.update({
            'pre_ruta_min': jornadas.minutos_pre_ruta(jornada), 'meta_pre_ruta': jornada.meta_pre_ruta_min,
            'calificacion_cierre': jornadas.ETIQUETAS_CIERRE.get(jornadas.calificacion_cierre(jornada)),
            'minutos_fuera': jornadas.minutos_sobre_cierre(jornada),
            'limite_cierre': jornadas.limite_cierre(jornada),
            'estado': jornadas.estado_visible(jornada, ahora, dentro=any(t.esta_abierto for t in tramos),
                                              en_visita=any(t.esta_abierto and t.es_programado for t in tramos),
                                              no_disponible=jornadas.incidencia_abierta(jornada.transporte_id) is not None),
        })
    return resumen
