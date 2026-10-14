"""
Portería (F3), pensada para tablet. Solo el rol Vigilancia registra entradas y salidas, y solo
en los CD que tiene autorizados (UsuarioCD). El vigilante nunca elige «entrada» o «salida»:
el sistema lo decide según exista o no un tramo abierto de la placa. Con la jornada de la flota
propia (F6) se suman el inicio de jornada (unidad que viene de afuera), el fin de jornada
(Post-Ruta) y el ingreso por avería.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from .. import consultas, jornadas, servicios, tiempo
from ..forms import EntradaForm, InicioJornadaForm, PostRutaForm, SalidaForm
from ..models import CentroDistribucion, Conductor, ConfiguracionAccesos, TipoIncidencia, Tramo
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
        # Desde la pantalla de ENTRADA de una unidad en ruta, el vigilante puede cerrar la jornada o
        # registrar el ingreso por avería (modo=post_ruta / modo=averia).
        modo = request.GET.get('modo', '')
        if resultado.accion == servicios.ENTRADA and resultado.permite_post_ruta and modo in ('post_ruta', 'averia'):
            resultado.accion = servicios.POST_RUTA
        contexto.update({'texto': texto, 'resultado': resultado, 'modo': modo})
        if resultado.jornada is not None:
            contexto['jornada_limite_cierre'] = jornadas.limite_cierre(resultado.jornada)
        if resultado.transporte is not None:
            # Último kilometraje de la placa: referencia junto a cada campo de km («Está saliendo» lo propone).
            contexto['ultimo_km'] = resultado.transporte.km_actual
        if resultado.accion == servicios.INICIO_JORNADA:
            contexto['ruta_salida'] = _ruta_salida(resultado.ruta_salida, ahora, config)
        if resultado.accion in (servicios.POST_RUTA, servicios.INICIO_JORNADA):
            contexto.update({
                'conductores': Conductor.objects.filter(activo=True).select_related('tipo_documento'),
                'tipos_incidencia': TipoIncidencia.objects.filter(activo=True),
                'fuera_de_horario': resultado.jornada is not None and ahora > jornadas.limite_cierre(resultado.jornada),
            })
        if resultado.accion == servicios.SALIDA:
            tramo = resultado.tramo_abierto
            contexto.update({
                'minutos_dentro': servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora)),
                'prolongada': tramo.es_programado and servicios.es_prolongada(tramo, ahora, config),
                'numero_tramo': servicios.numero_tramo_del_dia(tramo),
                'incidencia': jornadas.incidencia_abierta(resultado.transporte.pk),
            })
            if resultado.tipo_salida == servicios.SALIDA_PRE_RUTA_AUTOMATICA:
                desde = min(ahora, max(jornadas.inicio(resultado.jornada), tramo.entrada_en))
                contexto['minutos_pre_ruta'] = servicios.formato_duracion(int((ahora - desde).total_seconds() // 60))
            # Ruta INTERNO que atiende esta salida (su hora programada es la de salida) y cómo sale frente a ella.
            ruta = resultado.ruta_salida or (tramo.programacion if tramo.programacion_id and tramo.califica_salida else None)
            contexto['ruta_salida'] = _ruta_salida(ruta, ahora, config)
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


def _ruta_salida(programacion, ahora, config):
    """Datos para mostrar la ruta INTERNO que atiende una salida a ruta: hora programada (de salida) y cómo
    saldría si se registra ahora (a tiempo o con retraso, según la tolerancia)."""
    if programacion is None:
        return None
    desviacion = round((ahora - servicios.momento(programacion)).total_seconds() / 60)
    return {'programacion': programacion, 'desviacion': desviacion, 'texto': servicios.texto_desviacion(desviacion),
            'retrasada': servicios.puntualidad(desviacion, config.tolerancia_puntualidad_min) == servicios.RETRASADO}


def _volver(placa=None, **extra):
    parametros = {'placa': placa} if placa else {}
    parametros.update(extra)
    url = reverse('accesos:porteria')
    return redirect(f"{url}?{urlencode(parametros)}" if parametros else url)


def _errores_form(request, form):
    for errores in form.errors.values():
        for error in errores:
            messages.error(request, error)


@require_POST
@rol_requerido(VIGILANCIA)
@_requiere_cd
def registrar_entrada(request, cd):
    form = EntradaForm(request.POST)
    placa = request.POST.get('placa', '')
    if not form.is_valid():
        _errores_form(request, form)
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
def registrar_inicio_jornada(request, cd):
    """Unidad propia con jornada y sin tramo abierto: entra desde afuera o sale (durmió sin registro)."""
    form = InicioJornadaForm(request.POST)
    placa = request.POST.get('placa', '')
    if not form.is_valid():
        _errores_form(request, form)
        return _volver(placa)
    datos = form.cleaned_data
    try:
        if datos['sentido'] == InicioJornadaForm.INGRESA:
            tramo, avisos = servicios.registrar_inicio_jornada(
                transporte_id=datos['transporte'], cd=cd, km=datos['km'], obs=datos['obs'],
                conductor_id=datos['conductor'], usuario=request.user, ahora=tiempo.ahora())
            registrado = 'INICIO'
        else:
            tramo, avisos = servicios.registrar_salida_sin_registro(
                transporte_id=datos['transporte'], cd=cd, km=datos['km'], obs=datos['obs'],
                conductor_id=datos['conductor'], usuario=request.user, ahora=tiempo.ahora())
            registrado = 'PRE_RUTA'
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return _volver(placa)
    for aviso in avisos:
        messages.warning(request, aviso)
    return _volver(registrado=registrado, registrado_placa=tramo.transporte.placa)


@require_POST
@rol_requerido(VIGILANCIA)
@_requiere_cd
def registrar_post_ruta(request, cd):
    """Fin de jornada (Post-Ruta) o ingreso por avería."""
    form = PostRutaForm(request.POST)
    placa = request.POST.get('placa', '')
    modo = 'averia' if request.POST.get('averia') else 'post_ruta'
    if not form.is_valid():
        _errores_form(request, form)
        return _volver(placa, modo=modo)
    datos = form.cleaned_data
    try:
        tramo, avisos = servicios.registrar_post_ruta(
            transporte_id=datos['transporte'], cd=cd, km=datos['km'], obs=datos['obs'],
            conductor_id=datos['conductor'], usuario=request.user, ahora=tiempo.ahora(),
            averia_tipo_id=datos['averia_tipo'] if datos['averia'] else None,
            averia_descripcion=datos['averia_descripcion'] if datos['averia'] else '')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return _volver(placa, modo=modo)
    for aviso in avisos:
        messages.warning(request, aviso)
    return _volver(registrado='AVERIA' if datos['averia'] else 'POST_RUTA', registrado_placa=tramo.transporte.placa)


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
    registrado = 'PRE_RUTA' if tramo.clase == Tramo.Clase.PRE_RUTA else 'SALIDA'
    return _volver(registrado=registrado, registrado_placa=tramo.transporte.placa)


@rol_requerido(VIGILANCIA)
@_requiere_cd
def dentro_ahora(request, cd):
    ahora = tiempo.ahora()
    config = ConfiguracionAccesos.obtener()
    tramos = list(Tramo.objects.filter(cd=cd, abierto=True)
                  .select_related('transporte__tamano', 'programacion', 'conductor').order_by('entrada_en'))
    for tramo in tramos:
        tramo.tiempo_dentro = servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora))
        # La pernocta de la flota propia (Post-Ruta) no es una permanencia prolongada.
        tramo.prolongada = tramo.es_programado and servicios.es_prolongada(tramo, ahora, config)
    contexto = _contexto_cd(request, cd)
    contexto.update({'tramos': tramos, 'umbral': config.umbral_prolongada_min})
    return render(request, 'accesos/porteria/dentro_ahora.html', contexto)


@rol_requerido(VIGILANCIA)
@_requiere_cd
def programaciones_hoy(request, cd):
    """Consulta de solo lectura de las programaciones del CD del vigilante, con filtros de
    fecha, placa, conductor y estado, y los tiempos reales de cada una."""
    hoy = tiempo.hoy()
    ahora = tiempo.ahora()
    programaciones, filtros = consultas.programaciones_filtradas(
        request.GET, hoy=hoy, ahora=ahora, config=ConfiguracionAccesos.obtener(), cd=cd)
    contexto = _contexto_cd(request, cd)
    contexto.update({'programaciones': programaciones, 'filtros': filtros, 'es_hoy': filtros['fecha'] == hoy,
                     'jornadas_dia': jornadas.jornadas_del_dia(filtros['fecha'], ahora, cd_ids={cd.pk}),
                     'no_laborable': jornadas.no_laborables_entre(filtros['fecha'], filtros['fecha']).get(filtros['fecha']),
                     'fecha_larga': tiempo.fecha_larga(filtros['fecha']),
                     'etiquetas_estado': servicios.ETIQUETAS_ESTADO})
    return render(request, 'accesos/porteria/programaciones_hoy.html', contexto)
