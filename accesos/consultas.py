"""
Consulta de programaciones con filtros (fecha, CD, placa, conductor, estado), compartida por la
agenda de Despacho y la consulta de Vigilancia. Cada fila trae su tramo con los tiempos reales.
"""
from datetime import date

from django.db.models import Q

from . import servicios
from .models import Programacion, normalizar_placa


def _fecha(valor, por_defecto):
    try:
        return date.fromisoformat(valor) if valor else por_defecto
    except ValueError:
        return por_defecto


def programaciones_filtradas(params, *, hoy, ahora, config, cd=None):
    """`cd` fija el CD (Vigilancia); si es None, se toma del filtro `cd` de los parámetros."""
    filtros = {
        'fecha': _fecha(params.get('fecha'), hoy),
        'cd': params.get('cd', '') if cd is None else str(cd.pk),
        'q': params.get('q', '').strip(),
        'conductor': params.get('conductor', '').strip(),
        'estado': params.get('estado', '') if params.get('estado', '') in servicios.ETIQUETAS_ESTADO else '',
    }
    programaciones = (Programacion.objects.filter(fecha=filtros['fecha'])
                      .select_related('cd', 'transporte__tamano', 'conductor__tipo_documento')
                      .prefetch_related(servicios.PREFETCH_TRAMO_VIGENTE))
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

    filas = list(programaciones.order_by('hora', 'cd__nombre'))
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
