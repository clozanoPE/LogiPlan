from datetime import date, datetime, time, timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from .. import tiempo

# 07/10/2026 22:42 en Lima = 08/10/2026 03:42 en UTC (caso real observado en producción).
NOCHE_EN_LIMA_UTC = datetime(2026, 10, 8, 3, 42, tzinfo=dt_timezone.utc)


class CapaDeTiempoTests(SimpleTestCase):

    @mock.patch('accesos.tiempo.timezone.now', return_value=NOCHE_EN_LIMA_UTC)
    def test_hoy_es_la_fecha_de_lima_aunque_utc_ya_cambio_de_dia(self, _now):
        self.assertEqual(tiempo.hoy(), date(2026, 10, 7))
        self.assertEqual(tiempo.ahora().hour, 22)
        self.assertEqual(tiempo.ahora().utcoffset().total_seconds(), -5 * 3600)

    @mock.patch('accesos.tiempo.timezone.now',
                return_value=datetime(2026, 10, 7, 5, 30, tzinfo=dt_timezone.utc))
    def test_media_noche_y_media_en_lima(self, _now):
        # 00:30 del 07/10 en Lima.
        self.assertEqual(tiempo.hoy(), date(2026, 10, 7))
        self.assertEqual(tiempo.ahora().time(), time(0, 30))

    def test_combinar_crea_un_momento_en_hora_de_lima(self):
        momento = tiempo.combinar(date(2026, 10, 8), time(9, 15))
        self.assertEqual(momento.astimezone(dt_timezone.utc), datetime(2026, 10, 8, 14, 15, tzinfo=dt_timezone.utc))


class SinRelojDelSistemaTests(SimpleTestCase):
    """Dentro de accesos está prohibido usar el reloj del sistema operativo."""

    PROHIBIDOS = ('date.today(', 'datetime.now(', 'datetime.today(', 'datetime.utcnow(')

    def test_el_codigo_del_modulo_no_usa_date_today_ni_datetime_now(self):
        raiz = Path(__file__).resolve().parent.parent
        infractores = []
        for archivo in raiz.rglob('*.py'):
            # tiempo.py es la única fuente de hora y nombra estas funciones en su documentación.
            if 'tests' in archivo.parts or 'migrations' in archivo.parts or archivo.name == 'tiempo.py':
                continue
            texto = archivo.read_text(encoding='utf-8')
            infractores += [f"{archivo.name}: {p}" for p in self.PROHIBIDOS if p in texto]
        self.assertEqual(infractores, [], "Use accesos.tiempo.ahora() / hoy() en su lugar.")
