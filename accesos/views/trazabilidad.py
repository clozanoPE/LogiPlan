"""
Trazabilidad de una placa en un día: lo programado frente a lo real, en orden (ver accesos/trazabilidad.py).
Solo lectura. Administrador, Despacho y Consulta ven todos los CD; Vigilancia, solo los suyos.
"""
from datetime import date, timedelta

from django.shortcuts import render

from .. import excel, tiempo, trazabilidad
from ..models import ConfiguracionAccesos, Transporte, normalizar_placa
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, rol_requerido
from .control import cds_de_tramos


def _consulta(request):
    """(transporte o None, fecha, texto de la placa, mensaje) a partir de los parámetros GET."""
    hoy = tiempo.hoy()
    try:
        fecha = date.fromisoformat(request.GET.get('fecha', ''))
    except ValueError:
        fecha = hoy
    texto = request.GET.get('placa', '').strip()
    transporte = None
    mensaje = ''
    if texto:
        transporte = Transporte.objects.select_related('tamano').filter(placa_normalizada=normalizar_placa(texto)).first()
        if transporte is None:
            mensaje = f"La placa {texto.upper()} no está registrada."
    return transporte, fecha, texto, mensaje


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
@tiempo.con_hora_lima
def trazabilidad_placa(request):
    transporte, fecha, texto, mensaje = _consulta(request)
    contexto = {
        'texto': texto, 'fecha': fecha, 'mensaje': mensaje, 'transporte': transporte,
        'fecha_larga': tiempo.fecha_larga(fecha), 'anterior': fecha - timedelta(days=1),
        'siguiente': fecha + timedelta(days=1), 'hoy': tiempo.hoy(),
        'placas': Transporte.objects.filter(activo=True).order_by('placa_normalizada').values_list('placa', flat=True),
    }
    if transporte is not None:
        config = ConfiguracionAccesos.obtener()
        contexto.update(trazabilidad.del_dia(transporte, fecha, tiempo.ahora(), config, cd_ids=cds_de_tramos(request.user)))
        contexto['config'] = config
    return render(request, 'accesos/trazabilidad/trazabilidad.html', contexto)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def trazabilidad_exportar(request):
    transporte, fecha, texto, _ = _consulta(request)
    encabezados = ['Fecha', 'Hora', 'Evento', 'Hora programada', 'Referencia', 'Desviación (min)', 'Lectura',
                   'Calificación', 'Kilometraje', 'CD', 'Conductor', 'Vigilante', 'Detalle', 'Observación', 'Marcas',
                   'Turno']
    filas = []
    if transporte is not None:
        datos = trazabilidad.del_dia(transporte, fecha, tiempo.ahora(), ConfiguracionAccesos.obtener(),
                                     cd_ids=cds_de_tramos(request.user))
        for e in datos['eventos']:
            momento = tiempo.a_lima(e.momento)
            filas.append([momento.date(), momento.time().replace(second=0, microsecond=0), e.titulo, e.programada,
                          e.referencia, e.desviacion, e.lectura, e.etiqueta, e.km, str(e.cd or ''), e.conductor,
                          e.vigilante, e.detalle, e.obs, ', '.join(e.marcas), 'Noche' if e.turno_noche else ''])
    placa = transporte.placa_normalizada if transporte else normalizar_placa(texto) or 'placa'
    nombre = f"Trazabilidad_{placa}_{fecha:%Y%m%d}.xlsx"
    formatos = {0: 'DD/MM/YYYY', 1: 'HH:MM', 3: 'HH:MM', 8: '#,##0'}
    return excel.respuesta_excel(nombre, 'Trazabilidad', encabezados, filas, formatos)
