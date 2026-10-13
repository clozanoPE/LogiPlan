"""
Datos iniciales de la jornada de la flota propia (F6):
- Tipos de incidencia (avería = vehículo no disponible), editables en Django Admin.
- Feriados nacionales del Perú 2026 y 2027 (para que RRHH los valide; se editan en Django Admin).
- Permisos de Django Admin del rol Administrador: feriados y tipos de incidencia (sin borrado);
  jornadas e incidencias, solo consulta.
- Jornadas de las programaciones INTERNO ya registradas para hoy o fechas futuras (la Pre-Ruta se crea
  sola con la primera ruta INTERNO; las anteriores a esta versión no la tenían).
Idempotente. La reversa no borra nada (las tablas las retira la migración anterior).
"""
from datetime import date, datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.contrib.auth.management import create_permissions
from django.db import migrations

TIPOS_INCIDENCIA = ['Avería mecánica', 'Avería eléctrica', 'Neumáticos', 'Siniestro', 'Mantenimiento correctivo',
                    'Otro']

FERIADOS = [
    (date(2026, 1, 1), 'Año Nuevo'),
    (date(2026, 4, 2), 'Jueves Santo'),
    (date(2026, 4, 3), 'Viernes Santo'),
    (date(2026, 5, 1), 'Día del Trabajo'),
    (date(2026, 6, 7), 'Batalla de Arica y Día de la Bandera'),
    (date(2026, 6, 29), 'San Pedro y San Pablo'),
    (date(2026, 7, 23), 'Día de la Fuerza Aérea del Perú'),
    (date(2026, 7, 28), 'Fiestas Patrias'),
    (date(2026, 7, 29), 'Fiestas Patrias'),
    (date(2026, 8, 6), 'Batalla de Junín'),
    (date(2026, 8, 30), 'Santa Rosa de Lima'),
    (date(2026, 10, 8), 'Combate de Angamos'),
    (date(2026, 11, 1), 'Todos los Santos'),
    (date(2026, 12, 8), 'Inmaculada Concepción'),
    (date(2026, 12, 9), 'Batalla de Ayacucho'),
    (date(2026, 12, 25), 'Navidad'),
    (date(2027, 1, 1), 'Año Nuevo'),
    (date(2027, 3, 25), 'Jueves Santo'),
    (date(2027, 3, 26), 'Viernes Santo'),
    (date(2027, 5, 1), 'Día del Trabajo'),
    (date(2027, 6, 7), 'Batalla de Arica y Día de la Bandera'),
    (date(2027, 6, 29), 'San Pedro y San Pablo'),
    (date(2027, 7, 23), 'Día de la Fuerza Aérea del Perú'),
    (date(2027, 7, 28), 'Fiestas Patrias'),
    (date(2027, 7, 29), 'Fiestas Patrias'),
    (date(2027, 8, 6), 'Batalla de Junín'),
    (date(2027, 8, 30), 'Santa Rosa de Lima'),
    (date(2027, 10, 8), 'Combate de Angamos'),
    (date(2027, 11, 1), 'Todos los Santos'),
    (date(2027, 12, 8), 'Inmaculada Concepción'),
    (date(2027, 12, 9), 'Batalla de Ayacucho'),
    (date(2027, 12, 25), 'Navidad'),
]


def cargar(apps, schema_editor):
    TipoIncidencia = apps.get_model('accesos', 'TipoIncidencia')
    Feriado = apps.get_model('accesos', 'Feriado')
    for orden, nombre in enumerate(TIPOS_INCIDENCIA, start=1):
        TipoIncidencia.objects.get_or_create(nombre=nombre, defaults={'orden': orden})
    for fecha, descripcion in FERIADOS:
        Feriado.objects.get_or_create(fecha=fecha, defaults={'descripcion': descripcion})

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    config_app = apps.get_app_config('accesos')
    config_app.models_module = True
    create_permissions(config_app, apps=apps, verbosity=0)
    config_app.models_module = None
    codenames = [f'{accion}_{modelo}' for modelo in ('feriado', 'tipoincidencia') for accion in ('add', 'change', 'view')]
    codenames += ['view_jornada', 'view_incidencia']
    administrador, _ = Group.objects.get_or_create(name='Accesos - Administrador')
    administrador.permissions.add(*Permission.objects.filter(content_type__app_label='accesos', codename__in=codenames))

    crear_jornadas(apps)


def crear_jornadas(apps):
    """Misma regla que accesos.jornadas.sincronizar, con el modelo histórico."""
    Programacion = apps.get_model('accesos', 'Programacion')
    Jornada = apps.get_model('accesos', 'Jornada')
    ConfiguracionAccesos = apps.get_model('accesos', 'ConfiguracionAccesos')
    RegistroAuditoria = apps.get_model('accesos', 'RegistroAuditoria')
    Transporte = apps.get_model('accesos', 'Transporte')
    config, _ = ConfiguracionAccesos.objects.get_or_create(pk=1)
    hoy = datetime.now(dt_timezone.utc).astimezone(ZoneInfo('America/Lima')).date()
    primeras = {}
    for programacion in (Programacion.objects.filter(tipo='INTERNO', fecha__gte=hoy).exclude(estado='CANCELADA')
                         .order_by('fecha', 'hora', 'pk')):
        primeras.setdefault((programacion.transporte_id, programacion.fecha), programacion)
    for (transporte_id, fecha), primera in primeras.items():
        if Jornada.objects.filter(transporte_id=transporte_id, fecha=fecha).exists():
            continue
        jornada = Jornada.objects.create(
            transporte_id=transporte_id, fecha=fecha, cd_id=primera.cd_id, conductor_id=primera.conductor_id,
            hora_inicio=config.hora_pre_ruta, meta_pre_ruta_min=config.meta_pre_ruta_min,
            hora_cierre=config.hora_cierre_operativo, tolerancia_cierre_min=config.tolerancia_cierre_min)
        RegistroAuditoria.objects.create(
            usuario=None, accion='CREAR', entidad='Jornada', objeto_id=jornada.pk,
            objeto_repr=f"Jornada {Transporte.objects.get(pk=transporte_id).placa} {fecha:%d/%m/%Y}", cd_id=primera.cd_id,
            motivo=f"Pre-Ruta automática para una programación INTERNO existente (PRG-{primera.pk:06d}).")


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0010_jornada_flota_propia'),
    ]

    operations = [
        migrations.RunPython(cargar, migrations.RunPython.noop),
    ]
