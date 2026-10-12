"""
Dashboard de Gerencia (F5): dónde, cuándo y por qué se demoran los camiones. Solo lectura
para Administrador, Despacho, Consulta y Vigilancia. Los cálculos están en indicadores.py.

Por defecto muestra el día de hoy. Todos los filtros viven en la URL y se aplican a la vez a
todos los paneles: un clic en una placa del ranking, una celda del mapa de calor, un CD, una
barra de puntualidad o un punto de la tendencia filtra el dashboard completo.
"""
from datetime import date, timedelta

from django.shortcuts import render

from .. import indicadores, servicios, tiempo
from ..models import (CentroDistribucion, Conductor, ConfiguracionAccesos, Programacion, TamanoVehiculo, Tramo,
                      Transporte, normalizar_placa)
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, rol_requerido
from .control import cds_de_tramos

MAX_RANGO_DIAS = 180
ETIQUETAS_LLEGADA = {servicios.ADELANTADO: 'Llegó adelantado', servicios.PUNTUAL: 'Llegó puntual',
                     servicios.RETRASADO: 'Llegó retrasado'}
# Filtros que se pueden quitar con un chip (las fechas se cambian con los rangos).
CLAVES_FILTRO = ['cd', 'tipo', 'tamano', 'placa', 'conductor', 'razon_social', 'motivo', 'dia', 'hora', 'llegada',
                 'rango']


def _fecha(valor, por_defecto):
    try:
        return date.fromisoformat(valor) if valor else por_defecto
    except ValueError:
        return por_defecto


def _entero(valor, minimo=None, maximo=None):
    if not (valor and valor.lstrip('-').isdigit()):
        return None
    numero = int(valor)
    if (minimo is not None and numero < minimo) or (maximo is not None and numero > maximo):
        return None
    return numero


def _texto(valor):
    valor = (valor or '').strip()
    return valor[:200] or None


def leer_filtros(params):
    tipos_validos = {valor for valor, _ in Programacion.Tipo.choices}
    return {
        'cd': _entero(params.get('cd'), 1),
        'tipo': params.get('tipo') if params.get('tipo') in tipos_validos else None,
        'tamano': _entero(params.get('tamano'), 1),
        'placa': normalizar_placa(params.get('placa', '')) or None,
        'conductor': _entero(params.get('conductor'), 1),
        'razon_social': _texto(params.get('razon_social')),
        'motivo': _texto(params.get('motivo')),
        'dia': _entero(params.get('dia'), 0, 6),
        'hora': _entero(params.get('hora'), 0, 23),
        'llegada': params.get('llegada') if params.get('llegada') in ETIQUETAS_LLEGADA else None,
        'rango': _entero(params.get('rango'), 0, len(indicadores.TRAMOS_DE_PERMANENCIA) - 1),
    }


def _url(params, **cambios):
    """Misma URL con algunos parámetros cambiados (None = se quita)."""
    nuevos = params.copy()
    for clave, valor in cambios.items():
        if valor is None:
            nuevos.pop(clave, None)
        else:
            nuevos[clave] = valor
    return '?' + nuevos.urlencode() if nuevos else '?'


def _chips(filtros, params):
    """Filtros activos con un texto legible y el enlace para quitarlos."""
    nombres = {
        'cd': lambda v: f"CD {CentroDistribucion.objects.filter(pk=v).first() or v}",
        'tipo': lambda v: dict(Programacion.Tipo.choices)[v],
        'tamano': lambda v: str(TamanoVehiculo.objects.filter(pk=v).first() or v),
        'placa': lambda v: f"Placa {getattr(Transporte.objects.filter(placa_normalizada=v).first(), 'placa', v)}",
        'conductor': lambda v: getattr(Conductor.objects.filter(pk=v).first(), 'nombre_completo', v),
        'razon_social': lambda v: v,
        'motivo': lambda v: f"Motivo: {v}",
        'dia': lambda v: tiempo.DIAS_SEMANA[v],
        'hora': lambda v: f"{v:02d}:00–{v + 1:02d}:00",
        'llegada': lambda v: ETIQUETAS_LLEGADA[v],
        'rango': _etiqueta_rango,
    }
    return [{'clave': clave, 'texto': nombres[clave](filtros[clave]), 'quitar': _url(params, **{clave: None})}
            for clave in CLAVES_FILTRO if filtros.get(clave) is not None]


def _etiqueta_rango(indice):
    desde, hasta = indicadores.TRAMOS_DE_PERMANENCIA[indice]
    return f"Permanencia {desde}–{hasta} min" if hasta else f"Permanencia de más de {desde} min"


def _rangos_rapidos(hoy, params):
    rangos = [
        ('Hoy', hoy, hoy),
        ('Ayer', hoy - timedelta(days=1), hoy - timedelta(days=1)),
        ('7 días', hoy - timedelta(days=6), hoy),
        ('30 días', hoy - timedelta(days=29), hoy),
        ('Este mes', hoy.replace(day=1), hoy),
        ('90 días', hoy - timedelta(days=89), hoy),
    ]
    return [{'etiqueta': e, 'desde': d, 'hasta': h, 'url': _url(params, desde=d.isoformat(), hasta=h.isoformat())}
            for e, d, h in rangos]


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def dashboard(request):
    hoy = tiempo.hoy()
    ahora = tiempo.ahora()
    params = request.GET.copy()
    params.pop('page', None)
    # Por defecto: el día de hoy.
    hasta = _fecha(request.GET.get('hasta'), hoy)
    desde = _fecha(request.GET.get('desde'), hasta)
    if hasta < desde:
        desde, hasta = hasta, desde
    recortado = (hasta - desde).days >= MAX_RANGO_DIAS
    if recortado:
        desde = hasta - timedelta(days=MAX_RANGO_DIAS - 1)
    filtros = leer_filtros(request.GET)
    config = ConfiguracionAccesos.obtener()

    tramos = indicadores.tramos_del_periodo(desde, hasta, filtros, config)
    actuales = indicadores.kpis(tramos, indicadores.programaciones_del_periodo(desde, hasta, filtros), config, hoy)
    # Periodo anterior de igual duración (para «Hoy», el día anterior), para la variación de cada indicador.
    dias = (hasta - desde).days + 1
    desde_anterior, hasta_anterior = desde - timedelta(days=dias), desde - timedelta(days=1)
    anteriores = indicadores.kpis(indicadores.tramos_del_periodo(desde_anterior, hasta_anterior, filtros, config),
                                  indicadores.programaciones_del_periodo(desde_anterior, hasta_anterior, filtros),
                                  config, hoy)
    variacion = indicadores.variacion(actuales, anteriores)

    # Vehículos dentro ahora (mismos filtros de CD, tipo, tamaño, placa y conductor).
    dentro = Tramo.objects.filter(abierto=True).select_related('cd', 'transporte', 'programacion', 'conductor')
    centros = CentroDistribucion.objects.filter(activo=True)
    if filtros['cd']:
        dentro = dentro.filter(cd_id=filtros['cd'])
        centros = centros.filter(pk=filtros['cd'])
    for clave, campo in (('tipo', 'tipo_snapshot'), ('tamano', 'tamano_snapshot_id'),
                         ('placa', 'transporte__placa_normalizada'), ('conductor', 'conductor_id')):
        if filtros[clave]:
            dentro = dentro.filter(**{campo: filtros[clave]})
    dentro = list(dentro.order_by('entrada_en'))
    for tramo in dentro:
        tramo.minutos = servicios.minutos_dentro(tramo, ahora)
        tramo.tiempo = servicios.formato_duracion(tramo.minutos)
        tramo.prolongada = tramo.minutos > config.umbral_prolongada_min

    mapa = indicadores.mapa_de_calor(tramos, config)
    rankings = indicadores.rankings(tramos, config)
    comparativo = indicadores.comparativo_cd(tramos, config)
    segun_llegada = indicadores.permanencia_segun_llegada(tramos, config)
    demoras, total_demoras = indicadores.detalle_demoras(tramos, config, limite=15)
    for tramo in demoras:
        tramo.tiempo = servicios.formato_duracion(tramo.minutos_permanencia)
    for fila in comparativo:
        fila['promedio_texto'] = servicios.formato_duracion(fila['promedio'])
        fila['p90_texto'] = servicios.formato_duracion(fila['p90'])
        fila['url'] = _url(params, cd=fila['valor'])

    un_dia = desde == hasta
    return render(request, 'accesos/dashboard/dashboard.html', {
        'desde': desde, 'hasta': hasta, 'un_dia': un_dia, 'es_hoy': un_dia and desde == hoy,
        'fecha_larga': tiempo.fecha_larga(desde), 'hasta_larga': tiempo.fecha_larga(hasta),
        'recortado': recortado, 'max_rango': MAX_RANGO_DIAS,
        'desde_anterior': desde_anterior, 'hasta_anterior': hasta_anterior,
        'rangos': _rangos_rapidos(hoy, params), 'chips': _chips(filtros, params),
        'limpiar_url': _url(params, **{clave: None for clave in CLAVES_FILTRO}),
        'filtros': filtros, 'config': config, 'limite_demora': indicadores.limite_demora(config),
        'cds': CentroDistribucion.objects.filter(activo=True), 'tipos': Programacion.Tipo.choices,
        'tamanos': TamanoVehiculo.objects.all(), 'hora_actual': ahora,
        'kpis': actuales, 'tarjetas': _tarjetas(actuales, variacion, config, len(dentro),
                                                sum(c.capacidad_simultanea for c in centros), un_dia),
        'conclusiones': indicadores.conclusiones(actuales, variacion, mapa, rankings, comparativo, segun_llegada,
                                                 config),
        'dentro': dentro[:12], 'total_dentro': len(dentro), 'muestra_dentro': desde <= hoy <= hasta,
        'comparativo': comparativo, 'dimensiones': indicadores.DIMENSIONES,
        'demoras': demoras, 'total_demoras': total_demoras, 'cds_tramos': cds_de_tramos(request.user),
        'datos': {
            'mapa': mapa, 'rankings': rankings, 'dimensiones': indicadores.DIMENSIONES,
            'distribucion': indicadores.distribucion(tramos, config),
            'tendencia': indicadores.tendencia(tramos, desde, hasta, config),
            'puntualidad_cd': indicadores.puntualidad_por(tramos, config, 'cd'),
            'puntualidad_tipo': indicadores.puntualidad_por(tramos, config, 'tipo'),
            'segun_llegada': segun_llegada,
            'meta': config.meta_permanencia_min, 'limite': indicadores.limite_demora(config),
            'filtros': {clave: valor for clave, valor in filtros.items() if valor is not None},
        },
    })


def _cambio(valor, unidad, menor_es_mejor, referencia):
    """Texto de la variación frente al periodo anterior y si es una mejora."""
    if valor is None:
        return None, None
    if valor == 0:
        return f"Igual que {referencia}", None
    signo = '+' if valor > 0 else '−'
    return f"{signo}{abs(valor):g}{unidad} vs. {referencia}", (valor < 0) == menor_es_mejor


def _tarjetas(k, v, config, dentro_ahora, capacidad, un_dia):
    limite = indicadores.limite_demora(config)
    referencia = 'el día anterior' if un_dia else 'el periodo anterior'

    def tarjeta(titulo, valor, detalle, clave=None, unidad='', menor_es_mejor=True, icono=''):
        texto, mejora = _cambio(v.get(clave), unidad, menor_es_mejor, referencia) if clave else (None, None)
        return {'titulo': titulo, 'valor': valor, 'detalle': detalle, 'variacion': texto, 'mejora': mejora,
                'icono': icono}

    def porcentaje(x):
        return '—' if x is None else f"{x:g} %"

    return [
        tarjeta('Permanencia promedio', servicios.formato_duracion(k['permanencia_promedio']),
                f"P90: {servicios.formato_duracion(k['permanencia_p90'])} · {k['cerrados']} tramos cerrados",
                'permanencia_promedio', ' min', icono='fa-stopwatch'),
        tarjeta('Tramos con demora', porcentaje(k['pct_demora']),
                f"más de {limite} min" + ('' if config.meta_permanencia_min else ' (meta por definir)'),
                'pct_demora', ' pts', icono='fa-triangle-exclamation'),
        tarjeta('Llegadas puntuales', porcentaje(k['pct_puntual']),
                f"±{config.tolerancia_puntualidad_min} min de la hora programada", 'pct_puntual', ' pts',
                menor_es_mejor=False, icono='fa-calendar-check'),
        tarjeta('Citas no atendidas', porcentaje(k['pct_no_atendidas']),
                f"{k['citas_no_atendidas']} de {k['citas_programadas']} (días terminados)", 'pct_no_atendidas', ' pts',
                icono='fa-calendar-xmark'),
        tarjeta('Permanencias prolongadas', str(k['prolongadas']),
                f"más de {config.umbral_prolongada_min // 60} h · {k['horas_en_planta']:g} h en planta en total",
                'prolongadas', icono='fa-hourglass-end'),
        tarjeta('Dentro ahora', f"{dentro_ahora} / {capacidad}", 'vehículos frente a la capacidad', icono='fa-warehouse'),
    ]
