"""
Dashboard de Gerencia: cuatro pestañas con los mismos filtros (F5 + F6.3). Solo lectura para
Administrador, Despacho, Consulta y Vigilancia.
- Resumen ejecutivo: 8 indicadores con variación y tendencia de 14 días, alertas en vivo,
  conclusiones y calendario de las últimas 5 semanas.
- Operación en planta: dónde, cuándo y por qué se demoran los camiones (visitas programadas;
  cálculos en indicadores.py).
- Flota propia: jornada (Pre-Ruta y Post-Ruta), línea de tiempo del día, matriz placa × día,
  distribuciones, tendencia y rankings (indicadores_flota.py).
- Disponibilidad: averías, días fuera de servicio, tiempo de reparación (indicadores_flota.py).

Por defecto muestra el día de hoy. Todos los filtros viven en la URL y se aplican a la vez a
todos los paneles: un clic en una placa del ranking, una celda del mapa de calor, un CD, una
barra de puntualidad o un punto de la tendencia filtra el dashboard completo.
"""
from datetime import date, timedelta

from django.shortcuts import render

from .. import indicadores, indicadores_flota, jornadas, servicios, tiempo
from ..models import (CentroDistribucion, Conductor, ConfiguracionAccesos, Programacion, TamanoVehiculo, Tramo,
                      Transporte, normalizar_placa)
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, rol_requerido
from .control import cds_de_tramos

MAX_RANGO_DIAS = 180
# Puntualidad: la llegada en clientes y proveedores; la salida en las rutas INTERNO (su hora es la de salida).
ETIQUETAS_LLEGADA = {servicios.ADELANTADO: 'Adelantado', servicios.PUNTUAL: 'Puntual',
                     servicios.RETRASADO: 'Retrasado'}
# Filtros que se pueden quitar con un chip (las fechas se cambian con los rangos).
CLAVES_FILTRO = ['cd', 'tipo', 'tamano', 'placa', 'conductor', 'razon_social', 'motivo', 'dia', 'hora', 'llegada',
                 'rango', 'turno']
VISTAS = [
    ('resumen', 'Resumen ejecutivo', 'fa-gauge-high'),
    ('planta', 'Operación en planta', 'fa-warehouse'),
    ('flota', 'Flota propia', 'fa-truck'),
    ('disponibilidad', 'Disponibilidad', 'fa-screwdriver-wrench'),
]
# Filtros que no aplican a la flota propia (jornadas e incidencias no tienen cliente, motivo ni puntualidad).
FILTROS_SOLO_PLANTA = ('razon_social', 'motivo', 'dia', 'hora', 'llegada', 'rango')
# Turno de la flota propia (día o noche): solo aplica a la pestaña «Flota propia» y al resumen de la flota.
TURNOS = {'DIA': 'Turno día', 'NOCHE': 'Turno noche'}


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
        'turno': params.get('turno') if params.get('turno') in TURNOS else None,
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
        'llegada': lambda v: f"Puntualidad: {ETIQUETAS_LLEGADA[v]}",
        'rango': _etiqueta_rango,
        'turno': lambda v: TURNOS[v],
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
    vista = request.GET.get('vista') if request.GET.get('vista') in dict((v, e) for v, e, _ in VISTAS) else 'resumen'

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
        tramo.prolongada = tramo.es_programado and tramo.minutos > config.umbral_prolongada_min

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
    flota = _contexto_flota(desde, hasta, desde_anterior, hasta_anterior, filtros, ahora, params)
    conclusiones_planta = indicadores.conclusiones(actuales, variacion, mapa, rankings, comparativo, segun_llegada, config)
    tarjetas = _tarjetas(actuales, variacion, config, len(dentro), sum(c.capacidad_simultanea for c in centros), un_dia)
    return render(request, 'accesos/dashboard/dashboard.html', {
        'vista': vista, 'vistas': [{'clave': v, 'etiqueta': e, 'icono': i, 'url': _url(params, vista=v)}
                                   for v, e, i in VISTAS],
        'filtros_solo_planta': [c for c in _chips(filtros, params) if c['clave'] in FILTROS_SOLO_PLANTA],
        'flota': flota, 'aviso_operativo': indicadores_flota.AVISO_OPERATIVO,
        'resumen': _resumen(desde, hasta, hoy, ahora, filtros, config, params, actuales, variacion, flota, dentro,
                            centros, conclusiones_planta) if vista == 'resumen' else None,
        'desde': desde, 'hasta': hasta, 'un_dia': un_dia, 'es_hoy': un_dia and desde == hoy,
        'fecha_larga': tiempo.fecha_larga(desde), 'hasta_larga': tiempo.fecha_larga(hasta),
        'recortado': recortado, 'max_rango': MAX_RANGO_DIAS,
        'desde_anterior': desde_anterior, 'hasta_anterior': hasta_anterior,
        'rangos': _rangos_rapidos(hoy, params), 'chips': _chips(filtros, params),
        'limpiar_url': _url(params, **{clave: None for clave in CLAVES_FILTRO}),
        'filtros': filtros, 'config': config, 'limite_demora': indicadores.limite_demora(config),
        'cds': CentroDistribucion.objects.filter(activo=True), 'tipos': Programacion.Tipo.choices,
        'tamanos': TamanoVehiculo.objects.all(), 'hora_actual': ahora,
        'kpis': actuales, 'tarjetas': tarjetas,
        'conclusiones': conclusiones_planta,
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
            'flota': flota['datos'],
        },
    })


def _por_turno(lista, ahora, internas):
    """Indicadores clave del turno día frente al turno noche (cada jornada se mide con sus propios horarios)."""
    filas = []
    for turno, etiqueta in TURNOS.items():
        del_turno = [j for j in lista if j.turno == turno]
        k = indicadores_flota.kpis(del_turno, ahora, [p for p in internas if p.jornada_id and p.jornada.turno == turno])
        filas.append({'turno': turno, 'etiqueta': etiqueta, 'k': k})
    return filas


def _contexto_flota(desde, hasta, desde_anterior, hasta_anterior, filtros, ahora, params):
    """Indicadores de la flota propia y de la disponibilidad (pestañas «Flota propia» y «Disponibilidad»)."""
    un_dia = desde == hasta
    lista = indicadores_flota.jornadas_del_periodo(desde, hasta, filtros)
    internas = indicadores_flota.programaciones_internas(desde, hasta, filtros)
    hay_noche = any(j.es_noche for j in lista)
    actuales = indicadores_flota.kpis(lista, ahora, internas)
    anterior = indicadores_flota.jornadas_del_periodo(desde_anterior, hasta_anterior, filtros)
    anteriores = indicadores_flota.kpis(anterior, ahora,
                                        indicadores_flota.programaciones_internas(desde_anterior, hasta_anterior, filtros))
    disp = indicadores_flota.disponibilidad(desde, hasta, filtros, ahora)
    disp_anterior = indicadores_flota.disponibilidad(desde_anterior, hasta_anterior, filtros, ahora)
    ranking_placas = indicadores_flota.ranking(lista, 'placa')
    return {
        'kpis': actuales, 'variacion': indicadores.variacion(actuales, anteriores),
        'disponibilidad': disp,
        'variacion_disponibilidad': indicadores.variacion(
            {k: disp[k] for k in ('pct_disponibilidad', 'averias', 'dias_unidad_fuera')},
            {k: disp_anterior[k] for k in ('pct_disponibilidad', 'averias', 'dias_unidad_fuera')}),
        'conclusiones': indicadores_flota.conclusiones(actuales, ranking_placas, disp),
        'jornada_efectiva_texto': servicios.formato_duracion(actuales['jornada_efectiva']),
        'cambios': _cambios(indicadores.variacion(actuales, anteriores), 'el día anterior' if un_dia else 'el periodo anterior',
                            {'pct_pre_ruta_a_tiempo': (' pts', False), 'pct_cierre_en_horario': (' pts', False),
                             'fuera_de_horario': ('', True), 'pct_rutas_cumplidas': (' pts', False),
                             'pct_salidas_a_tiempo': (' pts', False)}),
        'cambios_disp': _cambios(
            indicadores.variacion({k: disp[k] for k in ('pct_disponibilidad', 'averias', 'dias_unidad_fuera')},
                                  {k: disp_anterior[k] for k in ('pct_disponibilidad', 'averias', 'dias_unidad_fuera')}),
            'el día anterior' if un_dia else 'el periodo anterior',
            {'pct_disponibilidad': (' pts', False), 'averias': ('', True), 'dias_unidad_fuera': ('', True)}),
        'referencia': 'el día anterior' if desde == hasta else 'el periodo anterior',
        'gantt_disponible': un_dia,
        # Selector de turno (Todos / Día / Noche) y comparativo de los dos turnos si en el periodo hubo turno noche.
        'turnos': [{'etiqueta': 'Todos', 'url': _url(params, turno=None), 'activo': not filtros.get('turno')}]
                  + [{'etiqueta': etiqueta, 'url': _url(params, turno=turno), 'activo': filtros.get('turno') == turno}
                     for turno, etiqueta in TURNOS.items()],
        'por_turno': _por_turno(lista, ahora, internas) if hay_noche and not filtros.get('turno') else None,
        'gantt_noche_disponible': un_dia and filtros.get('turno') != jornadas.DIA,
        'datos': {
            'gantt': (indicadores_flota.gantt(hasta, ahora, filtros)
                      if un_dia and filtros.get('turno') != jornadas.NOCHE else None),
            'gantt_noche': (indicadores_flota.gantt(hasta, ahora, filtros, jornadas.NOCHE)
                            if un_dia and filtros.get('turno') != jornadas.DIA else None),
            'matriz': None if un_dia else indicadores_flota.matriz(lista, desde, hasta, ahora),
            'retorno': indicadores_flota.distribucion_retorno(lista),
            'pre_ruta': indicadores_flota.distribucion_pre_ruta(lista),
            'tendencia': None if un_dia else indicadores_flota.tendencia(lista, desde, hasta, ahora),
            'ranking_placa': ranking_placas,
            'ranking_conductor': indicadores_flota.ranking(lista, 'conductor'),
            'disponibilidad_tendencia': disp['tendencia'],
            'por_tipo': disp['por_tipo'],
        },
    }


def _cambios(variacion, referencia, claves):
    """{clave: {'texto', 'mejora'}} para las claves indicadas: (unidad, menor_es_mejor)."""
    resultado = {}
    for clave, (unidad, menor_es_mejor) in claves.items():
        texto, mejora = _cambio(variacion.get(clave), unidad, menor_es_mejor, referencia)
        resultado[clave] = {'texto': texto, 'mejora': mejora}
    return resultado


def _formato(valor, unidad=''):
    if valor is None:
        return '—'
    return f"{valor:g}{unidad}"


def _serie(dias, funcion):
    return [funcion(d) for d in dias]


def _resumen(desde, hasta, hoy, ahora, filtros, config, params, k_planta, v_planta, flota, dentro, centros,
             conclusiones_planta):
    """Resumen ejecutivo: 8 indicadores (con tendencia de los últimos 14 días que terminan en «hasta»),
    alertas en vivo, conclusiones y calendario de 5 semanas."""
    inicio_serie = hasta - timedelta(days=13)
    no_laborables = jornadas.no_laborables_entre(inicio_serie - timedelta(days=21), hasta)
    dias = [inicio_serie + timedelta(days=i) for i in range(14) if inicio_serie + timedelta(days=i) not in no_laborables]
    tramos = indicadores.tramos_del_periodo(inicio_serie, hasta, filtros, config)
    lista = indicadores_flota.jornadas_del_periodo(inicio_serie, hasta, filtros)
    internas = indicadores_flota.programaciones_internas(inicio_serie, hasta, filtros)
    por_dia_tramos = {d: [t for t in tramos if t.fecha_local_entrada == d] for d in dias}
    por_dia_jornadas = {d: [j for j in lista if j.fecha == d] for d in dias}
    k_dia = {d: indicadores.kpis(por_dia_tramos[d], [], config, hoy) for d in dias}
    f_dia = {d: indicadores_flota.kpis(por_dia_jornadas[d], ahora, [p for p in internas if p.fecha_turno == d]) for d in dias}
    tendencia_disp = indicadores_flota.disponibilidad(inicio_serie, hasta, filtros, ahora)['tendencia']
    disp_dia = dict(zip(tendencia_disp['fechas'], tendencia_disp['valores']))
    kf, vf = flota['kpis'], flota['variacion']
    disp, vd = flota['disponibilidad'], flota['variacion_disponibilidad']
    referencia = 'el día anterior' if desde == hasta else 'el periodo anterior'
    capacidad = sum(c.capacidad_simultanea for c in centros)

    def tarjeta(titulo, valor, detalle, icono, variacion=None, unidad='', menor_es_mejor=False, serie=None,
                vista='resumen', alerta=False):
        texto, mejora = _cambio(variacion, unidad, menor_es_mejor, referencia) if variacion is not None else (None, None)
        return {'titulo': titulo, 'valor': valor, 'detalle': detalle, 'icono': icono, 'variacion': texto,
                'mejora': mejora, 'serie': serie or [], 'url': _url(params, vista=vista), 'alerta': alerta,
                'escala': 'pct' if unidad == ' pts' else 'valor'}

    tarjetas = [
        tarjeta('Ocupación ahora', f"{len(dentro)} / {capacidad}", 'vehículos dentro frente a la capacidad', 'fa-warehouse',
                vista='planta', alerta=capacidad and len(dentro) >= 0.9 * capacidad),
        tarjeta('Rutas cumplidas', _formato(kf.get('pct_rutas_cumplidas'), ' %'),
                f"{kf.get('rutas_atendidas', 0)} de {kf.get('rutas', 0)} rutas INTERNO (días terminados)"
                + (f" · {kf['pct_salidas_a_tiempo']:g} % salió a tiempo" if kf.get('salidas') else ''), 'fa-route',
                vf.get('pct_rutas_cumplidas'), ' pts', serie=_serie(dias, lambda d: f_dia[d].get('pct_rutas_cumplidas')),
                vista='flota'),
        tarjeta('Permanencia promedio', servicios.formato_duracion(k_planta['permanencia_promedio']),
                f"visitas programadas · P90 {servicios.formato_duracion(k_planta['permanencia_p90'])}", 'fa-stopwatch',
                v_planta.get('permanencia_promedio'), ' min', menor_es_mejor=True,
                serie=_serie(dias, lambda d: k_dia[d]['permanencia_promedio']), vista='planta'),
        tarjeta('Puntualidad en planta', _formato(k_planta['pct_puntual'], ' %'),
                f"llegada (clientes y proveedores) o salida (rutas INTERNO) · desde {config.tolerancia_puntualidad_min} "
                "min tarde es retraso", 'fa-calendar-check',
                v_planta.get('pct_puntual'), ' pts', serie=_serie(dias, lambda d: k_dia[d]['pct_puntual']), vista='planta'),
        tarjeta('Pre-Ruta a tiempo', _formato(kf['pct_pre_ruta_a_tiempo'], ' %'),
                f"salida a ruta en {flota['datos']['pre_ruta']['meta']} min o menos · promedio "
                f"{servicios.formato_duracion(kf['pre_ruta_promedio'])}", 'fa-sun', vf.get('pct_pre_ruta_a_tiempo'), ' pts',
                serie=_serie(dias, lambda d: f_dia[d]['pct_pre_ruta_a_tiempo']), vista='flota'),
        tarjeta('Cierre en horario', _formato(kf['pct_cierre_en_horario'], ' %'),
                f"retornos (Post-Ruta) hasta el cierre + tolerancia · {kf['cierres']} retornos", 'fa-flag-checkered',
                vf.get('pct_cierre_en_horario'), ' pts', serie=_serie(dias, lambda d: f_dia[d]['pct_cierre_en_horario']),
                vista='flota'),
        tarjeta('Retornos fuera de horario', str(kf['fuera_de_horario']),
                f"{servicios.formato_duracion(kf['minutos_sobre_cierre'])} sobre el horario de referencia en total",
                'fa-clock',
                vf.get('fuera_de_horario'), '', menor_es_mejor=True,
                serie=_serie(dias, lambda d: f_dia[d]['fuera_de_horario']), vista='flota', alerta=kf['fuera_de_horario'] > 0),
        tarjeta('Disponibilidad de flota', _formato(disp['pct_disponibilidad'], ' %'),
                f"{disp['no_disponibles_ahora']} no disponible{'s' if disp['no_disponibles_ahora'] != 1 else ''} ahora · "
                f"{disp['averias']} avería{'s' if disp['averias'] != 1 else ''}", 'fa-screwdriver-wrench',
                vd.get('pct_disponibilidad'), ' pts', serie=_serie([d for d in dias if d in disp_dia], disp_dia.get),
                vista='disponibilidad', alerta=disp['no_disponibles_ahora'] > 0),
    ]

    alertas = []
    if desde <= hoy <= hasta:
        for j in jornadas.jornadas_en_vivo(ahora, cd_ids={filtros['cd']} if filtros.get('cd') else None):
            if j.estado_v.codigo in ('PRE_RUTA_EXCEDIDA', 'SIN_RETORNO'):
                alertas.append({'icono': 'fa-sun' if j.estado_v.codigo == 'PRE_RUTA_EXCEDIDA' else 'fa-clock',
                                'nivel': 'aviso' if j.estado_v.codigo == 'PRE_RUTA_EXCEDIDA' else 'critico',
                                'texto': f"{j.transporte.placa}{' (turno noche)' if j.es_noche else ''} · "
                                         f"{j.estado_v.etiqueta} · {j.conductor.nombre_completo}"})
        for aviso in jornadas.avisos_flota(ahora):
            alertas.append({'icono': aviso.icono, 'nivel': 'serio', 'texto': f"{aviso.titulo} · {aviso.texto}"})
        for cd in centros:
            dentro_cd = sum(1 for t in dentro if t.cd_id == cd.pk)
            if dentro_cd >= 0.9 * cd.capacidad_simultanea:
                alertas.append({'icono': 'fa-warehouse', 'nivel': 'critico' if dentro_cd > cd.capacidad_simultanea else 'aviso',
                                'texto': f"CD {cd}: {dentro_cd} vehículos dentro (capacidad {cd.capacidad_simultanea})"})
        prolongadas = [t for t in dentro if t.prolongada]
        if prolongadas:
            alertas.append({'icono': 'fa-hourglass-end', 'nivel': 'aviso',
                            'texto': f"{len(prolongadas)} visita{'s' if len(prolongadas) != 1 else ''} con permanencia "
                                     f"prolongada: {', '.join(t.transporte.placa for t in prolongadas[:5])}"})

    # Calendario: 5 semanas (lunes a domingo) que terminan en la semana de «hasta».
    fin = hasta + timedelta(days=6 - hasta.weekday())
    inicio = fin - timedelta(days=34)
    cal_jornadas = indicadores_flota.jornadas_del_periodo(inicio, min(fin, hoy), filtros)
    cal_no_laborables = jornadas.no_laborables_entre(inicio, fin)
    semanas = []
    for s_i in range(5):
        semana = []
        for d_i in range(7):
            dia = inicio + timedelta(days=s_i * 7 + d_i)
            celda = {'fecha': dia, 'url': _url(params, desde=dia.isoformat(), hasta=dia.isoformat()),
                     'seleccionado': desde == hasta == dia, 'futuro': dia > hoy}
            if dia in cal_no_laborables:
                celda.update(clase='sin', texto=f"Sin actividad ({cal_no_laborables[dia]})")
            elif dia <= hoy:
                k = indicadores_flota.kpis([j for j in cal_jornadas if j.fecha == dia], ahora)
                pct = k['pct_cierre_en_horario']
                if pct is None:
                    celda.update(clase='vacio', texto='Sin retornos registrados')
                else:
                    celda.update(clase='ok' if pct >= 95 else ('aviso' if pct >= 80 else 'critico'),
                                 texto=f"{pct:g} % en horario · {k['fuera_de_horario']} fuera de horario", pct=pct)
            else:
                celda.update(clase='futuro', texto='Próximo')
            semana.append(celda)
        semanas.append(semana)
    return {'tarjetas': tarjetas, 'alertas': alertas, 'semanas': semanas,
            'conclusiones': flota['conclusiones'][:3] + conclusiones_planta[:3],
            'dias_semana': [d[:2] for d in tiempo.DIAS_SEMANA]}


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
        tarjeta('Puntualidad', porcentaje(k['pct_puntual']),
                f"llegada a la cita; rutas INTERNO: salida · desde {config.tolerancia_puntualidad_min} min tarde es retraso",
                'pct_puntual', ' pts',
                menor_es_mejor=False, icono='fa-calendar-check'),
        tarjeta('Citas no atendidas', porcentaje(k['pct_no_atendidas']),
                f"{k['citas_no_atendidas']} de {k['citas_programadas']} (días terminados)", 'pct_no_atendidas', ' pts',
                icono='fa-calendar-xmark'),
        tarjeta('Permanencias prolongadas', str(k['prolongadas']),
                f"más de {config.umbral_prolongada_min // 60} h · {k['horas_en_planta']:g} h en planta en total",
                'prolongadas', icono='fa-hourglass-end'),
        tarjeta('Dentro ahora', f"{dentro_ahora} / {capacidad}", 'vehículos frente a la capacidad', icono='fa-warehouse'),
    ]
