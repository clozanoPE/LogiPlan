"""
Consulta de programaciones con filtros (fecha, CD, placa, conductor, estado), compartida por la
agenda de Despacho y la consulta de Vigilancia. Cada fila trae su tramo con los tiempos reales.
"""
from datetime import date, timedelta

from django.db.models import Prefetch, Q

from . import servicios
from .models import Programacion, Tramo, normalizar_placa

MAX_RANGO_EXPORTAR_DIAS = 31  # la exportación por rango admite hasta 31 días (un mes)

# Igual que servicios.PREFETCH_TRAMO_VIGENTE, con el conductor que ingresó (se muestra si cambió).
PREFETCH_TRAMO = Prefetch('tramos', queryset=Tramo.objects.filter(vigente=True).select_related('conductor'),
                          to_attr='tramos_vigentes')


def _fecha(valor, por_defecto):
    try:
        return date.fromisoformat(valor) if valor else por_defecto
    except ValueError:
        return por_defecto


def rango_exportar(params, fecha):
    """(desde, hasta, recortado) de la exportación por rango. Sin `desde`, el día de la pantalla."""
    desde = _fecha(params.get('desde'), None)
    if desde is None:
        return fecha, fecha, False
    hasta = _fecha(params.get('hasta'), desde)
    if hasta < desde:
        desde, hasta = hasta, desde
    recortado = (hasta - desde).days >= MAX_RANGO_EXPORTAR_DIAS
    if recortado:
        hasta = desde + timedelta(days=MAX_RANGO_EXPORTAR_DIAS - 1)
    return desde, hasta, recortado


def programaciones_filtradas(params, *, hoy, ahora, config, cd=None, rango=None):
    """`cd` fija el CD (Vigilancia); si es None, se toma del filtro `cd` de los parámetros. `rango` (desde,
    hasta) reemplaza la fecha: solo lo usa la exportación a Excel (las pantallas muestran un día)."""
    filtros = {
        'fecha': _fecha(params.get('fecha'), hoy),
        'cd': params.get('cd', '') if cd is None else str(cd.pk),
        'q': params.get('q', '').strip(),
        'conductor': params.get('conductor', '').strip(),
        'estado': params.get('estado', '') if params.get('estado', '') in servicios.ETIQUETAS_ESTADO else '',
    }
    fechas = {'fecha__range': rango} if rango else {'fecha': filtros['fecha']}
    programaciones = (Programacion.objects.filter(**fechas)
                      .select_related('cd', 'transporte__tamano', 'conductor__tipo_documento', 'creado_por', 'incidencia',
                                      'jornada')
                      .prefetch_related(PREFETCH_TRAMO))
    if filtros['cd'].isdigit():
        programaciones = programaciones.filter(cd_id=int(filtros['cd']))
    if filtros['q']:
        programaciones = programaciones.filter(transporte__placa_normalizada__icontains=normalizar_placa(filtros['q']))
    if filtros['conductor']:
        texto = filtros['conductor']
        # Busca en el conductor programado y en el que realmente ingresó.
        programaciones = programaciones.filter(
            Q(conductor__apellido_paterno__icontains=texto) | Q(conductor__apellido_materno__icontains=texto)
            | Q(conductor__nombres__icontains=texto) | Q(conductor__numero_documento__icontains=texto)
            | Q(tramos__vigente=True, tramos__conductor__apellido_paterno__icontains=texto)
            | Q(tramos__vigente=True, tramos__conductor__nombres__icontains=texto)
            | Q(tramos__vigente=True, tramos__conductor__numero_documento__icontains=texto)
        ).distinct()
    programaciones = servicios.filtrar_por_estado_visible(programaciones, filtros['estado'], hoy)

    filas = list(programaciones.order_by('fecha', 'hora', 'cd__nombre', 'id'))
    for p in filas:
        p.estado_codigo = servicios.estado_visible(p, hoy)
        p.tramo = servicios.tramo_vigente(p)
        if p.tramo:
            minutos = servicios.minutos_dentro(p.tramo, ahora)
            p.permanencia = servicios.formato_duracion(minutos)
            p.prolongada = p.tramo.es_programado and minutos > config.umbral_prolongada_min
            p.conductor_distinto = p.tramo.conductor_id != p.conductor_id
            # Llegada (clientes y proveedores) o salida (rutas INTERNO, incluida la que atiende la Pre-Ruta).
            p.puntualidad = servicios.puntualidad(p.tramo.desviacion_min, config.tolerancia_puntualidad_min)
            p.texto_desviacion = servicios.texto_desviacion(p.tramo.desviacion_min)
    return filas, filtros
