"""
Control y correcciones (F4). El tablero «Hoy» y la consulta de tramos son de lectura para
Administrador, Despacho, Consulta y Vigilancia (esta última solo ve los tramos de sus CD
autorizados). Corregir, anular y cerrar tramos es solo del Administrador, siempre con motivo
y auditoría (R24–R27).
"""
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import excel, exportaciones, indicadores, jornadas, servicios, tiempo
from ..forms import CierreAdministrativoForm, ConvertirPostRutaForm, CorreccionTramoForm, MotivoForm
from ..models import CentroDistribucion, ConfiguracionAccesos, Programacion, RegistroAuditoria, Tramo, normalizar_placa
from ..paginacion import paginar
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, rol_requerido, tiene_rol
from .porteria import cds_autorizados

MAX_RANGO_DIAS = 180
ESTADOS_FILTRO = {
    '': 'Todos los válidos',
    'abiertos': 'Dentro ahora',
    'prolongados': 'Permanencia prolongada',
    'demoras': 'Demoras (sobre la meta de permanencia)',
    'marcados': 'Con marcas (fuera de ventana, km, corregidos, cierres)',
    'anulados': 'Anulados',
}


def cds_de_tramos(user):
    """CD cuyos tramos puede ver el usuario: None = todos; un conjunto de ids si solo es Vigilancia
    (lectura limitada a sus CD autorizados)."""
    if tiene_rol(user, ADMINISTRADOR, DESPACHO, CONSULTA):
        return None
    return set(cds_autorizados(user).values_list('pk', flat=True))


def _cds_visibles(user):
    cds = CentroDistribucion.objects.filter(activo=True)
    permitidos = cds_de_tramos(user)
    return cds if permitidos is None else cds.filter(pk__in=permitidos)


def _fecha(valor, por_defecto):
    try:
        return date.fromisoformat(valor) if valor else por_defecto
    except ValueError:
        return por_defecto


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def tablero_hoy(request):
    """Vista operativa en vivo: ocupación de cada CD, avance de las programaciones de hoy y vehículos dentro."""
    ahora = tiempo.ahora()
    hoy = ahora.date()
    config = ConfiguracionAccesos.obtener()
    abiertos = list(Tramo.objects.filter(abierto=True)
                    .select_related('cd', 'transporte__tamano', 'programacion', 'conductor').order_by('entrada_en'))
    for tramo in abiertos:
        tramo.minutos = servicios.minutos_dentro(tramo, ahora)
        tramo.tiempo_dentro = servicios.formato_duracion(tramo.minutos)
        # La pernocta de la flota propia no es una permanencia prolongada ni una demora.
        tramo.prolongada = tramo.es_programado and tramo.minutos > config.umbral_prolongada_min
        tramo.sobre_meta = bool(tramo.es_programado and config.meta_permanencia_min
                                and tramo.minutos > config.meta_permanencia_min)
    # Turnos de hoy (día y noche) más el turno noche de ayer si sigue en curso o volvió de madrugada.
    jornadas_hoy = jornadas.jornadas_en_vivo(ahora)

    programaciones = list(Programacion.objects.filter(fecha=hoy))
    centros = []
    for cd in CentroDistribucion.objects.filter(activo=True):
        estados = [servicios.estado_visible(p, hoy) for p in programaciones if p.cd_id == cd.pk]
        dentro = sum(1 for t in abiertos if t.cd_id == cd.pk)
        centros.append({
            'cd': cd, 'dentro': dentro, 'saturado': dentro > cd.capacidad_simultanea,
            'flota_dentro': sum(1 for t in abiertos if t.cd_id == cd.pk and t.es_de_jornada),
            'en_atencion': sum(1 for t in abiertos if t.cd_id == cd.pk and t.es_programado),
            'programadas': sum(1 for e in estados if e != servicios.CANCELADA),
            'atendidas': estados.count(servicios.CERRADA) + estados.count(servicios.EN_PLANTA),
            'pendientes': estados.count(servicios.PENDIENTE),
        })
    return render(request, 'accesos/control/tablero_hoy.html', {
        'centros': centros, 'abiertos': abiertos, 'config': config, 'cds_tramos': cds_de_tramos(request.user),
        'fecha_larga': tiempo.fecha_larga(hoy), 'hora': ahora,
        'jornadas_dia': jornadas_hoy, 'resumen_flota': resumen_flota(jornadas_hoy, len(jornadas.ids_no_disponibles())),
        'no_laborable': jornadas.no_laborables_entre(hoy, hoy).get(hoy),
    })


# Grupos de estados visibles de la jornada para el resumen de la flota propia («Hoy», TV).
GRUPOS_FLOTA = [
    ('por_iniciar', 'Por iniciar', ('POR_INICIAR', 'POR_LLEGAR')),
    ('pre_ruta', 'En Pre-Ruta', ('EN_PRE_RUTA', 'PRE_RUTA_EXCEDIDA')),
    ('en_ruta', 'En ruta', ('EN_RUTA', 'EN_VISITA', 'SIN_RETORNO')),
    ('cerradas', 'Retornaron', ('ANTICIPADO', 'EN_HORARIO', 'FUERA_DE_HORARIO', 'CERRADA')),
    ('no_disponibles', 'No disponibles', ('NO_DISPONIBLE', 'POR_AVERIA')),
]


def resumen_flota(jornadas_dia, no_disponibles=None):
    """Conteos de la flota propia del día por grupo de estado, más las alertas. «No disponibles» cuenta todos
    los vehículos con avería abierta (tengan o no jornada hoy) si se indica `no_disponibles`."""
    codigos = [j.estado_v.codigo for j in jornadas_dia]
    grupos = [{'clave': clave, 'etiqueta': etiqueta, 'total': sum(1 for c in codigos if c in incluidos)}
              for clave, etiqueta, incluidos in GRUPOS_FLOTA]
    if no_disponibles is not None:
        grupos[-1]['total'] = no_disponibles
    return {
        'total': len(jornadas_dia), 'grupos': grupos,
        # Placas distintas y jornadas por turno: una placa con turno día y noche cuenta una vez en «placas».
        'placas': len({j.transporte_id for j in jornadas_dia}),
        'dia': sum(1 for j in jornadas_dia if not j.es_noche),
        'noche': sum(1 for j in jornadas_dia if j.es_noche),
        'alertas': sum(1 for j in jornadas_dia if j.estado_v.alerta),
        'fuera_de_horario': codigos.count('FUERA_DE_HORARIO'),
        'sin_retorno': codigos.count('SIN_RETORNO'),
        'pre_ruta_excedida': codigos.count('PRE_RUTA_EXCEDIDA'),
    }


def _tramos_filtrados(request):
    hoy = tiempo.hoy()
    desde = _fecha(request.GET.get('desde'), hoy)
    hasta = _fecha(request.GET.get('hasta'), hoy)
    if hasta < desde:
        desde, hasta = hasta, desde
    recortado = (hasta - desde).days > MAX_RANGO_DIAS
    if recortado:
        desde = hasta - timedelta(days=MAX_RANGO_DIAS)
    filtros = {'desde': desde, 'hasta': hasta, 'cd': request.GET.get('cd', ''),
               'q': request.GET.get('q', '').strip(), 'estado': request.GET.get('estado', '')}

    tramos = (Tramo.objects.filter(fecha_local_entrada__range=(desde, hasta))
              .select_related('cd', 'transporte', 'tamano_snapshot', 'programacion', 'conductor',
                              'vigilante_entrada', 'vigilante_salida', 'jornada'))
    permitidos = cds_de_tramos(request.user)
    if permitidos is not None:
        tramos = tramos.filter(cd_id__in=permitidos)
    if filtros['cd'].isdigit():
        tramos = tramos.filter(cd_id=int(filtros['cd']))
    if filtros['q']:
        tramos = tramos.filter(transporte__placa_normalizada__icontains=normalizar_placa(filtros['q']))
    estado = filtros['estado']
    if estado == 'anulados':
        tramos = tramos.filter(vigente__isnull=True)
    else:
        tramos = tramos.filter(vigente=True)
        if estado == 'abiertos':
            tramos = tramos.filter(abierto=True)
        elif estado == 'marcados':
            tramos = tramos.filter(Q(fuera_de_ventana=True) | Q(km_inconsistente=True) | Q(corregido=True)
                                   | Q(cierre_administrativo=True))
    tramos = list(tramos.order_by('-entrada_en', '-id'))
    ahora = tiempo.ahora()
    config = ConfiguracionAccesos.obtener()
    for tramo in tramos:
        tramo.minutos = servicios.minutos_dentro(tramo, ahora)
        tramo.prolongada = tramo.minutos > config.umbral_prolongada_min
        # Llegada en las visitas de clientes y proveedores; salida en las rutas INTERNO (incluida la primera,
        # que atiende la salida de la Pre-Ruta).
        tramo.puntualidad = servicios.puntualidad(tramo.desviacion_min, config.tolerancia_puntualidad_min)
    if estado == 'prolongados':
        tramos = [t for t in tramos if t.prolongada]
    elif estado == 'demoras':
        tramos = [t for t in tramos if indicadores.es_demora(t.minutos_permanencia, config)]
    return tramos, filtros, recortado


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def tramos_lista(request):
    tramos, filtros, recortado = _tramos_filtrados(request)
    contexto = paginar(request, tramos)
    for tramo in contexto['page_obj']:
        tramo.tiempo_dentro = servicios.formato_duracion(tramo.minutos)
    return render(request, 'accesos/control/tramos_lista.html', {
        **contexto, 'filtros': filtros, 'recortado': recortado,
        'max_rango': MAX_RANGO_DIAS, 'cds': _cds_visibles(request.user),
        'estados_filtro': ESTADOS_FILTRO, 'total': len(tramos),
    })


ETIQUETAS_PUNTUALIDAD = {servicios.ADELANTADO: 'Adelantado', servicios.PUNTUAL: 'Puntual',
                         servicios.RETRASADO: 'Retrasado'}


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
def tramos_exportar(request):
    tramos, filtros, _ = _tramos_filtrados(request)
    encabezados = ['Fecha entrada', 'Hora entrada', 'Fecha salida', 'Hora salida', 'Permanencia (min)', 'CD',
                   'Placa', 'Tamaño', 'Tipo', 'Razón social', 'RUC / DNI', 'Motivo', 'Conductor', 'Programación',
                   'Hora programada', 'Desviación llegada (min)', 'Desviación salida (min)', 'Puntualidad',
                   'Se califica', 'Kilometraje', 'Obs. entrada',
                   'Obs. salida', 'Vigilante entrada', 'Vigilante salida', 'Estado', 'Marcas', 'Clase', 'Jornada']
    filas = []
    for t in tramos:
        salida = tiempo.a_lima(t.salida_en).replace(tzinfo=None) if t.salida_en else None
        marcas = [nombre for nombre, activa in (('Fuera de ventana', t.fuera_de_ventana),
                                                ('Km inconsistente', t.km_inconsistente),
                                                ('Corregido', t.corregido),
                                                ('Cierre administrativo', t.cierre_administrativo),
                                                ('Cierre automático (Pre-Ruta)', t.cierre_automatico),
                                                ('Salida tras la Post-Ruta', t.salida_tras_cierre),
                                                ('Día no laborable', t.dia_no_laborable)) if activa]
        p = t.programacion
        filas.append([
            t.fecha_local_entrada, t.hora_local_entrada, salida.date() if salida else None,
            salida.time().replace(microsecond=0) if salida else None, t.minutos_permanencia, str(t.cd),
            t.transporte.placa, str(t.tamano_snapshot), t.get_tipo_snapshot_display(),
            p.razon_social if p else '', p.documento if p else '', p.motivo if p else '', t.conductor.nombre_completo,
            p.codigo if p else '', p.hora if p else None, t.desviacion_llegada_min, t.desviacion_salida_min,
            ETIQUETAS_PUNTUALIDAD.get(t.puntualidad, ''),
            ('Salida' if t.califica_salida else 'Llegada') if p else '', t.km_entrada,
            t.obs_entrada, t.obs_salida, str(t.vigilante_entrada or 'Sistema'), str(t.vigilante_salida or ''),
            'Válido' if t.vigente else 'Anulado', ', '.join(marcas), t.get_clase_display(),
            t.jornada.codigo if t.jornada_id else '',
        ])
    formatos = {0: 'DD/MM/YYYY', 1: 'HH:MM', 2: 'DD/MM/YYYY', 3: 'HH:MM', 14: 'HH:MM', 19: '#,##0'}
    nombre = f"Tramos_{filtros['desde']:%Y%m%d}_{filtros['hasta']:%Y%m%d}.xlsx"
    return excel.respuesta_excel(nombre, 'Tramos', encabezados, filas, formatos)


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA)
@tiempo.con_hora_lima
def tramo_detalle(request, pk):
    tramos = Tramo.objects.select_related('cd', 'transporte', 'tamano_snapshot', 'programacion__cd', 'conductor',
                                          'vigilante_entrada', 'vigilante_salida')
    permitidos = cds_de_tramos(request.user)
    if permitidos is not None:
        # Vigilancia solo abre tramos de sus CD (404 para el resto, como si no existieran).
        tramos = tramos.filter(cd_id__in=permitidos)
    tramo = get_object_or_404(tramos, pk=pk)
    ahora = tiempo.ahora()
    config = ConfiguracionAccesos.obtener()
    contexto = {
        'tramo': tramo,
        'tiempo_dentro': servicios.formato_duracion(servicios.minutos_dentro(tramo, ahora)),
        'prolongada': servicios.es_prolongada(tramo, ahora, config),
        'puntualidad': ETIQUETAS_PUNTUALIDAD.get(servicios.puntualidad(tramo.desviacion_min,
                                                                       config.tolerancia_puntualidad_min)),
        'texto_desviacion': servicios.texto_desviacion(tramo.desviacion_min),
        'numero_tramo': servicios.numero_tramo_del_dia(tramo) if tramo.vigente else None,
        'historial': RegistroAuditoria.objects.filter(entidad='Tramo', objeto_id=pk).select_related('usuario'),
    }
    if tiene_rol(request.user, ADMINISTRADOR) and tramo.vigente:
        contexto['form_anular'] = MotivoForm(prefix='anular')
        if tramo.esta_abierto:
            contexto['form_cierre'] = CierreAdministrativoForm(prefix='cierre', initial={'salida_en': ahora})
            jornada = servicios.jornada_de_visita(tramo) if tramo.es_programado else None
            if jornada and jornada.estado == jornada.Estado.EN_RUTA:
                contexto['form_convertir'] = ConvertirPostRutaForm(prefix='convertir',
                                                                   initial={'fin_atencion': tramo.entrada_en})
    return render(request, 'accesos/control/tramo_detalle.html', contexto)


@rol_requerido(ADMINISTRADOR)
@tiempo.con_hora_lima
def tramo_corregir(request, pk):
    tramo = get_object_or_404(Tramo.objects.select_related('programacion', 'transporte'), pk=pk)
    if not tramo.vigente:
        messages.error(request, "El tramo está anulado; no se puede corregir.")
        return redirect('accesos:tramo_detalle', pk=pk)
    inicial = {'entrada_en': tramo.entrada_en, 'salida_en': tramo.salida_en, 'km_entrada': tramo.km_entrada,
               'obs_entrada': tramo.obs_entrada, 'obs_salida': tramo.obs_salida,
               'programacion': tramo.programacion_id, 'conductor': tramo.conductor_id}
    form = CorreccionTramoForm(request.POST or None, tramo=tramo, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        try:
            servicios.corregir_tramo(
                tramo, request.user, motivo=datos['motivo'], ahora=tiempo.ahora(), entrada_en=datos['entrada_en'],
                salida_en=datos.get('salida_en'), km_entrada=datos['km_entrada'], obs_entrada=datos['obs_entrada'],
                obs_salida=datos.get('obs_salida', ''), programacion=datos.get('programacion'),
                conductor=datos['conductor'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Tramo corregido. El cambio quedó en la auditoría.")
            return redirect('accesos:tramo_detalle', pk=pk)
    return render(request, 'accesos/control/tramo_corregir.html', {'tramo': tramo, 'form': form})


@require_POST
@rol_requerido(ADMINISTRADOR)
def tramo_anular(request, pk):
    tramo = get_object_or_404(Tramo, pk=pk)
    form = MotivoForm(request.POST, prefix='anular')
    try:
        if not form.is_valid():
            raise ValidationError("Indique el motivo de la anulación.")
        servicios.anular_tramo(tramo, request.user, motivo=form.cleaned_data['motivo'])
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, "Tramo anulado. Su programación vuelve a quedar pendiente.")
    return redirect('accesos:tramo_detalle', pk=pk)


@require_POST
@rol_requerido(ADMINISTRADOR)
@tiempo.con_hora_lima
def tramo_cierre(request, pk):
    tramo = get_object_or_404(Tramo, pk=pk)
    form = CierreAdministrativoForm(request.POST, prefix='cierre')
    try:
        if not form.is_valid():
            raise ValidationError("Indique la hora de salida y el motivo.")
        servicios.cierre_administrativo(tramo, request.user, salida_en=form.cleaned_data['salida_en'],
                                        motivo=form.cleaned_data['motivo'], ahora=tiempo.ahora())
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, "Salida registrada por cierre administrativo.")
    return redirect('accesos:tramo_detalle', pk=pk)


@require_POST
@rol_requerido(ADMINISTRADOR)
@tiempo.con_hora_lima
def tramo_convertir_post_ruta(request, pk):
    tramo = get_object_or_404(Tramo, pk=pk)
    form = ConvertirPostRutaForm(request.POST, prefix='convertir')
    try:
        if not form.is_valid():
            raise ValidationError("Indique el fin de la atención y el motivo.")
        servicios.convertir_en_post_ruta(tramo, request.user, fin_atencion=form.cleaned_data['fin_atencion'],
                                         motivo=form.cleaned_data['motivo'], ahora=tiempo.ahora())
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, "Visita cerrada y Post-Ruta registrada: la jornada de ese día quedó cerrada.")
    return redirect('accesos:tramo_detalle', pk=pk)


def _auditoria_filtrada(request):
    registros = RegistroAuditoria.objects.select_related('usuario', 'cd')
    filtros = {'accion': request.GET.get('accion', ''), 'entidad': request.GET.get('entidad', ''),
               'q': request.GET.get('q', '').strip()}
    if filtros['accion']:
        registros = registros.filter(accion=filtros['accion'])
    if filtros['entidad']:
        registros = registros.filter(entidad=filtros['entidad'])
    if filtros['q']:
        registros = registros.filter(objeto_repr__icontains=filtros['q'])
    return registros, filtros


MAX_FILAS_AUDITORIA = 10_000


@rol_requerido(ADMINISTRADOR)
def auditoria_exportar(request):
    """Los registros del filtro, del más reciente al más antiguo (máximo 10.000 por archivo)."""
    registros, _ = _auditoria_filtrada(request)
    total = registros.count()
    encabezados, filas, formatos = exportaciones.auditoria(registros[:MAX_FILAS_AUDITORIA])
    notas = None
    if total > MAX_FILAS_AUDITORIA:
        notas = [f"El filtro tiene {total:,} registros; se exportaron los {MAX_FILAS_AUDITORIA:,} más recientes. "
                 "Use los filtros para acotar la búsqueda.".replace(',', '.')]
    return excel.respuesta_excel(f"Auditoria_{tiempo.hoy():%Y%m%d}.xlsx", 'Auditoría', encabezados, filas,
                                 formatos, notas)


@rol_requerido(ADMINISTRADOR)
def auditoria_lista(request):
    registros, filtros = _auditoria_filtrada(request)
    return render(request, 'accesos/control/auditoria_lista.html', {
        **paginar(request, registros), 'filtros': filtros,
        'acciones': RegistroAuditoria.Accion.choices,
        'entidades': RegistroAuditoria.objects.values_list('entidad', flat=True).distinct().order_by('entidad'),
    })
