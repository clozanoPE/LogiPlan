"""
Indicadores de la jornada de la flota propia y de la disponibilidad de la flota (F6.3), para el
Dashboard (pestañas «Flota propia», «Disponibilidad» y el «Resumen ejecutivo») y la pantalla de TV.

Como en indicadores.py, todo se calcula en Python sobre los campos guardados (sin funciones de fecha de
MySQL). Los días no laborables (domingos y feriados) se muestran como «Sin actividad» y no entran en
promedios ni denominadores.

IMPORTANTE (decisión del usuario): los minutos fuera de horario (sobre el cierre operativo más su tolerancia,
16:15) son un indicador OPERATIVO del retorno del VEHÍCULO; no son un registro de asistencia ni sustentan horas
extra del personal. Por eso se muestran por placa y nunca se suman por conductor.

En las rutas INTERNO la hora programada es la de SALIDA del CD: «salidas a tiempo» compara cada salida a ruta
(la de la Pre-Ruta, que atiende la primera ruta, y la de cada visita) con su hora programada.
"""
from collections import Counter, defaultdict
from datetime import time, timedelta
from statistics import mean

from django.db.models import Q

from . import jornadas, servicios, tiempo
from .indicadores import _p90, _porcentaje, _promedio
from .models import ConfiguracionAccesos, Incidencia, Jornada, Programacion, Tramo, Transporte

Estado = Jornada.Estado
AVISO_OPERATIVO = ("Indicador operativo del retorno del vehículo al CD. No es un registro de asistencia ni sustenta "
                   "horas extra del personal.")


# --- DATOS DEL PERIODO ---

def jornadas_del_periodo(desde, hasta, filtros):
    """Jornadas no anuladas que empiezan en el rango, con los filtros que aplican a la flota (CD, placa, conductor,
    tamaño y turno). Un filtro de tipo distinto de INTERNO deja la flota vacía."""
    if filtros.get('tipo') and filtros['tipo'] != Programacion.Tipo.INTERNO:
        return []
    qs = (Jornada.objects.filter(fecha__range=(desde, hasta)).exclude(estado=Estado.ANULADA)
          .select_related('transporte__tamano', 'conductor', 'cd', 'incidencia__tipo'))
    if filtros.get('turno'):
        qs = qs.filter(turno=filtros['turno'])
    if filtros.get('cd'):
        qs = qs.filter(cd_id=filtros['cd'])
    if filtros.get('placa'):
        qs = qs.filter(transporte__placa_normalizada=filtros['placa'])
    if filtros.get('conductor'):
        qs = qs.filter(conductor_id=filtros['conductor'])
    if filtros.get('tamano'):
        qs = qs.filter(transporte__tamano_id=filtros['tamano'])
    lista = list(qs.order_by('fecha', 'transporte__placa_normalizada', 'turno'))
    salidas = _salidas_por_jornada(desde, hasta, {j.pk for j in lista})
    for j in lista:
        j.calificacion = jornadas.calificacion_cierre(j)
        j.minutos_pre_ruta = jornadas.minutos_pre_ruta(j)
        j.minutos_sobre_cierre = jornadas.minutos_sobre_cierre(j)
        j.salidas, j.retrasos = salidas.get(j.pk, ([], []))
    return lista


def _salidas_por_jornada(desde, hasta, ids):
    """{jornada_id: (desviaciones de salida, las que son retraso)} de las rutas INTERNO ya salidas, en una sola
    consulta: salida − hora programada de cada ruta atendida (la primera, por la salida de la Pre-Ruta)."""
    tolerancia = ConfiguracionAccesos.obtener().tolerancia_puntualidad_min
    resultado = defaultdict(lambda: ([], []))
    filas = (Tramo.objects.filter(jornada__fecha__range=(desde, hasta), vigente=True, programacion__isnull=False,
                                  desviacion_salida_min__isnull=False)
             .values_list('jornada_id', 'desviacion_salida_min'))
    for jornada_id, desviacion in filas:
        if jornada_id not in ids:
            continue
        todas, retrasos = resultado[jornada_id]
        todas.append(desviacion)
        if servicios.puntualidad(desviacion, tolerancia) == servicios.RETRASADO:
            retrasos.append(desviacion)
    return resultado


def _terminadas(lista, ahora):
    """Jornadas cuyo turno ya terminó o que ya cerraron (las que siguen en curso aún no se califican)."""
    return [j for j in lista if jornadas.turno_terminado(j, ahora) or j.estado == Estado.CERRADA]


def kpis(lista, ahora, programaciones_internas=None):
    hoy = ahora.date()
    con_salida = [j for j in lista if j.primera_salida]
    pre_medibles = [j for j in con_salida if j.minutos_pre_ruta is not None]
    a_tiempo = sum(1 for j in pre_medibles if j.minutos_pre_ruta <= j.meta_pre_ruta_min)
    minutos_pre = [j.minutos_pre_ruta for j in pre_medibles]
    cierres = [j for j in lista if j.calificacion in (jornadas.ANTICIPADO, jornadas.EN_HORARIO, jornadas.FUERA_DE_HORARIO)]
    en_horario = sum(1 for j in cierres if j.calificacion != jornadas.FUERA_DE_HORARIO)
    fuera = [j for j in cierres if j.calificacion == jornadas.FUERA_DE_HORARIO]
    efectiva = [int((j.post_ruta_en - j.inicio_real).total_seconds() // 60) for j in cierres
                if j.inicio_real and j.post_ruta_en]
    salidas = [d for j in lista for d in getattr(j, 'salidas', [])]
    retrasos = [d for j in lista for d in getattr(j, 'retrasos', [])]
    resultado = {
        'jornadas': len(lista),
        'con_salida': len(con_salida),
        'pct_pre_ruta_a_tiempo': _porcentaje(a_tiempo, len(pre_medibles)),
        'pre_ruta_promedio': _promedio(minutos_pre),
        'pre_ruta_p90': _p90(minutos_pre),
        'cierres': len(cierres),
        'pct_cierre_en_horario': _porcentaje(en_horario, len(cierres)),
        'fuera_de_horario': len(fuera),
        'minutos_sobre_cierre': sum(j.minutos_sobre_cierre for j in fuera),
        'anticipados': sum(1 for j in cierres if j.calificacion == jornadas.ANTICIPADO),
        'por_averia': sum(1 for j in lista if j.calificacion == jornadas.POR_AVERIA),
        'sin_post_ruta': sum(1 for j in lista if j.estado == Estado.EN_RUTA and jornadas.turno_terminado(j, ahora)),
        'no_salio': sum(1 for j in lista if j.estado == Estado.PLANIFICADA and jornadas.vencida_sin_salir(j, ahora)),
        'jornada_efectiva': _promedio(efectiva),
        'salidas': len(salidas),
        'salidas_retrasadas': len(retrasos),
        'pct_salidas_a_tiempo': _porcentaje(len(salidas) - len(retrasos), len(salidas)),
        'retraso_salida_promedio': _promedio(retrasos),
    }
    if programaciones_internas is not None:
        resultado.update(rutas_cumplidas(programaciones_internas, hoy))
    return resultado


def programaciones_internas(desde, hasta, filtros):
    """Rutas INTERNO del rango (incluidas las canceladas por avería, que cuentan como no cumplidas). Las de un turno
    noche cuentan en la fecha en que empieza su turno (la de las 02:00 es de la noche anterior)."""
    if filtros.get('tipo') and filtros['tipo'] != Programacion.Tipo.INTERNO:
        return []
    qs = (Programacion.objects.filter(Q(jornada__isnull=True, fecha__range=(desde, hasta))
                                      | Q(jornada__fecha__range=(desde, hasta)), tipo=Programacion.Tipo.INTERNO)
          .select_related('jornada'))
    for clave, campo in (('cd', 'cd_id'), ('placa', 'transporte__placa_normalizada'), ('conductor', 'conductor_id'),
                         ('tamano', 'transporte__tamano_id'), ('turno', 'jornada__turno')):
        if filtros.get(clave):
            qs = qs.filter(**{campo: filtros[clave]})
    lista = list(qs)
    for p in lista:
        p.fecha_turno = p.jornada.fecha if p.jornada_id else p.fecha
    return lista


def rutas_cumplidas(programaciones, hoy):
    """Rutas INTERNO atendidas ÷ (programadas no canceladas + canceladas por avería), en días ya terminados
    o de hoy (una ruta de hoy pendiente todavía puede cumplirse: solo cuentan las de días terminados; las de un turno
    noche, cuando termina su madrugada)."""
    cuentan = [p for p in programaciones
               if servicios.ultimo_dia(p) < hoy and (p.estado != Programacion.Estado.CANCELADA or p.incidencia_id)]
    atendidas = sum(1 for p in cuentan if p.estado in (Programacion.Estado.EN_PLANTA, Programacion.Estado.CERRADA))
    return {'rutas': len(cuentan), 'rutas_atendidas': atendidas, 'pct_rutas_cumplidas': _porcentaje(atendidas, len(cuentan)),
            'rutas_no_atendidas_cierre': sum(1 for p in cuentan if p.no_atendida_por_cierre),
            'rutas_canceladas_averia': sum(1 for p in cuentan if p.incidencia_id)}


# --- LÍNEA DE TIEMPO DEL DÍA (GANTT) ---

def gantt(fecha, ahora, filtros, turno=jornadas.DIA):
    """Una fila por jornada del turno (día o noche) que empieza en la fecha, con sus tramos (Pre-Ruta, visitas,
    Post-Ruta) en minutos desde la medianoche de esa fecha (el turno noche llega hasta la madrugada siguiente: más
    de 1440), las marcas de Pre-Ruta / meta / cierre / tolerancia y la hora actual si el turno está en curso."""
    noche = turno == jornadas.NOCHE
    filas = [j for j in jornadas.jornadas_del_dia(fecha, ahora, cd_ids={filtros['cd']} if filtros.get('cd') else None)
             if j.turno == turno
             and (not filtros.get('placa') or j.transporte.placa_normalizada == filtros['placa'])
             and (not filtros.get('conductor') or j.conductor_id == filtros['conductor'])
             and (not filtros.get('tamano') or j.transporte.tamano_id == filtros['tamano'])]
    if filtros.get('tipo') and filtros['tipo'] != Programacion.Tipo.INTERNO:
        filas = []
    tramos = defaultdict(list)
    for t in (Tramo.objects.filter(jornada__in=[j.pk for j in filas], vigente=True)
              .select_related('programacion').order_by('entrada_en')):
        tramos[t.jornada_id].append(t)
    medianoche = tiempo.combinar(fecha, time(0, 0))
    limite_eje = (48 if noche else 24) * 60
    fin_dia = medianoche + timedelta(minutes=limite_eje)
    config_actual = ConfiguracionAccesos.obtener()
    tolerancia = config_actual.tolerancia_puntualidad_min
    if noche:
        # El turno noche se dibuja «en vivo» desde una hora antes de su Pre-Ruta hasta una hora después de la
        # Pre-Ruta del día siguiente (después ya está cerrado: sin la marca «Ahora» ni el eje estirado).
        desde_vivo = tiempo.combinar(fecha, config_actual.hora_pre_ruta_noche) - timedelta(hours=1)
        hasta_vivo = tiempo.combinar(fecha + timedelta(days=1), config_actual.hora_pre_ruta) + timedelta(hours=1)
        es_hoy = desde_vivo <= ahora < hasta_vivo
    else:
        es_hoy = medianoche <= ahora < fin_dia  # el turno se dibuja «en vivo» (hasta ahora)

    def minuto(momento):
        return max(0, min(limite_eje, round((momento - medianoche).total_seconds() / 60)))

    datos = []
    extremos = [19 * 60, 24 * 60 + 7 * 60] if noche else [5 * 60 + 30, 19 * 60]
    for j in filas:
        segmentos = []
        for t in tramos[j.pk]:
            fin = t.salida_en or (ahora if es_hoy else fin_dia)
            if t.clase == Tramo.Clase.POST_RUTA and not t.salida_en:
                fin = min(ahora, fin_dia) if es_hoy else fin_dia
            if fin <= medianoche or t.entrada_en >= fin_dia:
                continue
            tipo = {'PRE_RUTA': 'pre', 'PROGRAMADO': 'visita', 'POST_RUTA': 'post'}[t.clase]
            etiqueta = {'pre': 'Pre-Ruta', 'visita': f"Ruta {t.programacion.hora:%H:%M}" if t.programacion_id else 'Visita',
                        'post': 'Post-Ruta (pernocta)'}[tipo]
            # Ruta INTERNO que atiende la salida (hora programada = salida) y cómo salió frente a ella.
            ruta = ''
            if t.programacion_id and t.desviacion_salida_min is not None:
                retraso = servicios.puntualidad(t.desviacion_salida_min, tolerancia) == servicios.RETRASADO
                ruta = (f"Sale a la ruta de las {t.programacion.hora:%H:%M}: "
                        f"{servicios.texto_desviacion(t.desviacion_salida_min)}{' (retraso)' if retraso else ''}")
            segmentos.append({'tipo': tipo, 'etiqueta': etiqueta, 'ruta': ruta,
                              'desde': minuto(max(t.entrada_en, medianoche)),
                              'hasta': minuto(min(fin, fin_dia)), 'abierto': not t.salida_en,
                              'entrada': f"{tiempo.a_lima(t.entrada_en):%H:%M}",
                              'salida': f"{tiempo.a_lima(t.salida_en):%H:%M}" if t.salida_en else 'sigue dentro'})
        # Pre-Ruta en curso (todavía no se graba): desde el inicio hasta ahora.
        if j.estado_v.codigo in ('EN_PRE_RUTA', 'PRE_RUTA_EXCEDIDA'):
            segmentos.insert(0, {'tipo': 'pre', 'etiqueta': 'Pre-Ruta en curso', 'ruta': '', 'desde': minuto(jornadas.inicio(j)),
                                 'hasta': minuto(ahora), 'abierto': True, 'entrada': f"{j.hora_inicio:%H:%M}",
                                 'salida': 'sigue dentro'})
        if segmentos:
            # La pernocta (Post-Ruta abierta) no estira el eje: se dibuja hasta el borde derecho.
            extremos += [min(s['desde'] for s in segmentos),
                         max(s['desde'] if s['tipo'] == 'post' else s['hasta'] for s in segmentos)]
        primera = j.primera_salida and minuto(j.primera_salida)
        regreso = j.post_ruta_en and minuto(j.post_ruta_en)
        fin_ruta = regreso or (minuto(ahora) if es_hoy and j.estado == Estado.EN_RUTA else None)
        datos.append({
            'placa': j.transporte.placa, 'placa_valor': j.transporte.placa_normalizada,
            'conductor': j.conductor.nombre_completo, 'cd': str(j.cd), 'estado': j.estado_v.etiqueta,
            'estado_codigo': j.estado_v.codigo, 'alerta': j.estado_v.alerta, 'segmentos': segmentos,
            'en_ruta': [primera, fin_ruta] if primera is not None and fin_ruta is not None else None,
            'pre_ruta_min': j.minutos_pre_ruta, 'exceso_min': j.minutos_sobre_cierre,
        })
    referencia = filas[0] if filas else None
    config = None if referencia else ConfiguracionAccesos.obtener()
    if referencia:
        hora_inicio, meta = referencia.hora_inicio, referencia.meta_pre_ruta_min
        cierre, tolerancia = referencia.hora_cierre, referencia.tolerancia_cierre_min
    elif noche:
        hora_inicio, meta = config.hora_pre_ruta_noche, config.meta_pre_ruta_noche_min
        cierre, tolerancia = config.hora_cierre_noche, config.tolerancia_cierre_noche_min
    else:
        hora_inicio, meta = config.hora_pre_ruta, config.meta_pre_ruta_min
        cierre, tolerancia = config.hora_cierre_operativo, config.tolerancia_cierre_min
    inicio_min = hora_inicio.hour * 60 + hora_inicio.minute
    # El cierre del turno noche es de madrugada del día siguiente.
    cierre_min = cierre.hour * 60 + cierre.minute + (24 * 60 if noche else 0)

    def hhmm(m):
        return f"{(m // 60) % 24:02d}:{m % 60:02d}"

    # La tolerancia queda a pocos minutos del cierre: comparten una sola etiqueta para no encimarse.
    marcas = [
        {'minuto': inicio_min, 'etiqueta': f"Pre-Ruta {hora_inicio:%H:%M} · meta {hhmm(inicio_min + meta)}"},
        {'minuto': inicio_min + meta, 'etiqueta': ''},
        {'minuto': cierre_min, 'etiqueta': f"Cierre {cierre:%H:%M} · tol. {hhmm(cierre_min + tolerancia)}"},
        {'minuto': cierre_min + tolerancia, 'etiqueta': '', 'critica': True},
    ]
    if es_hoy:
        extremos.append(minuto(ahora))
    desde_eje = max(0, (min(extremos) // 60) * 60)
    hasta_eje = min(limite_eje, -(-(max(extremos) + 30) // 60) * 60)
    return {'filas': datos, 'marcas': marcas, 'desde': desde_eje, 'hasta': hasta_eje,
            'ahora': minuto(ahora) if es_hoy else None, 'turno': turno,
            'titulo': f"Turno noche del {fecha:%d/%m}" if noche else f"Turno día del {fecha:%d/%m}"}


# --- MATRIZ PLACA × DÍA ---

CELDAS = {
    jornadas.ANTICIPADO: ('ok', 'Fin anticipado'),
    jornadas.EN_HORARIO: ('ok', 'En horario'),
    jornadas.FUERA_DE_HORARIO: ('critico', 'Fuera de horario'),
    jornadas.POR_AVERIA: ('serio', 'Cierre por avería'),
}


def matriz(lista, desde, hasta, ahora, maximo_dias=45):
    """Estado de la jornada de cada placa en cada día (últimos `maximo_dias` del rango). Días no laborables:
    «Sin actividad»."""
    inicio = max(desde, hasta - timedelta(days=maximo_dias - 1))
    dias = [inicio + timedelta(days=i) for i in range((hasta - inicio).days + 1)]
    no_laborables = jornadas.no_laborables_entre(inicio, hasta)
    por_placa = defaultdict(dict)
    nombres = {}
    for j in lista:
        if j.fecha < inicio:
            continue
        # Una fila por placa y turno: el turno noche de la placa va en su propia fila.
        clave = (j.transporte.placa_normalizada, j.turno)
        nombres[clave] = j.transporte.placa + (' · noche' if j.es_noche else '')
        if j.calificacion in CELDAS:
            clase, texto = CELDAS[j.calificacion]
        elif j.estado == Estado.EN_RUTA and jornadas.turno_terminado(j, ahora):
            clase, texto = 'aviso', 'Sin Post-Ruta'
        elif j.estado == Estado.PLANIFICADA and jornadas.vencida_sin_salir(j, ahora):
            clase, texto = 'aviso', 'No salió'
        else:
            clase, texto = 'curso', 'En curso'
        detalle = texto
        if j.minutos_sobre_cierre:
            detalle += f" (+{j.minutos_sobre_cierre} min sobre las {jornadas.limite_cierre(j):%H:%M})"
        if j.post_ruta_en:
            detalle += f" · retorno {tiempo.a_lima(j.post_ruta_en):%H:%M}"
        por_placa[clave][j.fecha.isoformat()] = {'clase': clase, 'texto': detalle}
    filas = [{'placa': nombres[c], 'valor': c[0], 'celdas': [por_placa[c].get(d.isoformat()) for d in dias]}
             for c in sorted(por_placa)]
    return {'dias': [{'fecha': d.isoformat(), 'etiqueta': f"{d:%d}", 'dia': tiempo.DIAS_SEMANA[d.weekday()][:2],
                      'no_laborable': no_laborables.get(d)} for d in dias], 'filas': filas}


# --- DISTRIBUCIONES ---

def distribucion_retorno(lista):
    """Retornos (Post-Ruta) por bloques de 15 min alrededor del cierre: cada barra lleva su banda. Con los dos
    turnos mezclados se muestra el turno día (sus retornos son de tarde; los del turno noche, de madrugada); con
    solo el turno noche, sus retornos (minutos desde la medianoche del día en que cierra, negativos antes de ella)."""
    cierres = [j for j in lista if j.calificacion in (jornadas.ANTICIPADO, jornadas.EN_HORARIO, jornadas.FUERA_DE_HORARIO)]
    de_dia = [j for j in cierres if not j.es_noche]
    cierres = de_dia or cierres
    if not cierres:
        return {'etiquetas': [], 'conteos': [], 'bandas': [], 'turno': None}
    referencia = cierres[0]
    cierre = referencia.hora_cierre.hour * 60 + referencia.hora_cierre.minute
    limite = cierre + referencia.tolerancia_cierre_min
    minutos = [round((j.post_ruta_en - tiempo.combinar(jornadas.fecha_cierre(j), time(0, 0))).total_seconds() // 60)
               for j in cierres]
    inicio = min(cierre - 120, (min(minutos) // 15) * 15)
    fin = max(limite + 60, (max(minutos) // 15 + 1) * 15)
    etiquetas, conteos, bandas = [], [], []
    for desde in range(inicio, fin, 15):
        hasta = desde + 15
        etiquetas.append(f"{(desde // 60) % 24:02d}:{desde % 60:02d}")
        conteos.append(sum(1 for m in minutos if desde <= m < hasta))
        if referencia.es_noche:
            bandas.append('en_horario' if desde < limite else 'fuera')
        else:
            bandas.append('anticipado' if hasta <= cierre else ('en_horario' if desde < limite else 'fuera'))
    return {'etiquetas': etiquetas, 'conteos': conteos, 'bandas': bandas, 'turno': referencia.turno}


RANGOS_PRE_RUTA = [(0, 30), (30, 45), (45, 60), (60, 75), (75, 90), (90, None)]


def distribucion_pre_ruta(lista):
    minutos = [j.minutos_pre_ruta for j in lista if j.minutos_pre_ruta is not None]
    meta = lista[0].meta_pre_ruta_min if lista else 60
    etiquetas, conteos, sobre = [], [], []
    for desde, hasta in RANGOS_PRE_RUTA:
        etiquetas.append(f"{desde}–{hasta} min" if hasta else f"Más de {desde} min")
        conteos.append(sum(1 for m in minutos if m >= desde and (hasta is None or m < hasta)))
        sobre.append(desde >= meta)
    return {'etiquetas': etiquetas, 'conteos': conteos, 'sobre_meta': sobre, 'meta': meta}


# --- TENDENCIA ---

def tendencia(lista, desde, hasta, ahora):
    """% de Pre-Ruta a tiempo y % de cierres en horario por día (rangos cortos) o por semana."""
    semanal = (hasta - desde).days >= 45
    grupos = defaultdict(list)
    for j in lista:
        clave = j.fecha - timedelta(days=j.fecha.weekday()) if semanal else j.fecha
        grupos[clave].append(j)
    no_laborables = jornadas.no_laborables_entre(desde, hasta)
    if semanal:
        inicio = desde - timedelta(days=desde.weekday())
        puntos = [inicio + timedelta(weeks=i) for i in range((hasta - inicio).days // 7 + 1)]
    else:
        puntos = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1) if desde + timedelta(days=i) not in no_laborables]
    pre, cierre, fuera, salidas = [], [], [], []
    for p in puntos:
        k = kpis(grupos.get(p, []), ahora)
        pre.append(k['pct_pre_ruta_a_tiempo'])
        cierre.append(k['pct_cierre_en_horario'])
        fuera.append(k['fuera_de_horario'])
        salidas.append(k['pct_salidas_a_tiempo'])
    return {'semanal': semanal, 'etiquetas': [(f"Sem. {p:%d/%m}" if semanal else f"{p:%d/%m}") for p in puntos],
            'valores': [p.isoformat() for p in puntos], 'pre_ruta': pre, 'cierre': cierre, 'fuera': fuera,
            'salidas': salidas}


# --- RANKINGS ---

def ranking(lista, dimension):
    """Por placa (con minutos fuera de horario) o por conductor (solo conteos y porcentajes)."""
    grupos = defaultdict(list)
    for j in lista:
        clave = ((j.transporte.placa_normalizada, j.transporte.placa) if dimension == 'placa'
                 else (j.conductor_id, j.conductor.nombre_completo))
        grupos[clave].append(j)
    filas = []
    for (valor, nombre), del_grupo in grupos.items():
        k = kpis(del_grupo, tiempo.ahora())
        fila = {'valor': valor, 'nombre': nombre, 'jornadas': k['jornadas'],
                'pre_ruta_promedio': k['pre_ruta_promedio'], 'pct_pre_ruta_a_tiempo': k['pct_pre_ruta_a_tiempo'],
                'pct_cierre_en_horario': k['pct_cierre_en_horario'], 'fuera_de_horario': k['fuera_de_horario'],
                'por_averia': k['por_averia'], 'pct_salidas_a_tiempo': k['pct_salidas_a_tiempo']}
        if dimension == 'placa':
            fila['minutos_sobre_cierre'] = k['minutos_sobre_cierre']
        filas.append(fila)
    return sorted(filas, key=lambda f: (-f['fuera_de_horario'], -(f['pre_ruta_promedio'] or 0), f['nombre']))


# --- DISPONIBILIDAD ---

def _dias_laborables(desde, hasta):
    no_laborables = jornadas.no_laborables_entre(desde, hasta)
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)
            if desde + timedelta(days=i) not in no_laborables]


def disponibilidad(desde, hasta, filtros, ahora):
    """Disponibilidad de la flota propia: días-unidad laborables sin avería ÷ días-unidad laborables, con su
    tendencia, averías por tipo, tiempo de reparación y ranking por placa. Solo hasta hoy."""
    hoy = ahora.date()
    hasta_real = min(hasta, hoy)
    flota = Transporte.objects.filter(es_interno=True, activo=True).select_related('tamano')
    if filtros.get('placa'):
        flota = flota.filter(placa_normalizada=filtros['placa'])
    if filtros.get('tamano'):
        flota = flota.filter(tamano_id=filtros['tamano'])
    if filtros.get('tipo') and filtros['tipo'] != Programacion.Tipo.INTERNO:
        flota = flota.none()
    flota = list(flota)
    ids = {t.pk for t in flota}
    incidencias = list(Incidencia.objects.filter(transporte_id__in=ids,
                                                inicio__lt=tiempo.combinar(hasta_real + timedelta(days=1), time(0, 0)))
                       .exclude(fin__lt=tiempo.combinar(desde, time(0, 0)))
                       .select_related('transporte', 'tipo', 'cd').order_by('inicio'))
    if filtros.get('cd'):
        incidencias = [i for i in incidencias if i.cd_id in (None, filtros['cd'])]
    dias = _dias_laborables(desde, hasta_real) if desde <= hasta_real else []

    def fuera(incidencia, dia):
        inicio = tiempo.a_lima(incidencia.inicio).date()
        fin = tiempo.a_lima(incidencia.fin).date() if incidencia.fin else hoy
        return inicio <= dia <= fin

    por_dia = []
    dias_fuera = Counter()
    for dia in dias:
        no_disponibles = {i.transporte_id for i in incidencias if fuera(i, dia)}
        for transporte_id in no_disponibles:
            dias_fuera[transporte_id] += 1
        por_dia.append((dia, len(flota) - len(no_disponibles)))
    total = len(flota) * len(dias)
    disponibles = sum(n for _, n in por_dia)
    en_periodo = [i for i in incidencias if desde <= tiempo.a_lima(i.inicio).date() <= hasta_real]
    cerradas = [i for i in en_periodo if i.fin]
    reparacion_horas = [(i.fin - i.inicio).total_seconds() / 3600 for i in cerradas]
    por_tipo = Counter(str(i.tipo) for i in en_periodo)
    averias_por_placa = Counter(i.transporte_id for i in en_periodo)
    ranking_placas = []
    for t in flota:
        n = averias_por_placa.get(t.pk, 0)
        if not n and not dias_fuera.get(t.pk):
            continue
        disponibles_t = len(dias) - dias_fuera.get(t.pk, 0)
        ranking_placas.append({'placa': t.placa, 'valor': t.placa_normalizada, 'averias': n,
                               'dias_fuera': dias_fuera.get(t.pk, 0),
                               'entre_averias': round(disponibles_t / n, 1) if n else None})
    ranking_placas.sort(key=lambda f: (-f['dias_fuera'], -f['averias'], f['placa']))
    canceladas = Programacion.objects.filter(incidencia__in=[i.pk for i in en_periodo]).count()
    abiertas = [i for i in incidencias if i.esta_abierta]
    return {
        'flota': len(flota), 'dias': len(dias),
        'pct_disponibilidad': _porcentaje(disponibles, total),
        'dias_unidad_fuera': total - disponibles,
        'no_disponibles_ahora': len(abiertas),
        'averias': len(en_periodo),
        'reparacion_promedio_h': round(mean(reparacion_horas), 1) if reparacion_horas else None,
        'canceladas_por_averia': canceladas,
        'afectadas_futuras': jornadas.programaciones_afectadas(hoy, ids).count(),
        'por_tipo': {'etiquetas': [t for t, _ in por_tipo.most_common()], 'conteos': [n for _, n in por_tipo.most_common()]},
        'tendencia': {'etiquetas': [f"{d:%d/%m}" for d, _ in por_dia], 'fechas': [d for d, _ in por_dia],
                      'valores': [_porcentaje(n, len(flota)) for _, n in por_dia]},
        'ranking': ranking_placas,
        'incidencias': incidencias,
        'linea': _linea_incidencias(incidencias, desde, hasta_real, hoy),
    }


def _linea_incidencias(incidencias, desde, hasta, hoy):
    """Barras de cada incidencia sobre el rango (en % del ancho), una fila por placa."""
    inicio_rango = tiempo.combinar(desde, time(0, 0))
    fin_rango = tiempo.combinar(hasta, time(0, 0)) + timedelta(days=1)
    total = (fin_rango - inicio_rango).total_seconds()
    filas = defaultdict(list)
    valores = {}
    for i in incidencias:
        valores[i.transporte.placa] = i.transporte.placa_normalizada
        desde_i = max(i.inicio, inicio_rango)
        hasta_i = min(i.fin or tiempo.ahora(), fin_rango)
        if hasta_i < desde_i or (hasta_i == desde_i and not i.esta_abierta):
            continue  # fuera del rango (una avería recién abierta se dibuja igual, con el ancho mínimo)
        filas[i.transporte.placa].append({
            'izquierda': round((desde_i - inicio_rango).total_seconds() / total * 100, 2),
            'ancho': max(0.8, round((hasta_i - desde_i).total_seconds() / total * 100, 2)),
            'abierta': i.esta_abierta, 'texto': f"{i.codigo} · {i.tipo} · {tiempo.a_lima(i.inicio):%d/%m %H:%M} → "
                                                f"{tiempo.a_lima(i.fin):%d/%m %H:%M}" if i.fin else
                                                f"{i.codigo} · {i.tipo} · desde {tiempo.a_lima(i.inicio):%d/%m %H:%M} (abierta)"})
    return [{'placa': p, 'valor': valores[p], 'barras': b} for p, b in sorted(filas.items())]


# --- CONCLUSIONES ---

def conclusiones(k, ranking_placas, disp):
    frases = []
    if k['cierres']:
        frases.append({'icono': 'fa-flag-checkered', 'alerta': (k['pct_cierre_en_horario'] or 0) < 90,
                       'texto': f"El {k['pct_cierre_en_horario']:g} % de los retornos (Post-Ruta) fue en horario; "
                                f"{k['fuera_de_horario']} fuera de horario."})
    if k.get('salidas'):
        texto = f"El {k['pct_salidas_a_tiempo']:g} % de las salidas a ruta fue a tiempo frente a su hora programada"
        if k['salidas_retrasadas']:
            texto += (f"; {k['salidas_retrasadas']} con retraso "
                      f"({servicios.formato_duracion(k['retraso_salida_promedio'])} en promedio)")
        frases.append({'icono': 'fa-route', 'alerta': (k['pct_salidas_a_tiempo'] or 0) < 80, 'texto': texto + '.'})
    if k['pre_ruta_promedio'] is not None:
        frases.append({'icono': 'fa-sun', 'alerta': (k['pct_pre_ruta_a_tiempo'] or 0) < 80,
                       'texto': f"La Pre-Ruta dura {servicios.formato_duracion(k['pre_ruta_promedio'])} en promedio; "
                                f"el {k['pct_pre_ruta_a_tiempo']:g} % sale dentro de la meta."})
    tardias = [f for f in ranking_placas if f['fuera_de_horario'] >= 3]
    if tardias:
        peor = tardias[0]
        frases.append({'icono': 'fa-truck', 'alerta': True,
                       'texto': f"La placa {peor['nombre']} volvió fuera de horario {peor['fuera_de_horario']} veces "
                                f"en el periodo."})
    lentas = sorted([f for f in ranking_placas if f['jornadas'] >= 3 and f['pre_ruta_promedio']],
                    key=lambda f: -f['pre_ruta_promedio'])
    if lentas and lentas[0]['pre_ruta_promedio'] > (k['pre_ruta_promedio'] or 0) + 10:
        frases.append({'icono': 'fa-hourglass-half', 'alerta': True,
                       'texto': f"La placa {lentas[0]['nombre']} es la que más tarda en salir a ruta: "
                                f"{servicios.formato_duracion(lentas[0]['pre_ruta_promedio'])} de Pre-Ruta en promedio."})
    if disp and disp['pct_disponibilidad'] is not None and disp['averias']:
        frases.append({'icono': 'fa-screwdriver-wrench', 'alerta': disp['pct_disponibilidad'] < 95,
                       'texto': f"Disponibilidad de la flota: {disp['pct_disponibilidad']:g} % "
                                f"({disp['averias']} avería{'s' if disp['averias'] != 1 else ''} en el periodo)."})
    return frases
