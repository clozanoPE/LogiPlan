"""
Último kilometraje de cada transporte. Los tramos válidos son la fuente de verdad: Vigilancia registra el
kilometraje en cada entrada, en el inicio de jornada, en la Post-Ruta y en la salida a ruta de una unidad que
durmió en el CD sin registro. El maestro de Transporte guarda una copia (`km_actual`, `km_actualizado_en`,
`km_origen`) para mostrarla sin consultas extra; se recalcula cada vez que se guarda un tramo (Tramo.save),
así que también sigue a las correcciones y anulaciones del Administrador.
"""
from .models import Tramo, Transporte

Clase = Tramo.Clase


def ultima_lectura(transporte_id):
    """Tramo válido más reciente de la placa que tiene kilometraje, o None."""
    return (Tramo.objects.filter(transporte_id=transporte_id, vigente=True, km_entrada__isnull=False)
            .select_related('cd').order_by('-entrada_en', '-id').first())


def momento_lectura(tramo):
    """Cuándo se leyó el kilometraje: en la Pre-Ruta, al salir a ruta; en los demás tramos, al entrar."""
    if tramo.clase == Clase.PRE_RUTA and tramo.salida_en:
        return tramo.salida_en
    return tramo.entrada_en


def origen(tramo):
    if tramo.clase == Clase.POST_RUTA:
        return "Post-Ruta (fin de jornada)"
    if tramo.clase == Clase.PRE_RUTA:
        return "Inicio de jornada (ingreso)" if tramo.vigilante_entrada_id else "Salida a ruta (Pre-Ruta)"
    return "Entrada al CD"


def actualizar(transporte_id):
    """Recalcula la copia del último kilometraje en el maestro. Usa update() para no tocar los datos de
    modificación del transporte (no es un cambio del usuario sobre el maestro)."""
    tramo = ultima_lectura(transporte_id)
    if tramo is None:
        datos = {'km_actual': None, 'km_actualizado_en': None, 'km_origen': ''}
    else:
        datos = {'km_actual': tramo.km_entrada, 'km_actualizado_en': momento_lectura(tramo),
                 'km_origen': f"{origen(tramo)} · CD {tramo.cd}"[:60]}
    Transporte.objects.filter(pk=transporte_id).update(**datos)
    return datos
