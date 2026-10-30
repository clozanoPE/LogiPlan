"""
Datos del modo TV de un CD (F6.4): lo que se ve en la pantalla grande, a distancia y sin tocar nada.
Lo usan la página (primer dibujo) y el endpoint JSON con el que la página se actualiza cada minuto.

Muestra placa y conductor (decisión del usuario). El exceso sobre el cierre operativo es un indicador
operativo del vehículo: en la TV solo se marca «Fuera de horario», sin minutos por persona.

Turno noche (15/10/2026): la flota de hoy incluye el turno noche de ayer mientras sigue en curso o si volvió hoy de
madrugada; «Flota propia» cuenta placas (una placa con turno día y noche cuenta una vez) con el detalle por turno, y
la línea de tiempo muestra el turno que corresponde a la hora (el turno noche desde una hora antes de su Pre-Ruta y
hasta la mañana siguiente).

Indicadores gerenciales (30/10/2026): la franja de 8 cifras y las páginas «Indicadores del turno» y «Últimos 7 días»
salen del MOTOR ÚNICO (indicadores_flota.jornadas_del_periodo + kpis) para el turno en foco (`turno_en_foco`): son
las mismas cifras del Dashboard › Flota y del Reporte de jornadas con fecha = día del turno, turno y CD. Los minutos
de Pre-Ruta y Post-Ruta y el km se muestran por placa, nunca por conductor (criterio laboral del 14/10/2026).
"""
from datetime import timedelta

from . import indicadores_flota, jornadas, kilometraje, servicios, tiempo
from .indicadores_flota import gantt
from .models import ConfiguracionAccesos, Incidencia, Jornada, Programacion, Tramo

GRUPOS = [
    ('por_iniciar', 'Por iniciar', ('POR_INICIAR', 'POR_LLEGAR')),
    ('pre_ruta', 'En Pre-Ruta', ('EN_PRE_RUTA', 'PRE_RUTA_EXCEDIDA')),
    ('en_ruta', 'En ruta', ('EN_RUTA', 'EN_VISITA', 'SIN_RETORNO')),
    ('retornaron', 'Retornaron', ('ANTICIPADO', 'EN_HORARIO', 'FUERA_DE_HORARIO', 'CERRADA', 'POR_AVERIA')),
]


def datos(cd, ahora):
    hoy = ahora.date()
    config = ConfiguracionAccesos.obtener()
    abiertos = list(Tramo.objects.filter(cd=cd, abierto=True)
                    .select_related('transporte', 'programacion', 'conductor').order_by('entrada_en'))
    dentro = []
    for t in abiertos:
        minutos = servicios.minutos_dentro(t, ahora)
        prolongada = t.es_programado and minutos > config.umbral_prolongada_min
        sobre_meta = t.es_programado and bool(config.meta_permanencia_min and minutos > config.meta_permanencia_min)
        dentro.append({
            'placa': t.transporte.placa, 'conductor': t.conductor.nombre_completo,
            'descripcion': t.programacion.razon_social if t.programacion_id else
            ('Flota propia · pernocta' if t.clase == Tramo.Clase.POST_RUTA else
             'Flota propia · retorno imprevisto' if t.es_imprevisto else 'Flota propia · Pre-Ruta'),
            'flota': t.es_de_jornada, 'entrada': f"{tiempo.a_lima(t.entrada_en):%H:%M}",
            'tiempo': servicios.formato_duracion(minutos), 'minutos': minutos,
            'nivel': 'critico' if prolongada else ('aviso' if sobre_meta else 'ok'),
        })
    programaciones = list(Programacion.objects.filter(cd=cd, fecha=hoy))
    estados = [servicios.estado_visible(p, hoy) for p in programaciones]
    del_dia = jornadas.jornadas_en_vivo(ahora, cd_ids={cd.pk})
    codigos = [j.estado_v.codigo for j in del_dia]
    no_disponibles = list(Incidencia.objects.filter(abierta=True).select_related('transporte', 'tipo'))

    alertas = []
    for j in del_dia:
        placa = f"{j.transporte.placa}{' (turno noche)' if j.es_noche else ''}"
        if j.estado_v.codigo == 'PRE_RUTA_EXCEDIDA':
            alertas.append({'nivel': 'aviso', 'icono': 'fa-sun',
                            'texto': f"{placa} sigue en Pre-Ruta pasada la meta · {j.conductor.nombre_completo}"})
        elif j.estado_v.codigo == 'SIN_RETORNO':
            alertas.append({'nivel': 'critico', 'icono': 'fa-clock',
                            'texto': f"{placa} sin retorno pasada la tolerancia · {j.conductor.nombre_completo}"})
    for d in dentro:
        if d['nivel'] == 'critico':
            alertas.append({'nivel': 'aviso', 'icono': 'fa-hourglass-end',
                            'texto': f"{d['placa']} lleva {d['tiempo']} dentro ({d['descripcion']})"})
    for i in no_disponibles:
        alertas.append({'nivel': 'serio', 'icono': 'fa-screwdriver-wrench',
                        'texto': f"{i.transporte.placa} no disponible · {i.tipo} desde el {tiempo.a_lima(i.inicio):%d/%m %H:%M}"})
    if len(abiertos) >= 0.9 * cd.capacidad_simultanea:
        alertas.insert(0, {'nivel': 'critico' if len(abiertos) > cd.capacidad_simultanea else 'aviso', 'icono': 'fa-warehouse',
                           'texto': f"{len(abiertos)} vehículos dentro frente a una capacidad de {cd.capacidad_simultanea}"})

    return {
        'cd': str(cd), 'capacidad': cd.capacidad_simultanea,
        'fecha': tiempo.fecha_larga(hoy), 'hora': f"{ahora:%H:%M}",
        # Un domingo de madrugada puede seguir en curso el turno noche del sábado: entonces no es «Sin actividad».
        'no_laborable': None if del_dia else jornadas.no_laborables_entre(hoy, hoy).get(hoy),
        'cifras': {
            'dentro': len(abiertos),
            'en_atencion': sum(1 for t in abiertos if t.es_programado),
            'flota_dentro': sum(1 for t in abiertos if t.es_de_jornada),
            'programadas': sum(1 for e in estados if e != servicios.CANCELADA),
            'atendidas': estados.count(servicios.CERRADA) + estados.count(servicios.EN_PLANTA),
            'pendientes': estados.count(servicios.PENDIENTE),
            'jornadas': len(del_dia),
            'placas': len({j.transporte_id for j in del_dia}),
            'dia': sum(1 for j in del_dia if not j.es_noche),
            'noche': sum(1 for j in del_dia if j.es_noche),
            'grupos': [{'etiqueta': etiqueta, 'total': sum(1 for c in codigos if c in incluidos)}
                       for _, etiqueta, incluidos in GRUPOS],
            'fuera_de_horario': codigos.count('FUERA_DE_HORARIO'),
            'no_disponibles': len(no_disponibles),
        },
        'gantt': _gantt_del_momento(hoy, ahora, cd, config),
        'dentro': dentro,
        'alertas': alertas,
        **indicadores_gerenciales(cd, ahora, config),
    }


def _gantt_del_momento(hoy, ahora, cd, config):
    """La línea de tiempo del turno en curso: el turno noche de hoy desde una hora antes de su Pre-Ruta, el turno
    noche de ayer hasta una hora después de la Pre-Ruta del día (vuelve de madrugada) y, si no hay turno noche, el
    turno día."""
    hora = ahora.time()
    antes_noche = (tiempo.combinar(hoy, config.hora_pre_ruta_noche) - timedelta(hours=1)).time()
    fin_madrugada = (tiempo.combinar(hoy, config.hora_pre_ruta) + timedelta(hours=1)).time()
    candidatos = []
    if hora >= antes_noche:
        candidatos.append((hoy, jornadas.NOCHE))
    elif hora < fin_madrugada:
        candidatos.append((hoy - timedelta(days=1), jornadas.NOCHE))
    candidatos.append((hoy, jornadas.DIA))
    for fecha, turno in candidatos:
        datos_gantt = gantt(fecha, ahora, {'cd': cd.pk}, turno)
        if datos_gantt['filas'] or turno == jornadas.DIA:
            return datos_gantt


# --- INDICADORES GERENCIALES (franja de 8 cifras y páginas que rotan) ---

def turno_en_foco(cd, ahora, config):
    """(fecha, turno) que muestra la TV: el turno noche de hoy desde una hora antes de su Pre-Ruta, el de ayer hasta
    una hora después de la Pre-Ruta del día (vuelve de madrugada), si hay jornadas de ese turno en el CD; si no, el
    turno día de hoy. Es la misma regla de la línea de tiempo."""
    hoy, hora = ahora.date(), ahora.time()
    antes_noche = (tiempo.combinar(hoy, config.hora_pre_ruta_noche) - timedelta(hours=1)).time()
    fin_madrugada = (tiempo.combinar(hoy, config.hora_pre_ruta) + timedelta(hours=1)).time()
    candidato = None
    if hora >= antes_noche:
        candidato = hoy
    elif hora < fin_madrugada:
        candidato = hoy - timedelta(days=1)
    if candidato and (Jornada.objects.filter(fecha=candidato, turno=jornadas.NOCHE, cd=cd)
                      .exclude(estado=Jornada.Estado.ANULADA).exists()):
        return candidato, jornadas.NOCHE
    return hoy, jornadas.DIA


def _nivel(valor, bueno, regular, menor_es_mejor=False):
    """Semáforo: ok / aviso / critico (None si no hay dato)."""
    if valor is None:
        return None
    if menor_es_mejor:
        return 'ok' if valor <= bueno else ('aviso' if valor <= regular else 'critico')
    return 'ok' if valor >= bueno else ('aviso' if valor >= regular else 'critico')


def _miles(numero):
    return kilometraje.miles(round(numero)) if numero is not None else None


def _dias_laborables_hasta(fecha, cantidad):
    """Los `cantidad` días laborables que terminan en `fecha` (incluida si es laborable)."""
    no_laborables = jornadas.no_laborables_entre(fecha - timedelta(days=cantidad * 2 + 7), fecha)
    dias, dia = [], fecha
    while len(dias) < cantidad and dia >= fecha - timedelta(days=cantidad * 2 + 7):
        if dia not in no_laborables:
            dias.append(dia)
        dia -= timedelta(days=1)
    return list(reversed(dias))


def indicadores_gerenciales(cd, ahora, config):
    fecha, turno = turno_en_foco(cd, ahora, config)
    filtros = {'cd': cd.pk, 'turno': turno}
    lista = indicadores_flota.jornadas_del_periodo(fecha, fecha, filtros, ahora)
    internas = indicadores_flota.programaciones_internas(fecha, fecha, filtros)
    k = indicadores_flota.kpis(lista, ahora, internas)
    # Referencia: el mismo día de la semana pasada, mismo turno y CD (día completo).
    referencia = fecha - timedelta(days=7)
    k_ref = indicadores_flota.kpis(indicadores_flota.jornadas_del_periodo(referencia, referencia, filtros, ahora), ahora)

    rutas = [p for p in internas if p.estado != Programacion.Estado.CANCELADA or p.incidencia_id]
    atendidas = sum(1 for p in rutas if p.estado in (Programacion.Estado.EN_PLANTA, Programacion.Estado.CERRADA))
    pendientes = sum(1 for p in rutas if p.estado == Programacion.Estado.PROGRAMADA and not p.no_atendida_por_cierre)
    estados = {j.pk: jornadas.estado_visible(j, ahora).codigo for j in lista}
    en_pre = sum(1 for c in estados.values() if c in ('EN_PRE_RUTA', 'PRE_RUTA_EXCEDIDA', 'POR_LLEGAR', 'POR_INICIAR'))
    en_ruta = sum(1 for c in estados.values() if c in ('EN_RUTA', 'EN_VISITA', 'SIN_RETORNO'))
    retornaron = sum(1 for j in lista if j.estado == Jornada.Estado.CERRADA)
    noche = turno == jornadas.NOCHE
    post = k['post_ruta_promedio_noche'] if noche else k['post_ruta_promedio_dia']
    post_ref = k_ref['post_ruta_promedio_noche'] if noche else k_ref['post_ruta_promedio_dia']
    dia_ref = f"{tiempo.DIAS_SEMANA[referencia.weekday()][:3].lower()} {referencia:%d/%m}"

    def comparar(actual, anterior, unidad, menor_es_mejor=False, neutro=False):
        if actual is None or anterior is None:
            return None
        diferencia = round(actual - anterior, 1)
        if diferencia == 0:
            return {'texto': f"igual que el {dia_ref}", 'mejora': None}
        mejora = None if neutro else ((diferencia < 0) == menor_es_mejor)
        return {'texto': f"{'+' if diferencia > 0 else '−'}{abs(diferencia):g}{unidad} vs. {dia_ref}", 'mejora': mejora}

    cierres = k['cierres']
    tarjetas = [
        {'clave': 'flota', 'titulo': 'Flota en operación', 'icono': 'fa-truck', 'valor': str(len({j.transporte_id for j in lista})),
         'detalle': f"{en_ruta} en ruta · {en_pre} por salir · {retornaron} retornaron", 'nivel': None},
        {'clave': 'pre_ruta', 'titulo': 'Pre-Ruta', 'icono': 'fa-sun',
         'valor': f"{k['pre_ruta_promedio']} min" if k['pre_ruta_promedio'] is not None else '—',
         'detalle': (f"{k['pct_pre_ruta_a_tiempo']:g} % a tiempo · meta {lista[0].meta_pre_ruta_min} min"
                     if k['pct_pre_ruta_a_tiempo'] is not None else f"meta {config.meta_pre_ruta_noche_min if noche else config.meta_pre_ruta_min} min · sin salidas aún"),
         'nivel': _nivel(k['pct_pre_ruta_a_tiempo'], 90, 75),
         'cambio': comparar(k['pre_ruta_promedio'], k_ref['pre_ruta_promedio'], ' min', menor_es_mejor=True)},
        {'clave': 'salidas', 'titulo': 'Salidas a tiempo', 'icono': 'fa-truck-fast',
         'valor': f"{k['pct_salidas_a_tiempo']:g} %" if k['pct_salidas_a_tiempo'] is not None else '—',
         'detalle': f"{k['salidas']} salida(s) a ruta · {k['salidas_retrasadas']} con retraso",
         'nivel': _nivel(k['pct_salidas_a_tiempo'], 90, 75),
         'cambio': comparar(k['pct_salidas_a_tiempo'], k_ref['pct_salidas_a_tiempo'], ' pts')},
        {'clave': 'rutas', 'titulo': 'Rutas del turno', 'icono': 'fa-route', 'valor': f"{atendidas} / {len(rutas)}",
         'detalle': f"atendidas · {pendientes} pendiente(s)", 'nivel': None},
        {'clave': 'retornos', 'titulo': 'Retornos en horario', 'icono': 'fa-flag-checkered',
         'valor': f"{cierres - k['fuera_de_horario']} / {cierres}" if cierres else '—',
         'detalle': (f"Post-Ruta {jornadas.texto_post_ruta(post)} del fin del turno" if post is not None
                     else f"fin del turno {(config.hora_cierre_noche if noche else config.hora_cierre_operativo):%H:%M}"),
         'nivel': _nivel(k['pct_cierre_en_horario'], 95, 80),
         'cambio': comparar(k['pct_cierre_en_horario'], k_ref['pct_cierre_en_horario'], ' pts')},
        {'clave': 'km', 'titulo': 'Km recorridos', 'icono': 'fa-road',
         'valor': f"{_miles(k['km_total'])} km" if k['km_turnos'] else '—',
         'detalle': (f"{k['km_por_unidad_dia']:g} km por unidad · de {k['km_turnos']} turno(s) cerrado(s) con km válido"
                     if k['km_turnos'] else 'se suman al cerrar cada turno (Post-Ruta)')
                    + (f" · {k['km_por_revisar']} por revisar" if k['km_por_revisar'] else ''),
         'nivel': 'aviso' if k['km_por_revisar'] else None,
         'cambio': comparar(k['km_por_unidad_dia'], k_ref['km_por_unidad_dia'], ' km/unidad', neutro=True)},
    ]

    # Página «Indicadores del turno»: por placa (vehículo), nunca por conductor.
    km_placas = sorted(({'placa': j.transporte.placa, 'km': j.km.recorrido, 'motivo': j.km.motivo,
                         'por_revisar': j.km.por_revisar} for j in lista if j.km.valido or j.km.por_revisar),
                       key=lambda f: (f['km'] is None, -(f['km'] or 0)))
    pre_placas = sorted(({'placa': j.transporte.placa, 'minutos': j.minutos_pre_ruta, 'meta': j.meta_pre_ruta_min,
                          'excedida': j.minutos_pre_ruta > j.meta_pre_ruta_min}
                         for j in lista if j.minutos_pre_ruta is not None), key=lambda f: -f['minutos'])
    retornos = sorted(({'placa': j.transporte.placa, 'hora': f"{tiempo.a_lima(j.post_ruta_en):%H:%M}",
                        'minutos': j.minutos_post_ruta, 'texto': jornadas.texto_post_ruta(j.minutos_post_ruta),
                        'calificacion': jornadas.ETIQUETAS_CIERRE.get(j.calificacion, ''),
                        'fuera': j.calificacion == jornadas.FUERA_DE_HORARIO}
                       for j in lista if j.minutos_post_ruta is not None), key=lambda f: f['minutos'])

    # Página «Últimos 7 días laborables» (mismo turno y CD).
    dias = _dias_laborables_hasta(fecha, 7)
    semana_lista = indicadores_flota.jornadas_del_periodo(dias[0], dias[-1], filtros, ahora) if dias else []
    semana = []
    for dia in dias:
        kd = indicadores_flota.kpis([j for j in semana_lista if j.fecha == dia], ahora)
        semana.append({'fecha': f"{tiempo.DIAS_SEMANA[dia.weekday()][:3]} {dia:%d/%m}", 'en_curso': dia == fecha,
                       'jornadas': kd['jornadas'], 'km': kd['km_total'] if kd['km_turnos'] else None,
                       'km_unidad': kd['km_por_unidad_dia'], 'pre_ruta': kd['pre_ruta_promedio'],
                       'pct_pre': kd['pct_pre_ruta_a_tiempo'], 'pct_salidas': kd['pct_salidas_a_tiempo'],
                       'pct_retornos': kd['pct_cierre_en_horario'],
                       'post_ruta': jornadas.texto_post_ruta(kd['post_ruta_promedio_noche'] if noche
                                                             else kd['post_ruta_promedio_dia']) or None})

    return {
        'foco': {'fecha': fecha.isoformat(), 'turno': turno, 'noche': noche,
                 'texto': f"Turno {'noche' if noche else 'día'} del {fecha:%d/%m}",
                 'cuadre': f"Mismas cifras que Dashboard › Flota y el Reporte de jornadas: {fecha:%d/%m/%Y} · turno "
                           f"{'noche' if noche else 'día'} · CD {cd}"},
        'indicadores': tarjetas,
        'pagina_turno': {'km': km_placas[:10], 'pre_ruta': pre_placas[:10], 'retornos': retornos[:10],
                         'meta_pre_ruta': lista[0].meta_pre_ruta_min if lista else config.meta_pre_ruta_min,
                         'km_max': max((f['km'] or 0 for f in km_placas), default=0),
                         'fin_turno': f"{(config.hora_cierre_noche if noche else config.hora_cierre_operativo):%H:%M}"},
        'semana': semana,
    }
