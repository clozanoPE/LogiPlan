"""
Indicadores del Dashboard de Gerencia (F5). Todo se calcula en Python sobre los tramos válidos
del rango, usando los campos ya copiados en cada tramo (fecha y hora local, minutos de
permanencia, desviación de llegada, tipo y tamaño). No se usan funciones de fecha de MySQL:
producción y local tienen distinta configuración de zonas horarias.

Solo los tramos cerrados (con salida) aportan permanencia; la puntualidad usa todos.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean, quantiles

from . import servicios, tiempo
from .models import Programacion, Tramo

DIMENSIONES = {
    'placa': 'Placa',
    'conductor': 'Conductor',
    'razon_social': 'Razón social',
    'motivo': 'Motivo',
    'tamano': 'Tamaño de vehículo',
    'tipo': 'Tipo de operación',
    'cd': 'CD',
}
TRAMOS_DE_PERMANENCIA = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 180), (180, 240), (240, None)]
DIAS_PARA_TENDENCIA_DIARIA = 45
MINIMO_TRAMOS_CONCLUSION = 3


def _promedio(valores):
    return round(mean(valores)) if valores else None


def _p90(valores):
    if not valores:
        return None
    if len(valores) == 1:
        return valores[0]
    return round(quantiles(valores, n=10, method='inclusive')[-1])


def _porcentaje(parte, total):
    return round(100 * parte / total, 1) if total else None


def limite_demora(config):
    return config.meta_permanencia_min or config.umbral_prolongada_min


def es_demora(minutos, config):
    """R23: sobre la meta de Gerencia; mientras no exista meta, se usa el umbral de prolongada."""
    return minutos is not None and minutos > limite_demora(config)


def _puntualidad(tramo, config):
    return servicios.puntualidad(tramo.desviacion_llegada_min, config.tolerancia_puntualidad_min)


# --- DATOS DEL PERIODO ---

# Filtros del dashboard. Todos se aplican a la vez a todos los paneles (filtrado cruzado):
# cd, tipo, tamano, placa (normalizada), conductor (id del que ingresó), razon_social, motivo,
# dia (0 = lunes), hora (0–23), llegada (ADELANTADO/PUNTUAL/RETRASADO), rango (índice de
# TRAMOS_DE_PERMANENCIA). «llegada» y «rango» solo existen en los tramos.

def tramos_del_periodo(desde, hasta, filtros, config=None):
    # Solo las visitas programadas: la Pre-Ruta y la Post-Ruta (con la pernocta) se miden aparte, en la
    # jornada de la flota propia; mezclarlas distorsionaría la permanencia y la puntualidad.
    tramos = (Tramo.objects.filter(vigente=True, clase=Tramo.Clase.PROGRAMADO,
                                   fecha_local_entrada__range=(desde, hasta))
              .select_related('cd', 'transporte', 'tamano_snapshot', 'programacion', 'conductor'))
    if filtros.get('cd'):
        tramos = tramos.filter(cd_id=filtros['cd'])
    if filtros.get('tipo'):
        tramos = tramos.filter(tipo_snapshot=filtros['tipo'])
    if filtros.get('tamano'):
        tramos = tramos.filter(tamano_snapshot_id=filtros['tamano'])
    if filtros.get('placa'):
        tramos = tramos.filter(transporte__placa_normalizada=filtros['placa'])
    if filtros.get('conductor'):
        tramos = tramos.filter(conductor_id=filtros['conductor'])
    if filtros.get('razon_social'):
        tramos = tramos.filter(programacion__razon_social=filtros['razon_social'])
    if filtros.get('motivo'):
        tramos = tramos.filter(programacion__motivo__iexact=filtros['motivo'])
    if filtros.get('dia') is not None:
        tramos = tramos.filter(fecha_local_entrada__iso_week_day=filtros['dia'] + 1)
    if filtros.get('hora') is not None:
        tramos = tramos.filter(hora_local_entrada__hour=filtros['hora'])
    tramos = list(tramos)
    if filtros.get('llegada') and config is not None:
        tramos = [t for t in tramos if _puntualidad(t, config) == filtros['llegada']]
    if filtros.get('rango') is not None:
        minimo, maximo = TRAMOS_DE_PERMANENCIA[filtros['rango']]
        tramos = [t for t in tramos if t.minutos_permanencia is not None and t.minutos_permanencia >= minimo
                  and (maximo is None or t.minutos_permanencia < maximo)]
    return tramos


def programaciones_del_periodo(desde, hasta, filtros):
    programaciones = servicios.activas().filter(fecha__range=(desde, hasta))
    if filtros.get('cd'):
        programaciones = programaciones.filter(cd_id=filtros['cd'])
    if filtros.get('tipo'):
        programaciones = programaciones.filter(tipo=filtros['tipo'])
    if filtros.get('tamano'):
        programaciones = programaciones.filter(transporte__tamano_id=filtros['tamano'])
    if filtros.get('placa'):
        programaciones = programaciones.filter(transporte__placa_normalizada=filtros['placa'])
    if filtros.get('conductor'):
        programaciones = programaciones.filter(conductor_id=filtros['conductor'])
    if filtros.get('razon_social'):
        programaciones = programaciones.filter(razon_social=filtros['razon_social'])
    if filtros.get('motivo'):
        programaciones = programaciones.filter(motivo__iexact=filtros['motivo'])
    if filtros.get('dia') is not None:
        programaciones = programaciones.filter(fecha__iso_week_day=filtros['dia'] + 1)
    if filtros.get('hora') is not None:
        programaciones = programaciones.filter(hora__hour=filtros['hora'])
    return list(programaciones)


# --- INDICADORES PRINCIPALES ---

def kpis(tramos, programaciones, config, hoy):
    cerrados = [t.minutos_permanencia for t in tramos if t.minutos_permanencia is not None]
    puntuales = sum(1 for t in tramos if _puntualidad(t, config) == servicios.PUNTUAL)
    # Citas: solo días ya terminados, porque una programación de hoy todavía puede atenderse.
    vencidas = [p for p in programaciones if p.fecha < hoy]
    no_atendidas = sum(1 for p in vencidas if p.estado == Programacion.Estado.PROGRAMADA)
    return {
        'tramos': len(tramos),
        'cerrados': len(cerrados),
        'permanencia_promedio': _promedio(cerrados),
        'permanencia_p90': _p90(cerrados),
        'horas_en_planta': round(sum(cerrados) / 60, 1) if cerrados else 0,
        'pct_demora': _porcentaje(sum(1 for m in cerrados if es_demora(m, config)), len(cerrados)),
        'pct_puntual': _porcentaje(puntuales, len(tramos)),
        'citas_programadas': len(vencidas),
        'citas_no_atendidas': no_atendidas,
        'pct_no_atendidas': _porcentaje(no_atendidas, len(vencidas)),
        'prolongadas': sum(1 for m in cerrados if m > config.umbral_prolongada_min),
    }


def variacion(actual, anterior):
    """Diferencia actual − anterior por indicador (None si falta alguno de los dos)."""
    return {clave: (round(actual[clave] - anterior[clave], 1)
                    if actual.get(clave) is not None and anterior.get(clave) is not None else None)
            for clave in actual}


# --- RANKINGS DE DEMORA ---

def _clave(tramo, dimension):
    """(valor del filtro, texto visible) del tramo para la dimensión: el valor es el que se usa
    al hacer clic en el ranking para filtrar todo el dashboard por esa fila."""
    if dimension == 'placa':
        return tramo.transporte.placa_normalizada, tramo.transporte.placa
    if dimension == 'conductor':
        return tramo.conductor_id, tramo.conductor.nombre_completo
    if dimension == 'motivo':
        motivo = tramo.programacion.motivo.strip().capitalize()
        return motivo, motivo
    if dimension == 'tamano':
        return tramo.tamano_snapshot_id, str(tramo.tamano_snapshot)
    if dimension == 'tipo':
        return tramo.tipo_snapshot, tramo.get_tipo_snapshot_display()
    if dimension == 'cd':
        return tramo.cd_id, str(tramo.cd)
    return tramo.programacion.razon_social, tramo.programacion.razon_social


def ranking(tramos, config, dimension, limite=15):
    """Quién o qué concentra la permanencia: por cada valor de la dimensión, tramos cerrados,
    promedio, P90, máximo, demoras y % de llegadas con retraso. Ordenado por promedio."""
    grupos = defaultdict(list)
    for t in tramos:
        if t.minutos_permanencia is not None:
            grupos[_clave(t, dimension)].append(t)
    filas = []
    for (valor, nombre), del_grupo in grupos.items():
        minutos = [t.minutos_permanencia for t in del_grupo]
        demoras = sum(1 for m in minutos if es_demora(m, config))
        retrasos = sum(1 for t in del_grupo if _puntualidad(t, config) == servicios.RETRASADO)
        filas.append({'nombre': nombre, 'valor': valor, 'tramos': len(del_grupo), 'promedio': _promedio(minutos),
                      'p90': _p90(minutos), 'maximo': max(minutos), 'total_horas': round(sum(minutos) / 60, 1),
                      'demoras': demoras, 'pct_demora': _porcentaje(demoras, len(del_grupo)),
                      'pct_retraso': _porcentaje(retrasos, len(del_grupo))})
    return sorted(filas, key=lambda f: (-f['promedio'], -f['tramos'], f['nombre']))[:limite]


def rankings(tramos, config):
    return {dimension: ranking(tramos, config, dimension) for dimension in DIMENSIONES}


# --- MAPA DE CALOR, DISTRIBUCIÓN, TENDENCIA ---

def mapa_de_calor(tramos, config):
    """Día de la semana × hora de llegada: llegadas, permanencia promedio y % de demoras por celda."""
    celdas = defaultdict(list)
    for t in tramos:
        celdas[(t.fecha_local_entrada.weekday(), t.hora_local_entrada.hour)].append(t)
    if not celdas:
        return {'horas': [], 'dias': list(tiempo.DIAS_SEMANA), 'celdas': []}
    horas = list(range(min(h for _, h in celdas), max(h for _, h in celdas) + 1))
    matriz = []
    for dia in range(7):
        fila = []
        for hora in horas:
            del_bloque = celdas.get((dia, hora), [])
            minutos = [t.minutos_permanencia for t in del_bloque if t.minutos_permanencia is not None]
            fila.append({'llegadas': len(del_bloque), 'promedio': _promedio(minutos),
                         'pct_demora': _porcentaje(sum(1 for m in minutos if es_demora(m, config)), len(minutos))})
        matriz.append(fila)
    return {'horas': horas, 'dias': list(tiempo.DIAS_SEMANA), 'celdas': matriz}


def distribucion(tramos, config):
    """Histograma de permanencia en rangos fijos; marca los rangos que superan el límite de demora."""
    cerrados = [t.minutos_permanencia for t in tramos if t.minutos_permanencia is not None]
    limite = limite_demora(config)
    etiquetas, conteos, sobre_limite = [], [], []
    for desde, hasta in TRAMOS_DE_PERMANENCIA:
        etiquetas.append(f"{desde}–{hasta} min" if hasta else f"Más de {desde} min")
        conteos.append(sum(1 for m in cerrados if m >= desde and (hasta is None or m < hasta)))
        sobre_limite.append(desde >= limite)
    return {'etiquetas': etiquetas, 'conteos': conteos, 'sobre_limite': sobre_limite}


def tendencia(tramos, desde, hasta, config):
    """Permanencia promedio en el tiempo: por hora de llegada si el rango es un solo día, por día
    en rangos cortos y por semana en rangos largos. Cada punto lleva el valor para filtrar al hacer clic."""
    if desde == hasta:
        unidad = 'hora'
    elif (hasta - desde).days < DIAS_PARA_TENDENCIA_DIARIA:
        unidad = 'dia'
    else:
        unidad = 'semana'
    grupos = defaultdict(list)
    for t in tramos:
        if t.minutos_permanencia is None:
            continue
        fecha = t.fecha_local_entrada
        clave = {'hora': t.hora_local_entrada.hour, 'dia': fecha,
                 'semana': fecha - timedelta(days=fecha.weekday())}[unidad]
        grupos[clave].append(t.minutos_permanencia)
    if unidad == 'hora':
        horas = [t.hora_local_entrada.hour for t in tramos]
        puntos = list(range(min(horas), max(horas) + 1)) if horas else []
        etiquetas = [f"{h:02d}:00" for h in puntos]
        valores = puntos
    elif unidad == 'dia':
        puntos = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
        etiquetas = [f"{p:%d/%m}" for p in puntos]
        valores = [p.isoformat() for p in puntos]
    else:
        inicio = desde - timedelta(days=desde.weekday())
        puntos = [inicio + timedelta(weeks=i) for i in range((hasta - inicio).days // 7 + 1)]
        etiquetas = [f"Sem. {p:%d/%m}" for p in puntos]
        valores = [p.isoformat() for p in puntos]
    return {
        'unidad': unidad,
        'diaria': unidad == 'dia',
        'etiquetas': etiquetas,
        'valores': valores,
        'promedio': [_promedio(grupos.get(p, [])) for p in puntos],
        'tramos': [len(grupos.get(p, [])) for p in puntos],
        'pct_demora': [_porcentaje(sum(1 for m in grupos.get(p, []) if es_demora(m, config)), len(grupos.get(p, [])))
                       for p in puntos],
    }


# --- PUNTUALIDAD ---

ORDEN_PUNTUALIDAD = (servicios.ADELANTADO, servicios.PUNTUAL, servicios.RETRASADO)


def puntualidad_por(tramos, config, clave):
    """% adelantado / puntual / retrasado por CD o por tipo de operación."""
    conteos = defaultdict(Counter)
    for t in tramos:
        grupo = (str(t.cd), t.cd_id) if clave == 'cd' else (t.get_tipo_snapshot_display(), t.tipo_snapshot)
        conteos[grupo][_puntualidad(t, config)] += 1
    claves = sorted(conteos)
    grupos = [nombre for nombre, _ in claves]
    resultado = {'grupos': grupos, 'valores': [valor for _, valor in claves], 'param': clave,
                 'totales': [sum(conteos[g].values()) for g in claves]}
    for estado in ORDEN_PUNTUALIDAD:
        resultado[estado] = [_porcentaje(conteos[g][estado], sum(conteos[g].values())) for g in claves]
    return resultado


def permanencia_segun_llegada(tramos, config):
    """¿Llegar tarde alarga la estadía? Permanencia promedio según la puntualidad de llegada."""
    grupos = defaultdict(list)
    for t in tramos:
        if t.minutos_permanencia is not None:
            grupos[_puntualidad(t, config)].append(t.minutos_permanencia)
    return {'categorias': list(ORDEN_PUNTUALIDAD),
            'promedio': [_promedio(grupos[c]) for c in ORDEN_PUNTUALIDAD],
            'tramos': [len(grupos[c]) for c in ORDEN_PUNTUALIDAD]}


def comparativo_cd(tramos, config):
    filas = []
    for fila in ranking(tramos, config, 'cd', limite=None):
        del_cd = [t for t in tramos if t.cd_id == fila['valor']]
        retrasos = [t.desviacion_llegada_min for t in del_cd if _puntualidad(t, config) == servicios.RETRASADO]
        fila['pct_puntual'] = _porcentaje(sum(1 for t in del_cd if _puntualidad(t, config) == servicios.PUNTUAL),
                                          len(del_cd))
        fila['retraso_promedio'] = _promedio(retrasos)
        filas.append(fila)
    return sorted(filas, key=lambda f: f['nombre'])


# --- DETALLE Y CONCLUSIONES ---

def detalle_demoras(tramos, config, limite=50):
    """Tramos sobre el límite, de mayor a menor, con las observaciones de Vigilancia."""
    demoras = [t for t in tramos if es_demora(t.minutos_permanencia, config)]
    return sorted(demoras, key=lambda t: -t.minutos_permanencia)[:limite], len(demoras)


def conclusiones(kpis_actuales, variacion_kpis, mapa, rankings_por_dimension, comparativo, segun_llegada, config):
    """Frases cortas para Gerencia, solo con hechos que los datos sostienen (mínimo de tramos)."""
    duracion = servicios.formato_duracion
    frases = []
    if kpis_actuales['permanencia_promedio'] is not None:
        texto = (f"La permanencia promedio fue de {duracion(kpis_actuales['permanencia_promedio'])} "
                 f"(el 10 % más lento superó {duracion(kpis_actuales['permanencia_p90'])}).")
        cambio = variacion_kpis.get('permanencia_promedio')
        if cambio:
            texto += f" Son {duracion(abs(cambio))} {'más' if cambio > 0 else 'menos'} que el periodo anterior."
        frases.append({'icono': 'fa-stopwatch', 'texto': texto, 'alerta': bool(cambio and cambio > 0)})

    bloques = [(dia, hora, celda) for dia, fila in enumerate(mapa['celdas'])
               for hora, celda in zip(mapa['horas'], fila) if celda['llegadas']]
    if bloques:
        dia, hora, celda = max(bloques, key=lambda b: b[2]['llegadas'])
        frases.append({'icono': 'fa-clock', 'alerta': False,
                       'texto': f"La hora pico es el {mapa['dias'][dia].lower()} entre las {hora:02d}:00 y las "
                                f"{hora + 1:02d}:00, con {celda['llegadas']} llegadas."})
        lentos = [b for b in bloques if b[2]['promedio'] is not None and b[2]['llegadas'] >= MINIMO_TRAMOS_CONCLUSION]
        if lentos:
            dia, hora, celda = max(lentos, key=lambda b: b[2]['promedio'])
            frases.append({'icono': 'fa-hourglass-half', 'alerta': es_demora(celda['promedio'], config),
                           'texto': f"La permanencia más alta se da en los ingresos del {mapa['dias'][dia].lower()} "
                                    f"de {hora:02d}:00 a {hora + 1:02d}:00: {duracion(celda['promedio'])} en promedio."})

    for dimension, etiqueta in (('placa', 'La placa'), ('conductor', 'El conductor'),
                                ('razon_social', 'La operación de')):
        candidatos = [f for f in rankings_por_dimension[dimension] if f['tramos'] >= MINIMO_TRAMOS_CONCLUSION]
        if candidatos:
            peor = candidatos[0]
            frases.append({'icono': 'fa-ranking-star', 'alerta': peor['demoras'] > 0,
                           'texto': f"{etiqueta} {peor['nombre']} tiene la mayor permanencia promedio: "
                                    f"{duracion(peor['promedio'])} en {peor['tramos']} tramos "
                                    f"({peor['demoras']} con demora)."})

    if len(comparativo) >= 2:
        lento, rapido = max(comparativo, key=lambda f: f['promedio']), min(comparativo, key=lambda f: f['promedio'])
        diferencia = lento['promedio'] - rapido['promedio']
        if diferencia > 0:
            frases.append({'icono': 'fa-warehouse', 'alerta': False,
                           'texto': f"En CD {lento['nombre']} los camiones permanecen {duracion(diferencia)} más "
                                    f"que en CD {rapido['nombre']} en promedio."})

    promedios = dict(zip(segun_llegada['categorias'], segun_llegada['promedio']))
    tramos_por = dict(zip(segun_llegada['categorias'], segun_llegada['tramos']))
    tarde, a_tiempo = promedios[servicios.RETRASADO], promedios[servicios.PUNTUAL]
    if (tarde is not None and a_tiempo is not None and tramos_por[servicios.RETRASADO] >= MINIMO_TRAMOS_CONCLUSION
            and tarde != a_tiempo):
        frases.append({'icono': 'fa-person-running', 'alerta': tarde > a_tiempo,
                       'texto': f"Los camiones que llegan retrasados permanecen {duracion(abs(tarde - a_tiempo))} "
                                f"{'más' if tarde > a_tiempo else 'menos'} que los que llegan puntuales."})
    return frases
