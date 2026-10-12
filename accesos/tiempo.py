"""
Única fuente de fecha y hora del módulo de Accesos.

El proyecto trabaja con TIME_ZONE='UTC' y el servidor de Railway también está en UTC,
pero la operación ocurre en Lima. Todo el código de `accesos` debe obtener "ahora" y
"hoy" desde aquí, nunca con date.today() ni datetime.now(): esas funciones dependen del
reloj del sistema operativo y se comportan distinto en local (Lima) y en Railway (UTC).

Lima no tiene horario de verano (UTC-5 fijo), por lo que no existen horas ambiguas.
"""
from datetime import date, datetime, time
from functools import wraps
from zoneinfo import ZoneInfo

from django.utils import timezone

ZONA_LIMA = ZoneInfo('America/Lima')


def ahora() -> datetime:
    """Fecha y hora actual, consciente de zona, expresada en hora de Lima."""
    return timezone.now().astimezone(ZONA_LIMA)


def hoy() -> date:
    """Fecha civil de hoy en Lima (no la del servidor)."""
    return ahora().date()


def combinar(fecha: date, hora: time) -> datetime:
    """Une una fecha y una hora civiles de Lima en un datetime consciente de zona."""
    return datetime.combine(fecha, hora, tzinfo=ZONA_LIMA)


DIAS_SEMANA = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')


def fecha_larga(fecha: date) -> str:
    """'Miércoles 07/10/2026'. En español sin depender de LANGUAGE_CODE (que es en-us)."""
    return f"{DIAS_SEMANA[fecha.weekday()]} {fecha:%d/%m/%Y}"


def a_lima(momento: datetime) -> datetime:
    """Convierte un datetime consciente de zona (p. ej. leído de la BD en UTC) a hora de Lima."""
    return momento.astimezone(ZONA_LIMA)


def con_hora_lima(vista):
    """Ejecuta la vista con America/Lima como zona activa: los formularios con fecha y hora
    interpretan y muestran la hora de Lima, sin cambiar TIME_ZONE global del proyecto."""
    @wraps(vista)
    def envoltura(*args, **kwargs):
        with timezone.override(ZONA_LIMA):
            return vista(*args, **kwargs)
    return envoltura
