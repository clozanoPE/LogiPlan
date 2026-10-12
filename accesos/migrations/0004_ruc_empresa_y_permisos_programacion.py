"""
RUC de la empresa (valor por defecto del documento en programaciones de tipo INTERNO) y
permiso de consulta de Programaciones en Django Admin para el rol Administrador.
Idempotente: solo completa el RUC si todavía está vacío.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

RUC_DARYZA = '20144109458'


def cargar(apps, schema_editor):
    ConfiguracionAccesos = apps.get_model('accesos', 'ConfiguracionAccesos')
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    config, _ = ConfiguracionAccesos.objects.get_or_create(pk=1)
    if not config.ruc_empresa:
        config.ruc_empresa = RUC_DARYZA
        config.save(update_fields=['ruc_empresa'])

    config_app = apps.get_app_config('accesos')
    config_app.models_module = True
    create_permissions(config_app, apps=apps, verbosity=0)
    config_app.models_module = None

    administrador, _ = Group.objects.get_or_create(name='Accesos - Administrador')
    administrador.permissions.add(*Permission.objects.filter(content_type__app_label='accesos',
                                                             codename='view_programacion'))


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0003_programacion'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
