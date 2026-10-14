"""
Disponibilidad de la flota (F6): vehículos no disponibles por avería, programaciones afectadas e
historial de incidencias. Administrador y Despacho registran la no disponibilidad (avería en ruta o
detectada en el CD; la de portería la registra Vigilancia al ingreso) y habilitan el vehículo cuando
vuelve a operar. Consulta solo lee.
"""
from collections import Counter

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import excel, exportaciones, jornadas, servicios, tiempo
from ..forms import HabilitarForm, NoDisponibilidadForm
from ..models import Incidencia
from ..paginacion import paginar
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, rol_requerido, tiene_rol


def _duracion(desde, hasta):
    minutos = int((hasta - desde).total_seconds() // 60)
    dias, resto = divmod(minutos, 24 * 60)
    return f"{dias} d {resto // 60} h" if dias else servicios.formato_duracion(minutos)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def disponibilidad(request):
    ahora = tiempo.ahora()
    abiertas = list(Incidencia.objects.filter(abierta=True)
                    .select_related('transporte__tamano', 'tipo', 'registrada_por', 'cd').order_by('inicio'))
    afectadas = list(jornadas.programaciones_afectadas(ahora.date()))
    for incidencia in abiertas:
        incidencia.tiempo = _duracion(incidencia.inicio, ahora)
        incidencia.afectadas = [p for p in afectadas if p.transporte_id == incidencia.transporte_id]
    historial = (Incidencia.objects.filter(abierta__isnull=True)
                 .select_related('transporte', 'tipo', 'registrada_por', 'cerrada_por').order_by('-fin', '-id'))
    contexto = paginar(request, historial)
    for incidencia in contexto['page_obj']:
        incidencia.tiempo = _duracion(incidencia.inicio, incidencia.fin)
    puede_gestionar = tiene_rol(request.user, ADMINISTRADOR, DESPACHO)
    inicial = {'transporte': request.GET.get('transporte')} if request.GET.get('transporte', '').isdigit() else None
    return render(request, 'accesos/flota/disponibilidad.html', {
        **contexto, 'abiertas': abiertas, 'total_afectadas': len(afectadas), 'puede_gestionar': puede_gestionar,
        'form': NoDisponibilidadForm(initial=inicial) if puede_gestionar else None,
        'form_habilitar': HabilitarForm(),
    })


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def disponibilidad_exportar(request):
    """Todas las incidencias (no disponibles ahora e historial), con sus programaciones por reasignar."""
    ahora = tiempo.ahora()
    afectadas = Counter(p.transporte_id for p in jornadas.programaciones_afectadas(ahora.date()))
    incidencias = Incidencia.objects.select_related('transporte__tamano', 'tipo', 'cd', 'registrada_por',
                                                    'cerrada_por').order_by('-inicio', '-id')
    encabezados, filas, formatos, notas = exportaciones.incidencias(incidencias, afectadas, ahora)
    return excel.respuesta_excel(f"Disponibilidad_flota_{tiempo.hoy():%Y%m%d}.xlsx", 'Incidencias', encabezados,
                                 filas, formatos, notas)


@require_POST
@rol_requerido(ADMINISTRADOR, DESPACHO)
def registrar_no_disponible(request):
    form = NoDisponibilidadForm(request.POST)
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return redirect('accesos:flota_disponibilidad')
    datos = form.cleaned_data
    try:
        incidencia, canceladas = jornadas.registrar_no_disponibilidad(
            transporte_id=datos['transporte'].pk, tipo_id=datos['tipo'].pk, descripcion=datos['descripcion'],
            usuario=request.user, ahora=tiempo.ahora())
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('accesos:flota_disponibilidad')
    texto = f"{incidencia.codigo}: {incidencia.transporte} queda NO DISPONIBLE."
    if canceladas:
        texto += f" Se cancelaron {len(canceladas)} programación(es) pendiente(s) de hoy."
    messages.success(request, texto)
    futuras = jornadas.programaciones_afectadas(tiempo.hoy(), [incidencia.transporte_id]).count()
    if futuras:
        messages.warning(request, f"Hay {futuras} programación(es) de días siguientes con esta placa: cámbieles la "
                                  "placa o cancélelas.")
    return redirect('accesos:flota_disponibilidad')


@require_POST
@rol_requerido(ADMINISTRADOR, DESPACHO)
def habilitar(request, pk):
    incidencia = get_object_or_404(Incidencia, pk=pk)
    form = HabilitarForm(request.POST)
    form.is_valid()
    try:
        jornadas.habilitar(incidencia, usuario=request.user, ahora=tiempo.ahora(),
                           observacion=form.cleaned_data.get('observacion', ''))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f"{incidencia.transporte} habilitado: vuelve a estar disponible para programar.")
    return redirect('accesos:flota_disponibilidad')
