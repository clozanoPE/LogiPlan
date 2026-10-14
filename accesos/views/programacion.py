"""
Programaciones (F2). Administrador y Despacho crean, editan y cancelan; Consulta solo lee.
Las reglas que dependen de la hora están en servicios.py y se evalúan con la hora de Lima.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import auditoria, consultas, excel, exportaciones, jornadas, qr, servicios, tiempo
from ..forms import CancelacionForm, ConductorRapidoForm, ProgramacionForm, TransporteForm
from ..models import CentroDistribucion, ConfiguracionAccesos, Programacion, RegistroAuditoria, TamanoVehiculo
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, rol_requerido

CAMPOS_COPIABLES = ['cd', 'tipo', 'transporte', 'conductor', 'documento', 'razon_social', 'motivo']


def _fecha_param(valor, por_defecto):
    try:
        return date.fromisoformat(valor) if valor else por_defecto
    except ValueError:
        return por_defecto


def _avisar(request, programacion):
    for aviso in servicios.avisos(programacion, ConfiguracionAccesos.obtener()):
        messages.warning(request, aviso)


def _motivos_frecuentes():
    return (Programacion.objects.values_list('motivo', flat=True)
            .order_by('motivo').distinct()[:30])


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def agenda(request):
    hoy = tiempo.hoy()
    ahora = tiempo.ahora()
    programaciones, filtros = consultas.programaciones_filtradas(
        request.GET, hoy=hoy, ahora=ahora, config=ConfiguracionAccesos.obtener())
    for programacion in programaciones:
        programacion.editable = servicios.motivo_no_editable(programacion, ahora) is None
    fecha = filtros['fecha']

    resumen = []
    for cd in CentroDistribucion.objects.filter(activo=True):
        del_cd = [p for p in programaciones if p.cd_id == cd.pk]
        resumen.append({
            'cd': cd,
            'activas': sum(1 for p in del_cd if not p.esta_cancelada),
            'cerradas': sum(1 for p in del_cd if p.estado_codigo == servicios.CERRADA),
            'en_planta': sum(1 for p in del_cd if p.estado_codigo == servicios.EN_PLANTA),
            'canceladas': sum(1 for p in del_cd if p.esta_cancelada),
        })

    cd_ids = {int(filtros['cd'])} if filtros['cd'].isdigit() else None
    return render(request, 'accesos/programacion/agenda.html', {
        'jornadas_dia': jornadas.jornadas_del_dia(fecha, ahora, cd_ids=cd_ids),
        'no_laborable': jornadas.no_laborables_entre(fecha, fecha).get(fecha),
        'fecha': fecha, 'fecha_larga': tiempo.fecha_larga(fecha), 'es_hoy': fecha == hoy,
        'dia_anterior': fecha - timedelta(days=1), 'dia_siguiente': fecha + timedelta(days=1),
        'programaciones': programaciones, 'resumen': resumen, 'filtros': filtros,
        'cds': CentroDistribucion.objects.filter(activo=True),
        'etiquetas_estado': servicios.ETIQUETAS_ESTADO,
    })


def respuesta_programaciones(request, *, cd=None):
    """Excel de programaciones con los filtros de la pantalla: el día mostrado o, con `desde`/`hasta`, un rango
    de hasta 31 días. `cd` fija el CD (Vigilancia)."""
    hoy, ahora = tiempo.hoy(), tiempo.ahora()
    fecha = consultas._fecha(request.GET.get('fecha'), hoy)
    desde, hasta, recortado = consultas.rango_exportar(request.GET, fecha)
    programaciones, _ = consultas.programaciones_filtradas(
        request.GET, hoy=hoy, ahora=ahora, config=ConfiguracionAccesos.obtener(), cd=cd, rango=(desde, hasta))
    encabezados, filas, formatos, notas = exportaciones.programaciones(programaciones, ahora)
    if recortado:
        notas.insert(0, f"El rango pedido superaba {consultas.MAX_RANGO_EXPORTAR_DIAS} días: se exportó del "
                        f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}.")
    sufijo = f"{desde:%Y%m%d}" if desde == hasta else f"{desde:%Y%m%d}_{hasta:%Y%m%d}"
    return excel.respuesta_excel(f"Programaciones_{sufijo}.xlsx", 'Programaciones', encabezados, filas,
                                 formatos, notas)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def exportar(request):
    return respuesta_programaciones(request)


def _render_form(request, form, programacion=None):
    config = ConfiguracionAccesos.obtener()
    return render(request, 'accesos/programacion/form.html', {
        'form': form, 'programacion': programacion,
        'horarios_sugeridos': [h.strip() for h in config.horarios_sugeridos.split(',') if h.strip()],
        'motivos_frecuentes': _motivos_frecuentes(),
        'ruc_empresa': config.ruc_empresa, 'razon_social_empresa': config.razon_social_empresa,
        # Registro rápido de transporte y conductor sin salir del formulario (prefijos para no chocar ids).
        'form_transporte': TransporteForm(prefix='tr', initial={'activo': True}),
        'form_conductor': ConductorRapidoForm(prefix='co', initial={'activo': True}),
        'hay_tamanos': TamanoVehiculo.objects.filter(activo=True).exists(),
    })


@rol_requerido(ADMINISTRADOR, DESPACHO)
def crear(request):
    if request.method == 'POST':
        form = ProgramacionForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                programacion = form.save(commit=False)
                programacion.creado_por = request.user
                programacion.save()
                auditoria.registrar_creacion(request.user, programacion)
            messages.success(request, f"Programación {programacion.codigo} registrada: {programacion.transporte} "
                                      f"el {programacion.fecha:%d/%m/%Y} a las {programacion.hora:%H:%M}.")
            _avisar(request, programacion)
            return redirect('accesos:programacion_detalle', pk=programacion.pk)
    else:
        inicial = {'fecha': _fecha_param(request.GET.get('fecha'), tiempo.hoy())}
        origen = request.GET.get('desde', '')
        if origen.isdigit():
            # «Nueva en otro horario»: copia los datos de una programación existente, sin la hora.
            base = Programacion.objects.filter(pk=int(origen)).values(*CAMPOS_COPIABLES, 'fecha').first()
            if base:
                inicial.update(base)
        form = ProgramacionForm(initial=inicial)
    return _render_form(request, form)


@rol_requerido(ADMINISTRADOR, DESPACHO)
def editar(request, pk):
    programacion = get_object_or_404(Programacion, pk=pk)
    impedimento = servicios.motivo_no_editable(programacion, tiempo.ahora())
    if impedimento:
        messages.error(request, f"No se puede editar {programacion.codigo}: {impedimento}")
        return redirect('accesos:programacion_detalle', pk=pk)

    foto_antes = auditoria.foto(programacion)
    form = ProgramacionForm(request.POST or None, instance=programacion)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            programacion = form.save(commit=False)
            programacion.modificado_por = request.user
            programacion.save()
            auditoria.registrar_modificacion(request.user, programacion, foto_antes)
        messages.success(request, f"Programación {programacion.codigo} actualizada.")
        _avisar(request, programacion)
        return redirect('accesos:programacion_detalle', pk=pk)
    return _render_form(request, form, programacion)


def _estado_jornada(jornada, ahora):
    abierto = jornada.transporte.tramos.filter(abierto=True).first()
    return jornadas.estado_visible(jornada, ahora, dentro=abierto is not None,
                                   en_visita=bool(abierto and abierto.es_programado),
                                   no_disponible=jornadas.incidencia_abierta(jornada.transporte_id) is not None)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA)
def detalle(request, pk):
    programacion = get_object_or_404(
        Programacion.objects.select_related('cd', 'transporte__tamano', 'conductor__tipo_documento',
                                            'creado_por', 'modificado_por', 'cancelado_por'),
        pk=pk,
    )
    ahora = tiempo.ahora()
    tramo = servicios.tramo_vigente(programacion)
    jornada = (jornadas.jornada_vigente(programacion.transporte_id, programacion.fecha)
               if programacion.tipo == Programacion.Tipo.INTERNO else None)
    return render(request, 'accesos/programacion/detalle.html', {
        'programacion': programacion,
        'jornada': jornada,
        'jornada_estado': _estado_jornada(jornada, ahora) if jornada else None,
        'tramo': tramo,
        'qr_svg': qr.svg(programacion.transporte.contenido_qr, escala=5),
        'tramo_permanencia': servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora)) if tramo else None,
        'fecha_larga': tiempo.fecha_larga(programacion.fecha),
        'estado_codigo': servicios.estado_visible(programacion, tiempo.hoy()),
        'etiquetas_estado': servicios.ETIQUETAS_ESTADO,
        'motivo_no_editable': servicios.motivo_no_editable(programacion, ahora),
        'motivo_no_cancelable': servicios.motivo_no_cancelable(programacion, request.user, ahora),
        'form_cancelacion': CancelacionForm(),
        'historial': RegistroAuditoria.objects.filter(entidad='Programacion', objeto_id=pk).select_related('usuario'),
    })


@require_POST
@rol_requerido(ADMINISTRADOR, DESPACHO)
def cancelar(request, pk):
    programacion = get_object_or_404(Programacion, pk=pk)
    form = CancelacionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indique el motivo de la cancelación.")
        return redirect('accesos:programacion_detalle', pk=pk)
    try:
        servicios.cancelar(programacion, request.user, form.cleaned_data['motivo'], tiempo.ahora())
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, f"Programación {programacion.codigo} cancelada.")
    return redirect('accesos:programacion_detalle', pk=pk)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def pase(request, pk):
    """Pase imprimible de la programación con el QR de la placa (sirve para cualquier tipo de vehículo)."""
    programacion = get_object_or_404(
        Programacion.objects.select_related('cd', 'transporte__tamano', 'conductor__tipo_documento'), pk=pk)
    return render(request, 'accesos/programacion/pase.html', {
        'programacion': programacion,
        'fecha_larga': tiempo.fecha_larga(programacion.fecha),
        'qr_svg': qr.svg(programacion.transporte.contenido_qr, escala=8),
    })
