"""
Generación de QR en memoria con `segno` (Python puro, sin dependencias). Los QR no se
guardan en disco: el filesystem de Railway es efímero, así que se generan al pedirlos.

El contenido es la placa normalizada (solo letras y números), para que un lector físico
configurado con otro idioma de teclado no altere caracteres como '-'.
"""
from io import BytesIO

import segno


def _qr(contenido):
    return segno.make_qr(contenido, error='m')


def svg(contenido, escala=10):
    """Etiqueta <svg> lista para incrustar en una plantilla."""
    return _qr(contenido).svg_inline(scale=escala, border=2)


def png(contenido, escala=12):
    buffer = BytesIO()
    _qr(contenido).save(buffer, kind='png', scale=escala, border=2)
    return buffer.getvalue()
