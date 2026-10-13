"""
Datos del modo TV de un CD (F6.4): lo que se ve en la pantalla grande, a distancia y sin tocar nada.
Lo usan la página (primer dibujo) y el endpoint JSON con el que la página se actualiza cada minuto.

Muestra placa y conductor (decisión del usuario). El exceso sobre el cierre operativo es un indicador
operativo del vehículo: en la TV solo se marca «Fuera de horario», sin minutos por persona.
"""
from . import jornadas, servicios, tiempo
from .indicadores_flota import gantt
from .models import ConfiguracionAccesos, Incidencia, Programacion, Tramo

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
            ('Flota propia · pernocta' if t.clase == Tramo.Clase.POST_RUTA else 'Flota propia · Pre-Ruta'),
            'flota': t.es_de_jornada, 'entrada': f"{tiempo.a_lima(t.entrada_en):%H:%M}",
            'tiempo': servicios.formato_duracion(minutos), 'minutos': minutos,
            'nivel': 'critico' if prolongada else ('aviso' if sobre_meta else 'ok'),
        })
    programaciones = list(Programacion.objects.filter(cd=cd, fecha=hoy))
    estados = [servicios.estado_visible(p, hoy) for p in programaciones]
    del_dia = jornadas.jornadas_del_dia(hoy, ahora, cd_ids={cd.pk})
    codigos = [j.estado_v.codigo for j in del_dia]
    no_disponibles = list(Incidencia.objects.filter(abierta=True).select_related('transporte', 'tipo'))

    alertas = []
    for j in del_dia:
        if j.estado_v.codigo == 'PRE_RUTA_EXCEDIDA':
            alertas.append({'nivel': 'aviso', 'icono': 'fa-sun',
                            'texto': f"{j.transporte.placa} sigue en Pre-Ruta pasada la meta · {j.conductor.nombre_completo}"})
        elif j.estado_v.codigo == 'SIN_RETORNO':
            alertas.append({'nivel': 'critico', 'icono': 'fa-clock',
                            'texto': f"{j.transporte.placa} sin retorno pasada la tolerancia · {j.conductor.nombre_completo}"})
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
        'no_laborable': jornadas.no_laborables_entre(hoy, hoy).get(hoy),
        'cifras': {
            'dentro': len(abiertos),
            'en_atencion': sum(1 for t in abiertos if t.es_programado),
            'flota_dentro': sum(1 for t in abiertos if t.es_de_jornada),
            'programadas': sum(1 for e in estados if e != servicios.CANCELADA),
            'atendidas': estados.count(servicios.CERRADA) + estados.count(servicios.EN_PLANTA),
            'pendientes': estados.count(servicios.PENDIENTE),
            'jornadas': len(del_dia),
            'grupos': [{'etiqueta': etiqueta, 'total': sum(1 for c in codigos if c in incluidos)}
                       for _, etiqueta, incluidos in GRUPOS],
            'fuera_de_horario': codigos.count('FUERA_DE_HORARIO'),
            'no_disponibles': len(no_disponibles),
        },
        'gantt': gantt(hoy, ahora, {'cd': cd.pk}),
        'dentro': dentro,
        'alertas': alertas,
    }
