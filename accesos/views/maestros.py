"""
Maestros de Transporte y Conductor. Administrador y Despacho los consultan y dan de alta;
solo el Administrador edita transportes e imprime los QR. En Conductores, el Administrador edita
todo y Despacho (quien alimenta las licencias) edita solo la licencia y sus notificaciones.
Nada se borra: se desactiva.
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import auditoria, jornadas, licencias, qr, tiempo
from ..forms import (ConductorForm, ConductorRapidoForm, TransporteForm, etiqueta_conductor,
                     etiqueta_transporte)
from ..models import ConfiguracionAccesos, Conductor, Transporte, normalizar_placa
from ..paginacion import paginar
from ..permisos import ADMINISTRADOR, DESPACHO, rol_requerido, roles_de

def _crear_con_auditoria(form, usuario):
    """Alta común a la pantalla del maestro y al registro rápido desde la programación."""
    with transaction.atomic():
        objeto = form.save(commit=False)
        objeto.creado_por = usuario
        objeto.save()
        auditoria.registrar_creacion(usuario, objeto)
    return objeto


def _respuesta_rapida(form, usuario, etiqueta):
    """Registro rápido (JSON) usado por las ventanas del formulario de programación."""
    if not form.is_valid():
        return JsonResponse({'ok': False, 'errores': form.errors.get_json_data()}, status=400)
    objeto = _crear_con_auditoria(form, usuario)
    return JsonResponse({'ok': True, 'id': objeto.pk, 'etiqueta': etiqueta(objeto)})


# --- TRANSPORTES ---

@rol_requerido(ADMINISTRADOR, DESPACHO)
def transportes_lista(request):
    busqueda = request.GET.get('q', '').strip()
    transportes = Transporte.objects.select_related('tamano')
    if busqueda:
        transportes = transportes.filter(placa_normalizada__icontains=normalizar_placa(busqueda))
    return render(request, 'accesos/maestros/transporte_lista.html', {
        **paginar(request, transportes), 'busqueda': busqueda, 'no_disponibles': jornadas.ids_no_disponibles(),
    })


@rol_requerido(ADMINISTRADOR, DESPACHO)
def transporte_crear(request):
    form = TransporteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        transporte = _crear_con_auditoria(form, request.user)
        messages.success(request, f"Transporte {transporte.placa} registrado.")
        return redirect('accesos:transportes_lista')
    return render(request, 'accesos/maestros/transporte_form.html', {'form': form})


@rol_requerido(ADMINISTRADOR)
def transporte_editar(request, pk):
    transporte = get_object_or_404(Transporte, pk=pk)
    foto_antes = auditoria.foto(transporte)
    form = TransporteForm(request.POST or None, instance=transporte)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            transporte = form.save(commit=False)
            transporte.modificado_por = request.user
            transporte.save()
            auditoria.registrar_modificacion(request.user, transporte, foto_antes)
        messages.success(request, f"Transporte {transporte.placa} actualizado.")
        return redirect('accesos:transportes_lista')
    return render(request, 'accesos/maestros/transporte_form.html', {'form': form, 'transporte': transporte})


@rol_requerido(ADMINISTRADOR)
def transporte_qr(request, pk):
    transporte = get_object_or_404(Transporte, pk=pk)
    if not transporte.es_interno:
        messages.warning(request, f"{transporte.placa} no es un transporte interno: solo los internos llevan QR.")
        return redirect('accesos:transportes_lista')
    return render(request, 'accesos/maestros/transporte_qr.html', {
        'transporte': transporte,
        'qr_svg': qr.svg(transporte.contenido_qr),
    })


@rol_requerido(ADMINISTRADOR)
def transporte_qr_png(request, pk):
    transporte = get_object_or_404(Transporte, pk=pk, es_interno=True)
    respuesta = HttpResponse(qr.png(transporte.contenido_qr), content_type='image/png')
    respuesta['Content-Disposition'] = f'attachment; filename="QR_{transporte.placa_normalizada}.png"'
    return respuesta


# --- CONDUCTORES ---

FILTROS_LICENCIA = [('', 'Todas las licencias'), ('alerta', 'Por vencer o vencidas (en aviso)'),
                    ('vencidas', 'Con alguna licencia vencida'), ('sin', 'Sin licencia registrada')]


@rol_requerido(ADMINISTRADOR, DESPACHO)
def conductores_lista(request):
    busqueda = request.GET.get('q', '').strip()
    filtro_licencia = request.GET.get('licencia', '')
    hoy, config = tiempo.hoy(), ConfiguracionAccesos.obtener()
    conductores = Conductor.objects.select_related('tipo_documento')
    if busqueda:
        conductores = conductores.filter(
            Q(numero_documento__icontains=busqueda)
            | Q(apellido_paterno__icontains=busqueda)
            | Q(apellido_materno__icontains=busqueda)
            | Q(nombres__icontains=busqueda)
            | Q(licencia_numero__icontains=busqueda)
        )
    if filtro_licencia == 'alerta':
        conductores = conductores.filter(licencias.filtro_en_alerta(hoy, config))
    elif filtro_licencia == 'vencidas':
        conductores = conductores.filter(Q(licencia_vencimiento__lt=hoy) | Q(licencia_especial_vencimiento__lt=hoy))
    elif filtro_licencia == 'sin':
        conductores = conductores.filter(licencia_vencimiento__isnull=True, licencia_especial_vencimiento__isnull=True)
    else:
        filtro_licencia = ''
    # El id desempata a los homónimos: sin él, un conductor podría repetirse o faltar al cambiar de página.
    contexto = paginar(request, conductores.order_by('apellido_paterno', 'apellido_materno', 'nombres', 'id'))
    for conductor in contexto['page_obj']:
        conductor.estados_licencia = [(etiqueta, categoria, vencimiento, licencias.estado(vencimiento, hoy, config))
                                      for etiqueta, categoria, vencimiento in conductor.licencias()]
    return render(request, 'accesos/maestros/conductor_lista.html', {
        **contexto, 'busqueda': busqueda,
        'filtro_licencia': filtro_licencia, 'filtros_licencia': FILTROS_LICENCIA,
        'aviso_dias': config.aviso_licencia_dias,
    })


@rol_requerido(ADMINISTRADOR, DESPACHO)
def conductor_crear(request):
    form = ConductorForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        conductor = _crear_con_auditoria(form, request.user)
        messages.success(request, f"Conductor {conductor.nombre_completo} registrado.")
        return redirect('accesos:conductores_lista')
    return render(request, 'accesos/maestros/conductor_form.html', {
        'form': form, 'config_licencias': ConfiguracionAccesos.obtener()})


@rol_requerido(ADMINISTRADOR, DESPACHO)
def conductor_editar(request, pk):
    conductor = get_object_or_404(Conductor, pk=pk)
    foto_antes = auditoria.foto(conductor)
    solo_licencia = ADMINISTRADOR not in roles_de(request.user)
    form = ConductorForm(request.POST or None, instance=conductor, solo_licencia=solo_licencia)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            conductor = form.save(commit=False)
            conductor.modificado_por = request.user
            conductor.save()
            auditoria.registrar_modificacion(request.user, conductor, foto_antes)
        messages.success(request, f"Conductor {conductor.nombre_completo} actualizado.")
        return redirect('accesos:conductores_lista')
    return render(request, 'accesos/maestros/conductor_form.html', {
        'form': form, 'conductor': conductor, 'solo_licencia': solo_licencia,
        'config_licencias': ConfiguracionAccesos.obtener()})


# --- REGISTRO RÁPIDO DESDE LA PROGRAMACIÓN (Despacho, sin salir del formulario) ---

@require_POST
@rol_requerido(ADMINISTRADOR, DESPACHO)
def transporte_crear_rapido(request):
    return _respuesta_rapida(TransporteForm(request.POST, prefix='tr'), request.user, etiqueta_transporte)


@require_POST
@rol_requerido(ADMINISTRADOR, DESPACHO)
def conductor_crear_rapido(request):
    return _respuesta_rapida(ConductorRapidoForm(request.POST, prefix='co'), request.user, etiqueta_conductor)
