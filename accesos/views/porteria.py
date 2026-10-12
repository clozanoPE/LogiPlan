"""
Portería (F3), pensada para tablet. Solo el rol Vigilancia registra entradas y salidas, y solo
en los CD que tiene autorizados (UsuarioCD). El vigilante nunca elige «entrada» o «salida»:
el sistema lo decide según exista o no un tramo abierto de la placa.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from .. import consultas, servicios, tiempo
from ..forms import EntradaForm, SalidaForm
from ..models import CentroDistribucion, Conductor, ConfiguracionAccesos, Tramo
from ..permisos import VIGILANCIA, rol_requerido

CLAVE_SESION_CD = 'accesos_cd_id'


def cds_autorizados(user):
    return CentroDistribucion.objects.filter(activo=True, usuariocd__usuario=user).distinct()


def cd_activo(request):
    """CD en el que opera el vigilante: el elegido en la sesión, o el único que tiene autorizado."""
    autorizados = cds_autorizados(request.user)
    cd_id = request.session.get(CLAVE_SESION_CD)
    cd = autorizados.filter(pk=cd_id).first() if cd_id else None
    if cd is None and autorizados.count() == 1:
        cd = autorizados.first()
        request.session[CLAVE_SESION_CD] = cd.pk
    return cd


def _requiere_cd(vista):
    """Envuelve una vista de portería: sin CD activo, lleva a elegirlo."""
    def envoltura(request, *args, **kwargs):
        cd = cd_activo(request)
        if cd is None:
            return redirect('accesos:porteria_cd')
        return vista(request, cd, *args, **kwargs)
    envoltura.__name__ = vista.__name__
    envoltura.__doc__ = vista.__doc__
    return envoltura


def _contexto_cd(request, cd):
    abiertos = Tramo.objects.filter(cd=cd, abierto=True).count()
    return {'cd': cd, 'dentro_ahora': abiertos, 'varios_cd': cds_autorizados(request.user).count() > 1}


@rol_requerido(VIGILANCIA)
def elegir_cd(request):
    autorizados = cds_autorizados(request.user)
    if request.method == 'POST':
        cd = autorizados.filter(pk=request.POST.get('cd') or 0).first()
        if cd:
            request.session[CLAVE_SESION_CD] = cd.pk
            return redirect('accesos:porteria')
        messages.error(request, "Elija uno de sus CD autorizados.")
    return render(request, 'accesos/porteria/elegir_cd.html', {'autorizados': autorizados})


@rol_requerido(VIGILANCIA)
@_requiere_cd
def porteria(request, cd):
    """Pantalla principal: escanear QR o digitar placa; muestra ENTRADA, SALIDA o bloqueo."""
    contexto = _contexto_cd(request, cd)
    texto = request.GET.get('placa', '').strip()
    if texto:
        ahora = tiempo.ahora()
        config = ConfiguracionAccesos.obtener()
        resultado = servicios.evaluar_porteria(texto, cd, ahora, config)
        contexto.update({'texto': texto, 'resultado': resultado})
        if resultado.accion == servicios.SALIDA:
            tramo = resultado.tramo_abierto
            contexto.update({
                'minutos_dentro': servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora)),
                'prolongada': servicios.es_prolongada(tramo, ahora, config),
                'numero_tramo': servicios.numero_tramo_del_dia(tramo),
            })
        elif resultado.accion == servicios.ENTRADA:
            for candidata in resultado.candidatas:
                minutos = abs(candidata.minutos)
                if minutos == 0:
                    candidata.descripcion = "Llega a la hora programada"
                elif candidata.minutos > 0:
                    candidata.descripcion = f"Llega {servicios.formato_duracion(minutos)} antes"
                else:
                    candidata.descripcion = f"Llega {servicios.formato_duracion(minutos)} tarde"
            contexto.update({
                'conductores': Conductor.objects.filter(activo=True).select_related('tipo_documento'),
                'ventana': config.ventana_sugerencia_min,
            })
    contexto['registrado'] = request.GET.get('registrado', '')
    contexto['registrado_placa'] = request.GET.get('registrado_placa', '')
    return render(request, 'accesos/porteria/porteria.html', contexto)


def _volver(placa=None, **extra):
    parametros = {'placa': placa} if placa else {}
    parametros.update(extra)
    url = reverse('accesos:porteria')
    return redirect(f"{url}?{urlencode(parametros)}" if parametros else url)


@require_POST
@rol_requerido(VIGILANCIA)
@_requiere_cd
def registrar_entrada(request, cd):
    form = EntradaForm(request.POST)
    placa = request.POST.get('placa', '')
    if not form.is_valid():
        for errores in form.errors.values():
            for error in errores:
                messages.error(request, error)
        return _volver(placa)
    datos = form.cleaned_data
    try:
        tramo, avisos = servicios.registrar_entrada(
            transporte_id=datos['transporte'], programacion_id=datos['programacion'], cd=cd, km=datos['km'],
            obs=datos['obs'], conductor_id=datos['conductor'], usuario=request.user, ahora=tiempo.ahora(),
            config=ConfiguracionAccesos.obtener(), confirmar_fuera_de_ventana=datos['confirmar_fuera_de_ventana'],
        )
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return _volver(placa)
    for aviso in avisos:
        messages.warning(request, aviso)
    return _volver(registrado='ENTRADA', registrado_placa=tramo.transporte.placa)


@require_POST
@rol_requerido(VIGILANCIA)
@_requiere_cd
def registrar_salida(request, cd):
    form = SalidaForm(request.POST)
    placa = request.POST.get('placa', '')
    if not form.is_valid():
        messages.error(request, "Datos incompletos. Vuelva a escanear la placa.")
        return _volver(placa)
    datos = form.cleaned_data
    try:
        tramo = servicios.registrar_salida(transporte_id=datos['transporte'], tramo_id=datos['tramo'], cd=cd,
                                           obs=datos['obs'], usuario=request.user, ahora=tiempo.ahora())
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return _volver(placa)
    return _volver(registrado='SALIDA', registrado_placa=tramo.transporte.placa)


@rol_requerido(VIGILANCIA)
@_requiere_cd
def dentro_ahora(request, cd):
    ahora = tiempo.ahora()
    config = ConfiguracionAccesos.obtener()
    tramos = list(Tramo.objects.filter(cd=cd, abierto=True)
                  .select_related('transporte__tamano', 'programacion', 'conductor').order_by('entrada_en'))
    for tramo in tramos:
        tramo.tiempo_dentro = servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora))
        tramo.prolongada = servicios.es_prolongada(tramo, ahora, config)
    contexto = _contexto_cd(request, cd)
    contexto.update({'tramos': tramos, 'umbral': config.umbral_prolongada_min})
    return render(request, 'accesos/porteria/dentro_ahora.html', contexto)


@rol_requerido(VIGILANCIA)
@_requiere_cd
def programaciones_hoy(request, cd):
    """Consulta de solo lectura de las programaciones del CD del vigilante, con filtros de
    fecha, placa, conductor y estado, y los tiempos reales de cada una."""
    hoy = tiempo.hoy()
    programaciones, filtros = consultas.programaciones_filtradas(
        request.GET, hoy=hoy, ahora=tiempo.ahora(), config=ConfiguracionAccesos.obtener(), cd=cd)
    contexto = _contexto_cd(request, cd)
    contexto.update({'programaciones': programaciones, 'filtros': filtros, 'es_hoy': filtros['fecha'] == hoy,
                     'fecha_larga': tiempo.fecha_larga(filtros['fecha']),
                     'etiquetas_estado': servicios.ETIQUETAS_ESTADO})
    return render(request, 'accesos/porteria/programaciones_hoy.html', contexto)
