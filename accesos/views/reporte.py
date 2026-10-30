"""
Reporte de jornadas de la flota propia (30/10/2026): pantalla con filtros, cuatro vistas (por placa y día —la
principal—, por turno, resumen por placa y calidad del km) y exportación a Excel
(reporte_jornadas.py). Administrador, Despacho y Consulta. Los filtros son los del Dashboard › Flota (rango, CD,
turno, placa, tamaño, conductor) más los propios del reporte (estado, Pre-Ruta, retorno, km); todo vive en la URL,
así que «Ver reporte detallado» del Dashboard abre las mismas cifras.
"""
from datetime import timedelta

from django.shortcuts import render
from django.urls import reverse

from .. import excel, reporte_jornadas, tiempo
from ..models import CentroDistribucion, Conductor, TamanoVehiculo
from ..paginacion import paginar
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, rol_requerido
from .dashboard import MAX_RANGO_DIAS, TURNOS, _chips, _fecha, _rangos_rapidos, _url, leer_filtros

# Filtros del Dashboard que aplican a la flota propia.
CLAVES_FLOTA = ('cd', 'tamano', 'placa', 'conductor', 'turno')


def _periodo(request, hoy):
    hasta = _fecha(request.GET.get('hasta'), hoy)
    desde = _fecha(request.GET.get('desde'), hasta)
    if hasta < desde:
        desde, hasta = hasta, desde
    recortado = (hasta - desde).days >= MAX_RANGO_DIAS
    if recortado:
        desde = hasta - timedelta(days=MAX_RANGO_DIAS - 1)
    return desde, hasta, recortado


def _filtros(request):
    todos = leer_filtros(request.GET)
    return {clave: (todos[clave] if clave in CLAVES_FLOTA else None) for clave in todos}


def _consulta(request):
    ahora = tiempo.ahora()
    desde, hasta, recortado = _periodo(request, ahora.date())
    filtros = _filtros(request)
    extra = reporte_jornadas.leer_extra(request.GET)
    lista, rutas, k = reporte_jornadas.datos(desde, hasta, filtros, extra, ahora)
    return ahora, desde, hasta, recortado, filtros, extra, lista, rutas, k


def _texto_filtros(desde, hasta, chips):
    periodo = f"{desde:%d/%m/%Y}" + (f" al {hasta:%d/%m/%Y}" if hasta != desde else '')
    return ' · '.join([periodo] + [c['texto'] for c in chips])


def _chips_reporte(filtros, extra, params):
    chips = [c for c in _chips(filtros, params) if c['clave'] in CLAVES_FLOTA]
    nombres = {'estado': reporte_jornadas.ESTADOS, 'pre': reporte_jornadas.PRE_RUTA,
               'retorno': reporte_jornadas.RETORNOS, 'km': reporte_jornadas.KM}
    for clave, valor in extra.items():
        if valor:
            chips.append({'clave': clave, 'texto': nombres[clave][valor], 'quitar': _url(params, **{clave: None})})
    return chips


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def reporte(request):
    ahora, desde, hasta, recortado, filtros, extra, lista, rutas, k = _consulta(request)
    params = request.GET.copy()
    params.pop('page', None)
    vistas = dict((v, e) for v, e, _ in reporte_jornadas.VISTAS)
    vista = request.GET.get('vista') if request.GET.get('vista') in vistas else reporte_jornadas.VISTA_INICIAL
    orden = request.GET.get('orden') if request.GET.get('orden') in reporte_jornadas.ORDENES else 'fecha'
    if vista == 'turno':
        filas = reporte_jornadas.ordenar(reporte_jornadas.filas_turno(lista, rutas, ahora), orden)
    elif vista == 'placa_dia':
        filas = reporte_jornadas.filas_placa_dia(lista, rutas)
    elif vista == 'mes':
        filas = reporte_jornadas.filas_mes(lista)
    else:
        filas = reporte_jornadas.filas_calidad(lista)
    chips = _chips_reporte(filtros, extra, params)
    sin_filtros = {clave: None for clave in (*CLAVES_FLOTA, *extra)}
    contexto = {
        'vista': vista, 'orden': orden, 'desde': desde, 'hasta': hasta, 'recortado': recortado,
        'max_rango': MAX_RANGO_DIAS, 'hoy': ahora.date(),
        'vistas': [{'clave': v, 'etiqueta': e, 'icono': i, 'url': _url(params, vista=v), 'activo': v == vista}
                   for v, e, i in reporte_jornadas.VISTAS],
        'ordenes': [{'clave': o, 'url': _url(params, orden=o)} for o in reporte_jornadas.ORDENES],
        'rangos': _rangos_rapidos(ahora.date(), params), 'chips': chips,
        'limpiar_url': _url(params, **sin_filtros), 'filtros': filtros, 'extra': extra,
        'tarjetas': reporte_jornadas.resumen(k), 'kpis': k,
        'cds': CentroDistribucion.objects.filter(activo=True), 'tamanos': TamanoVehiculo.objects.all(),
        'conductores': Conductor.objects.filter(activo=True).order_by('apellido_paterno', 'apellido_materno', 'nombres', 'id'),
        'turnos': TURNOS, 'estados': reporte_jornadas.ESTADOS, 'pres': reporte_jornadas.PRE_RUTA,
        'retornos': reporte_jornadas.RETORNOS, 'kms': reporte_jornadas.KM,
        'exportar_url': reverse('accesos:reporte_exportar') + _url(params),
        'dashboard_url': reverse('accesos:dashboard') + _url(params, vista='flota', estado=None, pre=None, retorno=None,
                                                             km=None, orden=None),
        'aviso': reporte_jornadas.AVISO, 'total': len(filas), 'tope_km': reporte_jornadas.kilometraje.TOPE_TURNO,
    }
    contexto.update(paginar(request, filas))
    return render(request, 'accesos/reporte/reporte.html', contexto)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def reporte_exportar(request):
    """Excel de todo lo filtrado (no solo la página): las mismas cifras de la pantalla y del Dashboard."""
    ahora, desde, hasta, _, filtros, extra, lista, rutas, k = _consulta(request)
    params = request.GET.copy()
    chips = _chips_reporte(filtros, extra, params)
    hojas = reporte_jornadas.hojas_excel(
        reporte_jornadas.filas_turno(lista, rutas, ahora), reporte_jornadas.filas_placa_dia(lista, rutas),
        reporte_jornadas.filas_tipo(lista), reporte_jornadas.filas_calidad(lista), k,
        _texto_filtros(desde, hasta, chips), f"{ahora:%d/%m/%Y %H:%M}", reporte_jornadas.filas_mes(lista),
        reporte_jornadas.filas_movimientos(lista))
    nombre = f"Reporte_jornadas_{desde:%Y%m%d}" + (f"_{hasta:%Y%m%d}" if hasta != desde else '') + '.xlsx'
    return excel.respuesta_libro(nombre, excel.libro_varias_hojas(hojas, reporte_jornadas.NOTAS))
