"""Grupo «Accesos - Pantalla»: usuario de una pantalla de TV (modo TV de sus CD autorizados). Idempotente."""
from django.db import migrations


def cargar(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.get_or_create(name='Accesos - Pantalla')


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0011_datos_jornada'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
