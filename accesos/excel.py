"""
Exportación a Excel del módulo de Accesos, en memoria (BytesIO), con el mismo estilo que las
exportaciones de Planificación (cabecera verde Daryza). Es un helper propio para no acoplar
este módulo a `planificacion.views`.
"""
from io import BytesIO

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COLOR_CABECERA = '2BB673'


def respuesta_excel(nombre_archivo, hoja, encabezados, filas, formatos=None):
    """`formatos`: {índice de columna (0-based): number_format}. Las celdas no admiten
    datetimes con zona: pasar fechas y horas ya convertidas a hora de Lima, sin tzinfo."""
    formatos = formatos or {}
    libro = Workbook()
    ws = libro.active
    ws.title = hoja
    relleno = PatternFill(start_color=COLOR_CABECERA, end_color=COLOR_CABECERA, fill_type='solid')
    for columna, encabezado in enumerate(encabezados, start=1):
        celda = ws.cell(row=1, column=columna, value=encabezado)
        celda.fill = relleno
        celda.font = Font(color='FFFFFF', bold=True)
        celda.alignment = Alignment(horizontal='center')
    for numero_fila, fila in enumerate(filas, start=2):
        for indice, valor in enumerate(fila):
            celda = ws.cell(row=numero_fila, column=indice + 1, value=valor)
            if indice in formatos:
                celda.number_format = formatos[indice]
    for columna, encabezado in enumerate(encabezados, start=1):
        ws.column_dimensions[get_column_letter(columna)].width = max(len(encabezado) + 4, 14)
    ws.freeze_panes = 'A2'

    buffer = BytesIO()
    libro.save(buffer)
    respuesta = HttpResponse(buffer.getvalue(),
                             content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return respuesta
