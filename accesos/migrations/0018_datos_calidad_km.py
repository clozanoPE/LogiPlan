"""
Calidad del kilometraje (30/10/2026). Las lecturas «de relleno» (0 o cinco o más dígitos iguales: 99999, 999999)
que se escribieron porque el km era obligatorio y no se podía leer el odómetro pasan a «Excluido», con la auditoría
a nombre de «Sistema». Así no suman en el reporte ni sirven de referencia en portería. El resto de lecturas dudosas
(menores a la anterior, saltos, 0 km en el turno) las revisa el Administrador desde el Reporte de jornadas: no se
excluyen solas porque hay que saber cuál de las dos lecturas es la errada.
Después se recalcula el último km de cada transporte (solo con lecturas válidas, misma regla que kilometraje.py).
Idempotente. La reversa vuelve a «Registrado» solo lo que excluyó esta migración (según la auditoría).
"""
from django.db import migrations

MOTIVO = "Migración: lectura de relleno (0, 99999…) excluida del kilometraje."
ORIGENES = {'POST_RUTA': "Post-Ruta (fin de jornada)", 'PROGRAMADO': "Entrada al CD"}


def _relleno(km):
    texto = str(km)
    return km == 0 or (len(texto) >= 5 and len(set(texto)) == 1)


def _repr_tramo(tramo):
    return f"Tramo {tramo.transporte.placa} {tramo.fecha_local_entrada:%d/%m/%Y} {tramo.hora_local_entrada:%H:%M}"


def _ultimo_km(apps):
    Tramo = apps.get_model('accesos', 'Tramo')
    Transporte = apps.get_model('accesos', 'Transporte')
    for transporte in Transporte.objects.all():
        tramo = (Tramo.objects.filter(transporte_id=transporte.pk, vigente=True, km_entrada__isnull=False,
                                      km_estado='REGISTRADO')
                 .select_related('cd').order_by('-entrada_en', '-id').first())
        if tramo is None:
            Transporte.objects.filter(pk=transporte.pk).update(km_actual=None, km_actualizado_en=None, km_origen='')
            continue
        if tramo.clase == 'PRE_RUTA':
            origen = "Inicio de jornada (ingreso)" if tramo.vigilante_entrada_id else "Salida a ruta (Pre-Ruta)"
        else:
            origen = ORIGENES[tramo.clase]
        momento = tramo.salida_en if tramo.clase == 'PRE_RUTA' and tramo.salida_en else tramo.entrada_en
        Transporte.objects.filter(pk=transporte.pk).update(
            km_actual=tramo.km_entrada, km_actualizado_en=momento, km_origen=f"{origen} · CD {tramo.cd.nombre}"[:60])


def adelante(apps, schema_editor):
    Tramo = apps.get_model('accesos', 'Tramo')
    Auditoria = apps.get_model('accesos', 'RegistroAuditoria')
    for tramo in (Tramo.objects.filter(vigente=True, km_entrada__isnull=False, km_estado='REGISTRADO')
                  .select_related('transporte')):
        if not _relleno(tramo.km_entrada):
            continue
        tramo.km_estado = 'EXCLUIDO'
        tramo.km_motivo = "Valor de relleno: no es una lectura del odómetro"
        tramo.save(update_fields=['km_estado', 'km_motivo'])
        Auditoria.objects.create(
            usuario=None, accion='CORREGIR', entidad='Tramo', objeto_id=tramo.pk, objeto_repr=_repr_tramo(tramo)[:200],
            datos_antes={'km_estado': 'REGISTRADO', 'km_motivo': ''},
            datos_despues={'km_estado': 'EXCLUIDO', 'km_motivo': tramo.km_motivo}, motivo=MOTIVO, cd_id=tramo.cd_id)
    _ultimo_km(apps)


def atras(apps, schema_editor):
    Tramo = apps.get_model('accesos', 'Tramo')
    Auditoria = apps.get_model('accesos', 'RegistroAuditoria')
    ids = list(Auditoria.objects.filter(entidad='Tramo', motivo=MOTIVO).values_list('objeto_id', flat=True))
    Tramo.objects.filter(pk__in=ids, km_estado='EXCLUIDO').update(km_estado='REGISTRADO', km_motivo='')
    Auditoria.objects.filter(entidad='Tramo', motivo=MOTIVO).delete()
    _ultimo_km(apps)


class Migration(migrations.Migration):

    dependencies = [
        ('accesos', '0017_calidad_km'),
    ]

    operations = [
        migrations.RunPython(adelante, atras),
    ]
