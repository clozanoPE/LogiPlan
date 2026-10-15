"""
Turno noche (15/10/2026): liga cada programación INTERNO existente a su jornada. Antes de esta versión cada placa
tenía una sola jornada por día (hoy, turno DÍA), así que la regla es (placa, fecha) → jornada del turno día. La
regla nueva de turnos rige desde el despliegue (decisión del usuario); las programaciones futuras ya cargadas se
reclasifican con `python manage.py reclasificar_turnos`.

Reversa: desliga las programaciones. Solo es posible si todavía no hay jornadas del turno noche (la regla única
anterior, una jornada por placa y día, no las admite).
"""
from django.db import migrations


def vincular(apps, schema_editor):
    Programacion = apps.get_model('accesos', 'Programacion')
    Jornada = apps.get_model('accesos', 'Jornada')
    for jornada in Jornada.objects.filter(turno='DIA').only('pk', 'transporte_id', 'fecha'):
        (Programacion.objects.filter(transporte_id=jornada.transporte_id, fecha=jornada.fecha, tipo='INTERNO',
                                     jornada__isnull=True)
         .update(jornada_id=jornada.pk))


def desvincular(apps, schema_editor):
    Programacion = apps.get_model('accesos', 'Programacion')
    Jornada = apps.get_model('accesos', 'Jornada')
    if Jornada.objects.filter(turno='NOCHE').exists():
        raise RuntimeError("Hay jornadas del turno noche: no se puede volver a una jornada por placa y día.")
    Programacion.objects.exclude(jornada__isnull=True).update(jornada_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0015_turno_noche'),
    ]

    operations = [
        migrations.RunPython(vincular, desvincular),
    ]
