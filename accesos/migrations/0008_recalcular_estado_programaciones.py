"""
Pasa a guardado el estado de las programaciones que ya tienen tramo: En planta si su tramo
vigente sigue abierto, Cerrada si ya tiene salida. Las canceladas no se tocan.
"""
from django.db import migrations


def recalcular(apps, schema_editor):
    Programacion = apps.get_model('accesos', 'Programacion')
    Tramo = apps.get_model('accesos', 'Tramo')
    for tramo in Tramo.objects.filter(vigente=True).only('programacion_id', 'abierto'):
        (Programacion.objects.filter(pk=tramo.programacion_id).exclude(estado='CANCELADA')
         .update(estado='EN_PLANTA' if tramo.abierto else 'CERRADA'))


def revertir(apps, schema_editor):
    Programacion = apps.get_model('accesos', 'Programacion')
    Programacion.objects.filter(estado__in=['EN_PLANTA', 'CERRADA']).update(estado='PROGRAMADA')


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0007_estado_programacion_guardado'),
    ]

    operations = [
        migrations.RunPython(recalcular, revertir),
    ]
