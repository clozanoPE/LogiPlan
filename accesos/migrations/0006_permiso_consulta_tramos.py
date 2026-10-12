"""Permiso de consulta de Tramos en Django Admin para el rol Administrador (solo lectura)."""
from django.contrib.auth.management import create_permissions
from django.db import migrations


def cargar(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    config_app = apps.get_app_config('accesos')
    config_app.models_module = True
    create_permissions(config_app, apps=apps, verbosity=0)
    config_app.models_module = None
    administrador, _ = Group.objects.get_or_create(name='Accesos - Administrador')
    administrador.permissions.add(*Permission.objects.filter(content_type__app_label='accesos', codename='view_tramo'))


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0005_tramo'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
