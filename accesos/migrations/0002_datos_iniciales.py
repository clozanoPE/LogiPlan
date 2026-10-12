"""
Datos iniciales del módulo de Accesos. Idempotente (get_or_create): se puede aplicar en una
base que ya tenga parte de estos datos sin duplicarlos. Reemplaza la necesidad de tocar
crear_admin.py para este módulo.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

GRUPOS = ['Accesos - Administrador', 'Accesos - Despacho', 'Accesos - Vigilancia', 'Accesos - Consulta']

CENTROS = [
    {'codigo': 'LURIN', 'nombre': 'Lurín', 'capacidad_simultanea': 5},
    {'codigo': 'PUNTA_NEGRA', 'nombre': 'Punta Negra', 'capacidad_simultanea': 3},
]

TIPOS_DOCUMENTO = [
    {'codigo': 'DNI', 'nombre': 'Documento Nacional de Identidad', 'longitud_min': 8, 'longitud_max': 8, 'solo_digitos': True},
    {'codigo': 'CE', 'nombre': 'Carné de Extranjería', 'longitud_min': 9, 'longitud_max': 12, 'solo_digitos': False},
]

# Modelos que el rol Administrador gestiona desde Django Admin (sin permiso de borrado:
# el admin del módulo lo bloquea igualmente). La auditoría solo se consulta.
MODELOS_ADMINISTRABLES = ['centrodistribucion', 'tamanovehiculo', 'tipodocumentoidentidad',
                          'configuracionaccesos', 'transporte', 'conductor', 'usuariocd']


def cargar_datos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    CentroDistribucion = apps.get_model('accesos', 'CentroDistribucion')
    TipoDocumentoIdentidad = apps.get_model('accesos', 'TipoDocumentoIdentidad')
    ConfiguracionAccesos = apps.get_model('accesos', 'ConfiguracionAccesos')

    for nombre in GRUPOS:
        Group.objects.get_or_create(name=nombre)

    for centro in CENTROS:
        CentroDistribucion.objects.get_or_create(codigo=centro['codigo'], defaults=centro)

    for tipo in TIPOS_DOCUMENTO:
        TipoDocumentoIdentidad.objects.get_or_create(codigo=tipo['codigo'], defaults=tipo)

    ConfiguracionAccesos.objects.get_or_create(pk=1)

    # Los permisos se crean normalmente después de migrar; se fuerzan aquí para poder asignarlos.
    config_app = apps.get_app_config('accesos')
    config_app.models_module = True
    create_permissions(config_app, apps=apps, verbosity=0)
    config_app.models_module = None

    codenames = [f'{accion}_{modelo}' for modelo in MODELOS_ADMINISTRABLES for accion in ('add', 'change', 'view')]
    codenames.append('view_registroauditoria')
    administrador = Group.objects.get(name='Accesos - Administrador')
    administrador.permissions.add(*Permission.objects.filter(content_type__app_label='accesos', codename__in=codenames))


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        # Reversa sin efecto: deshacer la migración no borra grupos ni catálogos ya en uso.
        migrations.RunPython(cargar_datos, migrations.RunPython.noop),
    ]
