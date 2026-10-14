"""
Datos para la hora de SALIDA en las rutas INTERNO y el kilometraje del maestro (14/10/2026):
1. Cada Pre-Ruta ya cerrada que no atendía ninguna ruta pasa a atender la primera ruta INTERNO pendiente de
   ese día en su CD (antes esa ruta quedaba pendiente y, al cerrar la jornada, «No atendida»). La programación
   queda Cerrada. Cada cambio queda en la auditoría a nombre de «Sistema».
2. Desviación de salida (salida − hora programada) de los tramos INTERNO cerrados que atienden una ruta.
3. Último kilometraje de cada transporte, desde sus tramos válidos.
Idempotente. La reversa deja de vincular las Pre-Rutas que vinculó (según la auditoría) y devuelve sus rutas a
Programada; las columnas nuevas las retira la migración anterior.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import migrations

LIMA = ZoneInfo('America/Lima')
MOTIVO = "Migración: la salida a ruta (fin de la Pre-Ruta) atiende la primera ruta INTERNO del día."
ORIGENES = {'POST_RUTA': "Post-Ruta (fin de jornada)", 'PROGRAMADO': "Entrada al CD"}


def _minutos(desde, hasta):
    return round((hasta - desde).total_seconds() / 60)


def _momento(programacion):
    return datetime.combine(programacion.fecha, programacion.hora, tzinfo=LIMA)


def _repr_tramo(tramo):
    return f"Tramo {tramo.transporte.placa} {tramo.fecha_local_entrada:%d/%m/%Y} {tramo.hora_local_entrada:%H:%M}"


def _repr_programacion(p):
    return f"PRG-{p.pk:06d} {p.transporte.placa} {p.fecha:%d/%m/%Y} {p.hora:%H:%M}"


def adelante(apps, schema_editor):
    Tramo = apps.get_model('accesos', 'Tramo')
    Programacion = apps.get_model('accesos', 'Programacion')
    Transporte = apps.get_model('accesos', 'Transporte')
    Auditoria = apps.get_model('accesos', 'RegistroAuditoria')

    # 1. Pre-Rutas cerradas sin ruta vinculada.
    pre_rutas = (Tramo.objects.filter(clase='PRE_RUTA', vigente=True, programacion__isnull=True,
                                      salida_en__isnull=False, jornada__isnull=False)
                 .select_related('jornada', 'transporte').order_by('entrada_en', 'pk'))
    for tramo in pre_rutas:
        ocupadas = Tramo.objects.filter(vigente=True, programacion__isnull=False).values('programacion_id')
        programacion = (Programacion.objects.filter(transporte_id=tramo.transporte_id, fecha=tramo.jornada.fecha,
                                                    cd_id=tramo.cd_id, tipo='INTERNO', estado='PROGRAMADA')
                        .exclude(pk__in=ocupadas).select_related('transporte').order_by('hora', 'pk').first())
        if programacion is None:
            continue
        desviacion = _minutos(_momento(programacion), tramo.salida_en)
        tramo.programacion = programacion
        tramo.desviacion_salida_min = desviacion
        tramo.save(update_fields=['programacion', 'desviacion_salida_min'])
        Auditoria.objects.create(
            usuario=None, accion='CORREGIR', entidad='Tramo', objeto_id=tramo.pk, objeto_repr=_repr_tramo(tramo)[:200],
            datos_antes={'programacion_id': None, 'desviacion_salida_min': None},
            datos_despues={'programacion_id': programacion.pk, 'desviacion_salida_min': desviacion},
            motivo=MOTIVO, cd_id=tramo.cd_id)
        antes = {'estado': programacion.estado, 'no_atendida_por_cierre': programacion.no_atendida_por_cierre}
        programacion.estado = 'CERRADA'
        programacion.no_atendida_por_cierre = False
        programacion.save(update_fields=['estado', 'no_atendida_por_cierre'])
        Auditoria.objects.create(
            usuario=None, accion='MODIFICAR', entidad='Programacion', objeto_id=programacion.pk,
            objeto_repr=_repr_programacion(programacion)[:200], datos_antes=antes,
            datos_despues={'estado': 'CERRADA', 'no_atendida_por_cierre': False}, motivo=MOTIVO,
            cd_id=programacion.cd_id)

    # 2. Desviación de salida de las rutas INTERNO cerradas.
    for tramo in (Tramo.objects.filter(vigente=True, tipo_snapshot='INTERNO', programacion__isnull=False,
                                       salida_en__isnull=False, desviacion_salida_min__isnull=True)
                  .select_related('programacion')):
        tramo.desviacion_salida_min = _minutos(_momento(tramo.programacion), tramo.salida_en)
        tramo.save(update_fields=['desviacion_salida_min'])

    # 3. Último kilometraje de cada transporte (misma regla que accesos/kilometraje.py).
    for transporte in Transporte.objects.all():
        tramo = (Tramo.objects.filter(transporte_id=transporte.pk, vigente=True, km_entrada__isnull=False)
                 .select_related('cd').order_by('-entrada_en', '-id').first())
        if tramo is None:
            continue
        if tramo.clase == 'PRE_RUTA':
            origen = "Inicio de jornada (ingreso)" if tramo.vigilante_entrada_id else "Salida a ruta (Pre-Ruta)"
        else:
            origen = ORIGENES[tramo.clase]
        momento = tramo.salida_en if tramo.clase == 'PRE_RUTA' and tramo.salida_en else tramo.entrada_en
        Transporte.objects.filter(pk=transporte.pk).update(
            km_actual=tramo.km_entrada, km_actualizado_en=momento, km_origen=f"{origen} · CD {tramo.cd.nombre}"[:60])


def atras(apps, schema_editor):
    Tramo = apps.get_model('accesos', 'Tramo')
    Programacion = apps.get_model('accesos', 'Programacion')
    Auditoria = apps.get_model('accesos', 'RegistroAuditoria')
    ids = Auditoria.objects.filter(entidad='Tramo', motivo=MOTIVO).values_list('objeto_id', flat=True)
    for tramo in Tramo.objects.filter(pk__in=list(ids), clase='PRE_RUTA', programacion__isnull=False):
        programacion_id = tramo.programacion_id
        tramo.programacion = None
        tramo.save(update_fields=['programacion'])
        Programacion.objects.filter(pk=programacion_id, estado='CERRADA').update(estado='PROGRAMADA')


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0013_km_transporte_desviacion_salida'),
    ]

    operations = [
        migrations.RunPython(adelante, atras),
    ]
