# Turno noche de la flota propia (15/10/2026): la jornada suma el turno (DÍA / NOCHE), una por placa, fecha y turno;
# la configuración suma los horarios del turno noche y cada programación queda ligada a su jornada.
# La regla única nueva se crea ANTES de quitar la anterior (MySQL usa ese índice para la llave foránea del transporte).

import datetime
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0014_datos_km_salida'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='jornada',
            options={'ordering': ['-fecha', 'transporte__placa_normalizada', 'turno'], 'verbose_name': 'Jornada de flota propia', 'verbose_name_plural': 'Jornadas de flota propia'},
        ),
        migrations.AddField(
            model_name='configuracionaccesos',
            name='hora_cierre_noche',
            field=models.TimeField(default=datetime.time(6, 15), help_text='Retorno esperado del turno noche (día siguiente). Las salidas hasta esta hora son del turno noche del día anterior.', verbose_name='Hora de cierre del turno noche'),
        ),
        migrations.AddField(
            model_name='configuracionaccesos',
            name='hora_pre_ruta_noche',
            field=models.TimeField(default=datetime.time(20, 0), help_text='Desde esta hora una ruta INTERNO es del turno noche y empieza su Pre-Ruta.', verbose_name='Hora de inicio del turno noche (Pre-Ruta noche)'),
        ),
        migrations.AddField(
            model_name='configuracionaccesos',
            name='meta_pre_ruta_noche_min',
            field=models.PositiveSmallIntegerField(default=60, help_text='Minutos para salir a ruta desde el inicio del turno noche; después, alerta.', verbose_name='Meta de la Pre-Ruta noche (min)'),
        ),
        migrations.AddField(
            model_name='configuracionaccesos',
            name='tolerancia_cierre_noche_min',
            field=models.PositiveSmallIntegerField(default=15, help_text='Un retorno del turno noche después del cierre más esta tolerancia es «fuera de horario».', verbose_name='Tolerancia del cierre del turno noche (min)'),
        ),
        migrations.AddField(
            model_name='jornada',
            name='turno',
            field=models.CharField(choices=[('DIA', 'Día'), ('NOCHE', 'Noche')], db_index=True, default='DIA', max_length=5),
        ),
        migrations.AddConstraint(
            model_name='jornada',
            constraint=models.UniqueConstraint(fields=('transporte', 'fecha', 'turno'), name='accesos_jornada_una_por_turno'),
        ),
        migrations.RemoveConstraint(
            model_name='jornada',
            name='accesos_jornada_una_por_dia',
        ),
        migrations.AddField(
            model_name='programacion',
            name='jornada',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='programaciones', to='accesos.jornada'),
        ),
        migrations.AlterField(
            model_name='jornada',
            name='conductor',
            field=models.ForeignKey(help_text='Conductor previsto: el de la primera programación del turno.', on_delete=django.db.models.deletion.PROTECT, related_name='jornadas', to='accesos.conductor'),
        ),
        migrations.AlterField(
            model_name='jornada',
            name='fecha',
            field=models.DateField(db_index=True, help_text='Día en que empieza el turno.'),
        ),
        migrations.AddConstraint(
            model_name='configuracionaccesos',
            constraint=models.CheckConstraint(condition=models.Q(('meta_pre_ruta_noche_min__gt', 0)), name='accesos_config_meta_pre_ruta_noche_positiva'),
        ),
    ]
