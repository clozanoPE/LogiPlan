"""
Reporte de jornadas de la flota propia (30/10/2026): el control operativo detallado de lo que pidieron las áreas.

- Tiempo de Pre-Ruta = salida a ruta − ingreso (06:30 en el turno día, 20:00 en el turno noche, o la entrada real si
  la unidad llegó después).
- Tiempo de Post-Ruta = fin del turno (16:00; 06:15 del día siguiente en el turno noche) − retorno real al CD:
  positivo = volvió antes, negativo = después.
- Km por turno = última − primera lectura del turno; km del día = turno día + turno noche que empieza ese día;
  km fuera de jornada = lo recorrido entre la lectura anterior de la placa y el inicio del turno (p. ej. si durmió
  fuera). Solo suman los turnos con km válido; los demás quedan «por revisar» con su motivo.
- Control del recorrido (30/10/2026, pedido del usuario: «en una sola vista ver el tiempo y recorrido en km de una
  placa, cómo inició el día y con qué km terminó, y al mes sacar estadísticas por placa»): la vista principal es
  «Por placa y día» (inicio y km inicial, fin y km final, km, horas en ruta, retornos imprevistos); «Resumen por
  placa» suma el periodo y lo contrasta con el odómetro (primera y última lectura válida: si no cuadra, hay huecos);
  el Excel trae además «Movimientos», cada entrada y salida con su km y sus minutos. «Por tipo de unidad» queda solo
  en el Excel (decisión del usuario: hoy todos los transportes son del mismo tamaño).

Usa el motor único de indicadores_flota (jornadas_del_periodo + kpis) con los mismos filtros del Dashboard, así que
sin filtros propios del reporte su resumen es igual a las tarjetas de Dashboard › Flota. Los filtros propios
(estado, Pre-Ruta, retorno, km) recortan la lista y el resumen se recalcula sobre lo que queda (cuadra con la
tabla y con el Excel). Indicador OPERATIVO del vehículo: no es asistencia ni sustenta horas extra; los minutos
nunca se agrupan ni se suman por conductor.
"""
from collections import defaultdict

from . import indicadores_flota, jornadas, kilometraje, servicios, tiempo
from .models import Jornada, Programacion, Tramo

Estado = Jornada.Estado
KmEstado = Tramo.KmEstado

ESTADOS = {
    'cerrada': 'Cerrada',
    'en_curso': 'En curso',
    'sin_post_ruta': 'Sin Post-Ruta',
    'no_salio': 'No salió',
    'averia': 'Por avería',
    'sin_retorno': 'Cerrada sin retorno',
}
PRE_RUTA = {'a_tiempo': 'Pre-Ruta a tiempo', 'excedida': 'Pre-Ruta excedida'}
RETORNOS = {jornadas.ANTICIPADO: 'Retorno: fin anticipado', jornadas.EN_HORARIO: 'Retorno: en horario',
            jornadas.FUERA_DE_HORARIO: 'Retorno: fuera de horario'}
KM = {'valido': 'Km válido', 'por_revisar': 'Km por revisar', 'no_leido': 'Odómetro no leído',
      'observado': 'Km observado', 'excluido': 'Lectura excluida'}
VISTAS = [
    ('placa_dia', 'Por placa y día', 'fa-calendar-day'),
    ('turno', 'Por turno', 'fa-list'),
    ('mes', 'Resumen por placa', 'fa-chart-bar'),
    ('calidad', 'Calidad del km', 'fa-gauge-high'),
]
VISTA_INICIAL = 'placa_dia'
ORDENES = {
    'fecha': (lambda f: (f['fecha'], f['placa'], f['turno_orden']), False),
    'placa': (lambda f: (f['placa'], f['fecha'], f['turno_orden']), False),
    'pre_ruta': (lambda f: (f['pre_ruta'] is None, -(f['pre_ruta'] or 0)), False),
    'post_ruta': (lambda f: (f['post_ruta'] is None, f['post_ruta'] or 0), False),
    'km': (lambda f: (f['km_recorrido'] is None, -(f['km_recorrido'] or 0)), False),
}
AVISO = indicadores_flota.AVISO_OPERATIVO


def leer_extra(params):
    """Filtros propios del reporte (los demás son los del Dashboard). Valores inválidos se ignoran."""
    return {
        'estado': params.get('estado') if params.get('estado') in ESTADOS else None,
        'pre': params.get('pre') if params.get('pre') in PRE_RUTA else None,
        'retorno': params.get('retorno') if params.get('retorno') in RETORNOS else None,
        'km': params.get('km') if params.get('km') in KM else None,
    }


def estado(jornada, ahora):
    """Estado de la jornada para el reporte (clave de ESTADOS)."""
    if jornada.estado == Estado.CERRADA:
        if jornada.cierre_sin_retorno:
            return 'sin_retorno'
        return 'averia' if jornada.motivo_cierre == Jornada.MotivoCierre.AVERIA else 'cerrada'
    if jornada.estado == Estado.EN_RUTA:
        return 'sin_post_ruta' if jornadas.turno_terminado(jornada, ahora) else 'en_curso'
    return 'no_salio' if jornadas.vencida_sin_salir(jornada, ahora) else 'en_curso'


def _cumple(jornada, extra, ahora):
    if extra['estado'] and estado(jornada, ahora) != extra['estado']:
        return False
    if extra['pre']:
        a_tiempo = jornadas.pre_ruta_a_tiempo(jornada)
        if a_tiempo is None or a_tiempo != (extra['pre'] == 'a_tiempo'):
            return False
    if extra['retorno'] and jornada.calificacion != extra['retorno']:
        return False
    if extra['km']:
        km = jornada.km
        estados = {e for _, _, _, e in km.lecturas}
        if extra['km'] == 'valido' and not km.valido:
            return False
        if extra['km'] == 'por_revisar' and not km.por_revisar:
            return False
        if extra['km'] == 'no_leido' and KmEstado.NO_LEIDO not in estados:
            return False
        if extra['km'] == 'excluido' and KmEstado.EXCLUIDO not in estados:
            return False
        if extra['km'] == 'observado' and KmEstado.OBSERVADO not in estados:
            return False
    return True


def datos(desde, hasta, filtros, extra, ahora):
    """Jornadas filtradas, rutas INTERNO por jornada y el resumen (indicadores_flota.kpis sobre lo filtrado)."""
    todas = indicadores_flota.jornadas_del_periodo(desde, hasta, filtros, ahora)
    lista = [j for j in todas if _cumple(j, extra, ahora)]
    internas = indicadores_flota.programaciones_internas(desde, hasta, filtros)
    if any(extra.values()):
        ids = {j.pk for j in lista}
        internas = [p for p in internas if p.jornada_id in ids]
    rutas = defaultdict(list)
    for p in internas:
        rutas[p.jornada_id].append(p)
    return lista, rutas, indicadores_flota.kpis(lista, ahora, internas)


def _local(momento):
    return tiempo.a_lima(momento).replace(tzinfo=None) if momento else None


def _ingreso(j):
    """Inicio de la jornada: la entrada real (ingreso desde afuera) o la hora de la Pre-Ruta si salió a ruta."""
    return j.inicio_real or (jornadas.inicio(j) if j.primera_salida else None)


def _rutas(j, rutas):
    """(programadas, atendidas) de la jornada: las canceladas solo cuentan si fue por avería."""
    programadas = [p for p in rutas.get(j.pk, []) if p.estado != Programacion.Estado.CANCELADA or p.incidencia_id]
    atendidas = [p for p in programadas if p.estado in (Programacion.Estado.EN_PLANTA, Programacion.Estado.CERRADA)]
    return len(programadas), len(atendidas)


def _porcentaje(parte, total):
    return round(100 * parte / total) if total else None


def _marcas(j):
    return [texto for texto, activa in (
        ('Ingresó desde afuera', j.ingreso_desde_afuera), ('Salió antes de la Pre-Ruta', j.salida_anticipada),
        ('Sin Pre-Ruta (visita abierta de otro día)', j.salida_sin_pre_ruta),
        ('Salió del CD tras su Post-Ruta', j.pernocto_fuera), ('Programada tarde', j.programada_tarde)) if activa]


def filas_turno(lista, rutas, ahora):
    """Una fila por jornada (placa y turno)."""
    filas = []
    for j in lista:
        programadas, atendidas = _rutas(j, rutas)
        clave_estado = estado(j, ahora)
        filas.append({
            'jornada': j, 'codigo': j.codigo, 'fecha': j.fecha, 'turno': 'Noche' if j.es_noche else 'Día',
            'turno_orden': 1 if j.es_noche else 0, 'cd': str(j.cd), 'placa': j.transporte.placa,
            'placa_normalizada': j.transporte.placa_normalizada, 'tamano': str(j.transporte.tamano),
            'conductor': j.conductor.nombre_completo, 'estado': ESTADOS[clave_estado], 'estado_clave': clave_estado,
            'ingreso': _ingreso(j),
            'salida_ruta': j.primera_salida, 'pre_ruta': j.minutos_pre_ruta, 'meta_pre_ruta': j.meta_pre_ruta_min,
            'pre_a_tiempo': jornadas.pre_ruta_a_tiempo(j),
            'fin_turno': jornadas.cierre(j), 'retorno': j.post_ruta_en, 'post_ruta': j.minutos_post_ruta,
            'post_ruta_texto': jornadas.texto_post_ruta(j.minutos_post_ruta),
            'calificacion': jornadas.ETIQUETAS_CIERRE.get(j.calificacion, ''),
            'calificacion_clave': j.calificacion, 'minutos_sobre_cierre': j.minutos_sobre_cierre,
            'rutas_programadas': programadas, 'rutas_atendidas': atendidas,
            'en_ruta': j.km.minutos_en_ruta, 'en_ruta_texto': _horas(j.km.minutos_en_ruta),
            'imprevistos': j.km.imprevistos, 'minutos_imprevistos': j.km.minutos_imprevistos,
            'salidas': len(j.salidas), 'salidas_a_tiempo': len(j.salidas) - len(j.retrasos),
            'km_inicial': j.km.inicial, 'km_final': j.km.final, 'km_recorrido': j.km.recorrido,
            'km_valido': j.km.valido, 'km_motivo': j.km.motivo, 'km_por_revisar': j.km.por_revisar,
            'km_fuera': j.km.fuera, 'marcas': _marcas(j),
        })
    return filas


def ordenar(filas, orden):
    clave, _ = ORDENES.get(orden, ORDENES['fecha'])
    return sorted(filas, key=clave)


def _horas(minutos):
    return servicios.formato_duracion(minutos) if minutos is not None else ''


def filas_placa_dia(lista, rutas=None):
    """Una fila por placa y día (turno día + turno noche que empieza ese día): cómo inició (hora y km), cómo
    terminó (hora y km), km recorridos, horas en ruta, rutas y retornos imprevistos. Es la base del cruce con el
    reporte de combustible (por placa y fecha)."""
    rutas = rutas or {}
    grupos = defaultdict(list)
    for j in lista:
        grupos[(j.fecha, j.transporte.placa_normalizada)].append(j)
    filas = []
    for (fecha, _), del_dia in sorted(grupos.items()):
        del_dia = sorted(del_dia, key=lambda j: j.es_noche)
        ingresos = [i for j in del_dia if (i := _ingreso(j))]
        retornos = [j.post_ruta_en for j in del_dia if j.post_ruta_en]
        iniciales = [j.km.inicial for j in del_dia if j.km.inicial is not None]
        finales = [j.km.final for j in del_dia if j.km.final is not None]
        en_ruta = [j.km.minutos_en_ruta for j in del_dia if j.km.minutos_en_ruta is not None]
        rutas_dia = [_rutas(j, rutas) for j in del_dia]
        turno_dia = next((j for j in del_dia if not j.es_noche), None)
        dia = [j for j in del_dia if not j.es_noche]
        noche = [j for j in del_dia if j.es_noche]
        suma = lambda js: sum(j.km.recorrido for j in js if j.km.valido) if any(j.km.valido for j in js) else None
        km_dia, km_noche = suma(dia), suma(noche)
        validos = [j for j in del_dia if j.km.valido]
        km_turnos = sum(j.km.recorrido for j in validos) if validos else None
        fuera = sum(j.km.fuera or 0 for j in del_dia)
        pendientes = [j for j in del_dia if not j.km.valido]
        filas.append({
            'fecha': fecha, 'placa': del_dia[0].transporte.placa, 'placa_normalizada': del_dia[0].transporte.placa_normalizada,
            'tamano': str(del_dia[0].transporte.tamano), 'cd': ', '.join(sorted({str(j.cd) for j in del_dia})),
            'conductores': ', '.join(dict.fromkeys(j.conductor.nombre_completo for j in del_dia)),
            'turnos': len(del_dia), 'turno_noche': any(j.es_noche for j in del_dia),
            'inicio': min(ingresos) if ingresos else None, 'fin': max(retornos) if retornos else None,
            'km_inicial': iniciales[0] if iniciales else None, 'km_final': finales[-1] if finales else None,
            'en_ruta': sum(en_ruta) if en_ruta else None, 'en_ruta_texto': _horas(sum(en_ruta)) if en_ruta else '',
            'imprevistos': sum(j.km.imprevistos for j in del_dia),
            'rutas_programadas': sum(p for p, _ in rutas_dia), 'rutas_atendidas': sum(a for _, a in rutas_dia),
            'pre_ruta': turno_dia.minutos_pre_ruta if turno_dia else None,
            'post_ruta_texto': jornadas.texto_post_ruta(turno_dia.minutos_post_ruta) if turno_dia else '',
            'km_dia': km_dia, 'km_noche': km_noche, 'km_turnos': km_turnos,
            'km_fuera': fuera, 'km_total': (km_turnos or 0) + fuera if km_turnos is not None or fuera else None,
            'completo': not pendientes,
            'observacion': '; '.join(f"Turno {'noche' if j.es_noche else 'día'}: {j.km.motivo}" for j in pendientes),
        })
    return filas


def filas_mes(lista):
    """Resumen del periodo por placa (p. ej. el mes): días trabajados, km, horas en ruta e imprevistos, con la
    cobertura del km y el control cruzado contra el odómetro (última − primera lectura válida del periodo): si el
    km por odómetro no coincide con el km sumado de los turnos más el km fuera de jornada, hay huecos (lecturas
    no leídas, observadas o excluidas) y el consumo por km de ese periodo hay que leerlo con cuidado."""
    grupos = defaultdict(list)
    for j in lista:
        grupos[(j.transporte.placa, j.transporte.placa_normalizada)].append(j)
    filas = []
    for (placa, normalizada), del_placa in sorted(grupos.items()):
        orden = sorted(del_placa, key=lambda j: (j.fecha, j.es_noche))
        validos = [j for j in orden if j.km.valido]
        medibles = [j for j in orden if j.km.valido or j.km.por_revisar]
        km_turnos = sum(j.km.recorrido for j in validos) if validos else None
        fuera = sum(j.km.fuera or 0 for j in orden)
        lecturas = [km for j in orden for _, _, km, estado_km in j.km.lecturas
                    if estado_km == KmEstado.REGISTRADO and km is not None and not kilometraje.es_relleno(km)]
        reinicio = any(j.km.motivo == "Cambio de odómetro en el turno" for j in orden)
        odometro = (lecturas[-1] - lecturas[0] if len(lecturas) >= 2 and not reinicio and lecturas[-1] >= lecturas[0]
                    else None)
        # El km fuera de jornada del primer turno es anterior a la primera lectura del periodo: no entra en el odómetro.
        sumado = (km_turnos or 0) + fuera - (orden[0].km.fuera or 0)
        dias_km = {j.fecha for j in validos}
        en_ruta = [j.km.minutos_en_ruta for j in orden if j.km.minutos_en_ruta is not None]
        filas.append({
            'placa': placa, 'placa_normalizada': normalizada, 'tamano': str(orden[0].transporte.tamano),
            'dias': len({j.fecha for j in orden if j.primera_salida}), 'turnos': len(orden),
            'turnos_noche': sum(1 for j in orden if j.es_noche), 'turnos_km': len(validos),
            'por_revisar': sum(1 for j in orden if j.km.por_revisar),
            'cobertura': _porcentaje(len(validos), len(medibles)),
            'km_turnos': km_turnos, 'km_fuera': fuera, 'km_total': (km_turnos or 0) + fuera if validos or fuera else None,
            'km_por_dia': round(km_turnos / len(dias_km), 1) if dias_km else None,
            'km_primera': lecturas[0] if lecturas else None, 'km_ultima': lecturas[-1] if lecturas else None,
            'km_odometro': odometro, 'diferencia': odometro - sumado if odometro is not None else None,
            'en_ruta': sum(en_ruta) if en_ruta else None, 'en_ruta_texto': _horas(sum(en_ruta)) if en_ruta else '',
            'imprevistos': sum(j.km.imprevistos for j in orden),
            'minutos_imprevistos': sum(j.km.minutos_imprevistos for j in orden),
        })
    return filas


ETIQUETAS_MOTIVO = dict(Tramo.MotivoImprevisto.choices)


def filas_movimientos(lista):
    """Cada entrada y salida de las jornadas (Pre-Ruta, visitas, retornos imprevistos, Post-Ruta) con su km, los
    km desde la lectura válida anterior de la placa, los minutos en el CD y los minutos fuera antes de entrar: la
    bitácora para auditar el recorrido."""
    if not lista:
        return []
    por_id = {j.pk: j for j in lista}
    tramos = (Tramo.objects.filter(jornada_id__in=por_id, vigente=True)
              .select_related('transporte', 'cd', 'conductor', 'programacion', 'vigilante_entrada', 'vigilante_salida')
              .order_by('transporte__placa_normalizada', 'entrada_en', 'id'))
    filas, km_anterior, salida_anterior = [], {}, {}
    for t in tramos:
        j = por_id[t.jornada_id]
        fuera = None
        if salida_anterior.get(t.transporte_id) and t.entrada_en >= salida_anterior[t.transporte_id]:
            fuera = round((t.entrada_en - salida_anterior[t.transporte_id]).total_seconds() / 60)
        desde_anterior = None
        if t.km_valido:
            previo = km_anterior.get(t.transporte_id)
            if previo is not None and t.km_entrada >= previo:
                desde_anterior = t.km_entrada - previo
            km_anterior[t.transporte_id] = t.km_entrada
        if t.salida_en:
            salida_anterior[t.transporte_id] = t.salida_en
        p = t.programacion
        detalle = (ETIQUETAS_MOTIVO.get(t.motivo_imprevisto, '') if t.es_imprevisto
                   else (f"{p.get_tipo_display()} · {p.razon_social}" if p else ''))
        filas.append({
            'fecha': j.fecha, 'turno': 'Noche' if j.es_noche else 'Día', 'placa': t.transporte.placa,
            'jornada': j.codigo, 'movimiento': t.get_clase_display(), 'detalle': detalle,
            'programacion': p.codigo if p else '', 'hora_programada': p.hora if p else None,
            'entrada': t.entrada_en, 'salida': t.salida_en, 'minutos_cd': t.minutos_permanencia, 'fuera': fuera,
            'km': t.km_entrada, 'estado_km': t.get_km_estado_display(), 'km_desde_anterior': desde_anterior,
            'conductor': t.conductor.nombre_completo, 'cd': str(t.cd),
            'vigilante_entrada': str(t.vigilante_entrada) if t.vigilante_entrada_id else 'Sistema',
            'vigilante_salida': str(t.vigilante_salida) if t.vigilante_salida_id else '',
            'obs_entrada': t.obs_entrada, 'obs_salida': t.obs_salida, 'tramo': t.pk,
        })
    return filas


def filas_tipo(lista):
    """Km por tipo de unidad (tamaño del maestro)."""
    grupos = defaultdict(list)
    for j in lista:
        grupos[str(j.transporte.tamano)].append(j)
    filas = []
    for tamano, del_tipo in sorted(grupos.items()):
        validos = [j for j in del_tipo if j.km.valido]
        total = sum(j.km.recorrido for j in validos)
        unidades_dia = {(j.transporte_id, j.fecha) for j in validos}
        filas.append({
            'tamano': tamano, 'placas': len({j.transporte_id for j in del_tipo}), 'jornadas': len(del_tipo),
            'turnos_km': len(validos), 'por_revisar': sum(1 for j in del_tipo if j.km.por_revisar),
            'km_total': total if validos else None,
            'km_unidad_dia': round(total / len(unidades_dia), 1) if unidades_dia else None,
            'km_turno': round(total / len(validos), 1) if validos else None,
            'km_fuera': sum(j.km.fuera or 0 for j in del_tipo),
        })
    return filas


ETIQUETAS_CLASE = dict(Tramo.Clase.choices)
ETIQUETAS_KM = dict(KmEstado.choices)


def filas_calidad(lista):
    """Turnos cuyo km no suma y se puede corregir (por revisar), con sus lecturas para ir a cada tramo."""
    filas = []
    for j in lista:
        if not j.km.por_revisar:
            continue
        lecturas = [{'tramo': pk, 'clase': ETIQUETAS_CLASE[clase], 'km': km, 'estado': ETIQUETAS_KM[estado_km],
                     'problema': estado_km != KmEstado.REGISTRADO or (km is not None and kilometraje.es_relleno(km))}
                    for pk, clase, km, estado_km in j.km.lecturas]
        filas.append({'jornada': j, 'codigo': j.codigo, 'fecha': j.fecha, 'turno': 'Noche' if j.es_noche else 'Día',
                      'placa': j.transporte.placa, 'placa_normalizada': j.transporte.placa_normalizada,
                      'motivo': j.km.motivo, 'lecturas': lecturas,
                      'sin_post_ruta': j.estado == Estado.EN_RUTA})
    return sorted(filas, key=lambda f: (f['fecha'], f['placa']), reverse=True)


def resumen(k):
    """Tarjetas del resumen (las mismas cifras que Dashboard › Flota con los mismos filtros)."""
    def minutos(valor):
        return f"{valor} min" if valor is not None else '—'

    def post(valor):
        return jornadas.texto_post_ruta(valor) or '—'

    def numero(valor, sufijo=''):
        return f"{kilometraje.miles(round(valor))}{sufijo}" if valor is not None else '—'

    return [
        {'titulo': 'Jornadas', 'valor': k['jornadas'],
         'detalle': f"{k['con_salida']} salieron a ruta · {k['imprevistos']} retorno(s) imprevisto(s)", 'icono': 'fa-truck'},
        {'titulo': 'Pre-Ruta promedio', 'valor': minutos(k['pre_ruta_promedio']),
         'detalle': f"{k['pct_pre_ruta_a_tiempo'] if k['pct_pre_ruta_a_tiempo'] is not None else '—'} % a tiempo (meta)",
         'icono': 'fa-sun'},
        {'titulo': 'Post-Ruta turno día', 'valor': post(k['post_ruta_promedio_dia']),
         'detalle': f"promedio de {k['post_ruta_dia']} retorno(s) frente al fin del turno", 'icono': 'fa-flag-checkered'},
        {'titulo': 'Post-Ruta turno noche', 'valor': post(k['post_ruta_promedio_noche']),
         'detalle': f"promedio de {k['post_ruta_noche']} retorno(s)", 'icono': 'fa-moon'},
        {'titulo': 'Retornos en horario', 'valor': f"{k['pct_cierre_en_horario']} %" if k['pct_cierre_en_horario'] is not None else '—',
         'detalle': f"{k['fuera_de_horario']} fuera de horario de {k['cierres']}", 'icono': 'fa-clock'},
        {'titulo': 'Km recorridos', 'valor': numero(k['km_total'] if k['km_turnos'] else None, ' km'),
         'detalle': f"{k['km_turnos']} turno(s) con km válido · {k['km_por_revisar']} por revisar", 'icono': 'fa-road'},
        {'titulo': 'Km por unidad y día', 'valor': numero(k['km_por_unidad_dia'], ' km'),
         'detalle': f"{k['km_unidades_dia']} unidad(es)-día · {k['pct_km_valido'] if k['pct_km_valido'] is not None else '—'} % con km válido",
         'icono': 'fa-gauge-high'},
        {'titulo': 'Salidas a tiempo', 'valor': f"{k['pct_salidas_a_tiempo']} %" if k['pct_salidas_a_tiempo'] is not None else '—',
         'detalle': f"{k['salidas']} salida(s) a ruta · rutas cumplidas {k.get('rutas_atendidas', 0)}/{k.get('rutas', 0)}",
         'icono': 'fa-truck-fast'},
    ]


NOTAS = [
    "Reporte de jornadas de la flota propia (Control de Accesos, Daryza). Mismos filtros y cifras que Dashboard › Flota.",
    "Tiempo de Pre-Ruta = salida a ruta − hora de ingreso (06:30 en el turno día y 20:00 en el turno noche; si la unidad "
    "entró al CD después, desde su entrada). Meta: 60 min.",
    "Tiempo de Post-Ruta = fin del turno (16:00; 06:15 del día siguiente en el turno noche) − hora real de retorno al CD. "
    "Positivo: volvió antes del fin del turno; negativo: después. La calificación del retorno usa la tolerancia "
    "(en horario hasta las 16:15 / 06:30). No incluye retornos por avería ni jornadas cerradas sin retorno.",
    "Km por turno = última lectura (Post-Ruta) − primera lectura del turno (al salir a ruta o al ingresar). Ejemplo: "
    "90 → 95 → 110 = 20 km. Km del día = turno día + turno noche que empieza ese día. Km fuera de jornada = lo recorrido "
    "entre la lectura anterior de la placa y el inicio del turno (por ejemplo, si durmió fuera del CD). Km total para "
    "combustible = km del día + km fuera de jornada.",
    f"Un turno suma km solo si sus lecturas son válidas: no suman los odómetros no leídos, las lecturas excluidas por el "
    f"Administrador, los valores de relleno (0, 99999…), los km que bajan, los turnos con más de "
    f"{kilometraje.TOPE_TURNO} km ni los de 0 km habiendo salido a ruta. Se listan en «Calidad del km» con su "
    "motivo para corregirlos.",
    "Promedio por unidad y día = km totales ÷ placas-día con km válido.",
    "Controles del km en portería (desde el 30/10/2026): cada lectura de un regreso al CD debe ser MAYOR a la anterior y "
    "el recorrido debe caber en el tiempo fuera del CD (a la velocidad máxima de Configuración, 80 km/h). Si el tablero "
    "marca otro valor, Vigilancia lo registra como «km observado»: no suma hasta que el Administrador lo valide. Los "
    "intentos rechazados quedan en la auditoría.",
    "Retorno imprevisto = la unidad en ruta volvió al CD por un hecho fortuito (olvidó carga, recojo…) y volvió a salir. "
    "No cambia rutas ni puntualidad; su lectura de km es un control intermedio del recorrido.",
    "Horas en ruta = tiempo fuera del CD entre la salida a ruta y la Post-Ruta (sin la Pre-Ruta, las visitas ni los "
    "retornos imprevistos).",
    "Resumen por placa: km por odómetro = última − primera lectura válida del periodo. Diferencia = km por odómetro − "
    "(km de los turnos + km fuera de jornada). Si no es 0, hay lecturas que no suman (ver «Calidad del km»): tómelo en "
    "cuenta al calcular el consumo por km.",
    AVISO + " Los minutos se muestran por placa (vehículo); el conductor es solo un dato de contexto.",
]


def hojas_excel(filas_t, filas_pd, filas_tp, filas_c, k, filtros_texto, generado, filas_m=(), filas_mov=()):
    """Hojas del Excel: Resumen, Por placa y día, Por turno, Movimientos, Resumen por placa, Calidad del km y Por
    tipo de unidad."""
    si_no = lambda v: '' if v is None else ('SI' if v else 'NO')
    resumen_filas = [['Generado', generado], ['Filtros', filtros_texto]]
    resumen_filas += [[t['titulo'], t['valor'], t['detalle']] for t in resumen(k)]
    resumen_filas += [
        ['Pre-Ruta promedio (min)', k['pre_ruta_promedio'], ''], ['Pre-Ruta P90 (min)', k['pre_ruta_p90'], ''],
        ['Post-Ruta promedio turno día (min)', k['post_ruta_promedio_dia'], '+ antes / − después del fin del turno'],
        ['Post-Ruta promedio turno noche (min)', k['post_ruta_promedio_noche'], ''],
        ['Km totales', k['km_total'], ''], ['Km por unidad y día', k['km_por_unidad_dia'], ''],
        ['Km por turno', k['km_por_turno'], ''], ['Km fuera de jornada', k['km_fuera_jornada'], ''],
        ['Retornos imprevistos', k['imprevistos'], f"{k['minutos_imprevistos']} min en el CD"],
        ['Horas en ruta (min)', k['minutos_en_ruta'], 'fuera del CD entre la salida a ruta y la Post-Ruta'],
    ]
    turno = [[f['fecha'], f['turno'], f['cd'], f['placa'], f['tamano'], f['conductor'], f['estado'],
              _local(f['ingreso']), _local(f['salida_ruta']), f['pre_ruta'], f['meta_pre_ruta'], si_no(f['pre_a_tiempo']),
              _local(f['fin_turno']), _local(f['retorno']), f['post_ruta'], f['calificacion'],
              f['minutos_sobre_cierre'] or None, f['rutas_programadas'], f['rutas_atendidas'], f['salidas'],
              f['salidas_a_tiempo'], f['km_inicial'], f['km_final'], f['km_recorrido'], si_no(f['km_valido']),
              f['km_motivo'], f['km_fuera'], f['en_ruta'], f['imprevistos'], ', '.join(f['marcas']), f['codigo']]
             for f in filas_t]
    placa_dia = [[f['fecha'], f['placa'], f['tamano'], f['cd'], f['conductores'], f['turnos'], _local(f['inicio']),
                  f['km_inicial'], _local(f['fin']), f['km_final'], f['km_dia'], f['km_noche'], f['km_turnos'],
                  f['km_fuera'], f['km_total'], f['en_ruta'], f['imprevistos'], f['rutas_programadas'],
                  f['rutas_atendidas'], si_no(f['completo']), f['observacion']] for f in filas_pd]
    mes = [[f['placa'], f['tamano'], f['dias'], f['turnos'], f['turnos_noche'], f['turnos_km'], f['por_revisar'],
            f['cobertura'], f['km_turnos'], f['km_fuera'], f['km_total'], f['km_por_dia'], f['km_primera'],
            f['km_ultima'], f['km_odometro'], f['diferencia'], f['en_ruta'], f['imprevistos'],
            f['minutos_imprevistos']] for f in filas_m]
    movimientos = [[f['fecha'], f['turno'], f['placa'], f['jornada'], f['movimiento'], f['detalle'], f['programacion'],
                    f['hora_programada'], _local(f['entrada']), _local(f['salida']), f['minutos_cd'], f['fuera'], f['km'],
                    f['estado_km'], f['km_desde_anterior'], f['conductor'], f['cd'], f['vigilante_entrada'],
                    f['vigilante_salida'], f['obs_entrada'], f['obs_salida'], f['tramo']] for f in filas_mov]
    tipo = [[f['tamano'], f['placas'], f['jornadas'], f['turnos_km'], f['por_revisar'], f['km_total'],
             f['km_unidad_dia'], f['km_turno'], f['km_fuera']] for f in filas_tp]
    calidad = [[f['fecha'], f['turno'], f['placa'], f['codigo'], f['motivo'],
                ' · '.join(f"{l['clase']} {l['km'] if l['km'] is not None else '—'} ({l['estado']}, tramo {l['tramo']})"
                           for l in f['lecturas'])] for f in filas_c]
    fecha_hora = 'DD/MM/YYYY HH:MM'
    return [
        ('Resumen', ['Indicador', 'Valor', 'Detalle'], resumen_filas, {}),
        ('Por placa y día', ['Fecha', 'Placa', 'Tamaño', 'CD', 'Conductor(es)', 'Turnos', 'Inicio', 'Km inicial', 'Fin',
                             'Km final', 'Km turno día', 'Km turno noche', 'Km del día', 'Km fuera de jornada',
                             'Km total (combustible)', 'Horas en ruta (min)', 'Retornos imprevistos', 'Rutas programadas',
                             'Rutas atendidas', 'Completo', 'Observación'], placa_dia,
         {0: 'DD/MM/YYYY', 6: fecha_hora, 7: '#,##0', 8: fecha_hora, 9: '#,##0', 10: '#,##0', 11: '#,##0', 12: '#,##0',
          13: '#,##0', 14: '#,##0'}),
        ('Por turno', ['Fecha', 'Turno', 'CD', 'Placa', 'Tamaño', 'Conductor previsto', 'Estado', 'Ingreso (inicio Pre-Ruta)',
                       'Salida a ruta', 'Pre-Ruta (min)', 'Meta Pre-Ruta (min)', 'Pre-Ruta a tiempo', 'Fin del turno',
                       'Retorno real', 'Post-Ruta (min)', 'Calificación del retorno', 'Min sobre la tolerancia',
                       'Rutas programadas', 'Rutas atendidas', 'Salidas a ruta', 'Salidas a tiempo', 'Km inicial',
                       'Km final', 'Km recorridos', 'Km válido', 'Motivo (km)', 'Km fuera de jornada',
                       'Horas en ruta (min)', 'Retornos imprevistos', 'Marcas', 'Jornada'], turno,
         {0: 'DD/MM/YYYY', 7: fecha_hora, 8: fecha_hora, 12: fecha_hora, 13: fecha_hora, 21: '#,##0', 22: '#,##0',
          23: '#,##0', 26: '#,##0'}),
        ('Movimientos', ['Fecha del turno', 'Turno', 'Placa', 'Jornada', 'Movimiento', 'Detalle', 'Programación',
                         'Hora programada', 'Entrada', 'Salida', 'Minutos en el CD', 'Minutos fuera antes de entrar',
                         'Km', 'Estado del km', 'Km desde la lectura anterior', 'Conductor', 'CD', 'Registró la entrada',
                         'Registró la salida', 'Observación de entrada', 'Observación de salida', 'Tramo'], movimientos,
         {0: 'DD/MM/YYYY', 7: 'HH:MM', 8: fecha_hora, 9: fecha_hora, 12: '#,##0', 14: '#,##0'}),
        ('Resumen por placa', ['Placa', 'Tamaño', 'Días trabajados', 'Turnos', 'Turnos noche', 'Turnos con km válido',
                               'Turnos por revisar', 'Cobertura del km (%)', 'Km de los turnos', 'Km fuera de jornada',
                               'Km total (combustible)', 'Km por día', 'Primera lectura', 'Última lectura',
                               'Km por odómetro', 'Diferencia (huecos)', 'Horas en ruta (min)', 'Retornos imprevistos',
                               'Minutos en CD por imprevistos'], mes,
         {8: '#,##0', 9: '#,##0', 10: '#,##0', 11: '#,##0.0', 12: '#,##0', 13: '#,##0', 14: '#,##0', 15: '#,##0'}),
        ('Calidad del km', ['Fecha', 'Turno', 'Placa', 'Jornada', 'Motivo', 'Lecturas'], calidad, {0: 'DD/MM/YYYY'}),
        ('Por tipo de unidad', ['Tamaño', 'Placas', 'Jornadas', 'Turnos con km válido', 'Turnos por revisar', 'Km totales',
                                'Km por unidad y día', 'Km por turno', 'Km fuera de jornada'], tipo,
         {5: '#,##0', 6: '#,##0.0', 7: '#,##0.0', 8: '#,##0'}),
    ]
