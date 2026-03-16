from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from .models import PlanificacionDespacho, ConfiguracionCapacidad
from .forms import PlanificacionForm
from datetime import date, timedelta
import collections
from django.utils import timezone

# --- FUNCIONES DE AYUDA PARA GRUPOS ---

def es_planeamiento(user):
    """Verifica si el usuario pertenece al grupo Planeamiento o es Superusuario."""
    return user.groups.filter(name='Planeamiento').exists() or user.is_superuser

def obtener_nombre_dia_es(f):
    dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    return dias[f.weekday()]

def obtener_nombre_mes_es(f):
    meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 
             'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
    return meses[f.month - 1]


def calcular_kpis_dashboard(lunes_actual, conteo_por_fecha, capacidad_limite):
    """
    Analiza la tendencia de la semana en curso de Lunes a Sábado.
    Optimizado para el gráfico de tendencia visual.
    """
    kpis = {
        'tendencia': [],
        'eficiencia': 0,
        'dia_pico': "N/A",
        'alertas': 0,
        'total_unidades': 0
    }
    
    if capacidad_limite <= 0:
        return kpis

    max_unidades = -1
    total_unidades_semana = 0
    dias_criticos = 0
    
    # Nombres cortos para el gráfico
    nombres_cortos = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB"]

    for i in range(6): # Lunes a Sábado
        fecha = lunes_actual + timedelta(days=i)
        # conteo_por_fecha debe ser un dict que devuelva un set o lista de placas
        unidades = len(conteo_por_fecha.get(fecha, set()))
        
        porcentaje = (unidades / capacidad_limite * 100)
        
        # Guardamos la info necesaria para el componente visual
        kpis['tendencia'].append({
            'dia': nombres_cortos[i],
            'valor': min(porcentaje, 100), # Para la altura de la barra
            'real': porcentaje,            # Valor real para cálculos
            'unidades': unidades           # Número de camiones
        })

        total_unidades_semana += unidades
        if unidades > max_unidades:
            max_unidades = unidades
            kpis['dia_pico'] = obtener_nombre_dia_es(fecha)
        
        if unidades >= capacidad_limite:
            dias_criticos += 1

    kpis['total_unidades'] = total_unidades_semana
    # Eficiencia sobre la capacidad total de la semana (6 días)
    kpis['eficiencia'] = round((total_unidades_semana / (6 * capacidad_limite)) * 100, 1)
    kpis['alertas'] = dias_criticos
    
    return kpis


# --- VISTAS DEL SISTEMA ---

@login_required
def dashboard(request):
    """
    Vista principal: Calcula utilización de flota (Lunes a Sábado).
    Muestra 2 semanas de planificación omitiendo domingos.
    """
    # 1. Obtener capacidad nominal
    config = ConfiguracionCapacidad.objects.first()
    capacidad_limite = config.limite_max if config else 7

    # 2. Rango de fechas: 14 días naturales para extraer días laborables

    hoy = date.today()    
    #fecha_inicio = date.today()
    fecha_inicio = hoy - timedelta(days=hoy.weekday())
    fecha_fin = fecha_inicio + timedelta(days=13)
    
    # Referencia para KPIs: El lunes de la semana actual
    lunes_esta_semana = fecha_inicio - timedelta(days=fecha_inicio.weekday())
    
    # 3. Consultar despachos en el rango
    despachos = PlanificacionDespacho.objects.filter(
        fecha_despacho__range=[fecha_inicio, fecha_fin]
    ).order_by('fecha_despacho', 'cliente')

    # 4. Organizar datos por fecha
    datos_por_dia = collections.defaultdict(list)
    conteo_por_fecha = collections.defaultdict(set) # Auxiliar para KPIs (Placas únicas)
    
    for d in despachos:
        datos_por_dia[d.fecha_despacho].append(d)
        conteo_por_fecha[d.fecha_despacho].add(d.unidad_placa)

    agenda = collections.defaultdict(list)
    
    # 5. Construir el calendario (Filtrando Domingos)
    for i in range(14):
        dia_actual = fecha_inicio + timedelta(days=i)
        
        # OMITIR DOMINGOS
        if dia_actual.weekday() == 6:
            continue

        # REGISTROS COMPLETOS del día
        registros_dia = datos_por_dia.get(dia_actual, [])
        
        # Capacidad basada en Placas Únicas
        placas_unicas = set(r.unidad_placa for r in registros_dia)
        conteo_placas = len(placas_unicas)
        
        # --- LÓGICA DE AGRUPACIÓN PARA EL DASHBOARD ---
        dict_empresas = {}
        for r in registros_dia:
            if r.cliente not in dict_empresas:
                dict_empresas[r.cliente] = {
                    'nombre': r.cliente,
                    'id_referencia': r.id
                }
        
        empresas_unicas = sorted(dict_empresas.values(), key=lambda x: x['nombre'])
        
        porcentaje = (conteo_placas / capacidad_limite) * 100 if capacidad_limite > 0 else 0
        
        # Estilos de Semáforo
        if conteo_placas > capacidad_limite:
            estado, color_class = "SOBRESATURADO", "rose"
            estilo_card = "border-rose-500 shadow-rose-100"
            estilo_header = "bg-rose-600 text-white"
        elif porcentaje >= 80:
            estado, color_class = "CRÍTICO", "amber"
            estilo_card = "border-amber-400"
            estilo_header = "bg-amber-500 text-white"
        else:
            estado, color_class = "NORMAL", "emerald"
            estilo_card = "border-slate-100"
            estilo_header = "bg-slate-100 text-slate-700"

        dia_info = {
            'fecha': dia_actual,
            'nombre_dia': obtener_nombre_dia_es(dia_actual),
            'mes': obtener_nombre_mes_es(dia_actual),
            'empresas': empresas_unicas,
            'conteo_placas': conteo_placas,
            'porcentaje': porcentaje,
            'estado': estado,
            'color_class': color_class,
            'estilo_card': estilo_card,
            'estilo_header': estilo_header,
        }
        
        num_semana = dia_actual.isocalendar()[1]
        agenda[num_semana].append(dia_info)
    
    # --- INTEGRACIÓN DE KPIs ---
    kpis_data = calcular_kpis_dashboard(lunes_esta_semana, conteo_por_fecha, capacidad_limite)

    return render(request, 'planificacion/dashboard.html', {
        'agenda': dict(agenda),
        'capacidad_limite': capacidad_limite,
        'kpis': kpis_data,
    })

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def actualizar_capacidad(request):
    """Ajusta el límite máximo de la flota."""
    if request.method == 'POST':
        nuevo_limite = request.POST.get('nuevo_limite')
        if nuevo_limite:
            config, created = ConfiguracionCapacidad.objects.get_or_create(id=1)
            config.limite_max = int(nuevo_limite)
            config.save()
            messages.success(request, f"Capacidad de flota actualizada a {nuevo_limite} unidades.")
    return redirect('dashboard')

@login_required
def detalle_dia(request, fecha):
    """Muestra todos los despachos de una fecha específica."""
    fecha_dt = date.fromisoformat(fecha)
    despachos = PlanificacionDespacho.objects.filter(fecha_despacho=fecha_dt).order_by('cliente')
    
    config = ConfiguracionCapacidad.objects.first()
    capacidad_limite = config.limite_max if config else 7
    
    placas_unicas = set(d.unidad_placa for d in despachos)
    conteo_placas = len(placas_unicas)
    
    es_editable = fecha_dt >= date.today()
    
    return render(request, 'planificacion/detalle_dia.html', {
        'despachos': despachos,
        'fecha': fecha_dt,
        'conteo_placas': conteo_placas,
        'capacidad_limite': capacidad_limite,
        'nombre_dia': obtener_nombre_dia_es(fecha_dt),
        'es_editable': es_editable,
        'usuario_puede_editar': es_planeamiento(request.user), 
    })

@login_required
def lista_busqueda(request):
    """Vista de gestión y búsqueda con filtros."""
    queryset = PlanificacionDespacho.objects.all().order_by('-fecha_despacho')
    
    q_cliente = request.GET.get('cliente')
    q_placa = request.GET.get('placa')
    q_desde = request.GET.get('fecha_desde')
    q_hasta = request.GET.get('fecha_hasta')
    
    if q_cliente:
        queryset = queryset.filter(cliente__icontains=q_cliente)
    if q_placa:
        queryset = queryset.filter(unidad_placa__icontains=q_placa)
    if q_desde:
        queryset = queryset.filter(fecha_despacho__gte=q_desde)
    if q_hasta:
        queryset = queryset.filter(fecha_despacho__lte=q_hasta)
    
    for reg in queryset:
        reg.dia_semana_es = obtener_nombre_dia_es(reg.fecha_despacho)
        
    return render(request, 'planificacion/lista_busqueda.html', {
        'registros': queryset,
        'usuario_puede_editar': es_planeamiento(request.user),
    })

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def registrar_planificacion(request):
    """Registro de nuevas planificaciones."""
    fecha_predefinida = request.GET.get('fecha')
    
    if request.method == 'POST':
        form = PlanificacionForm(request.POST)
        if form.is_valid():
            nueva_fecha = form.cleaned_data.get('fecha_despacho')
            if nueva_fecha < date.today():
                messages.error(request, "⚠️ Error de Seguridad: No se pueden crear registros con fechas pasadas.")
                return render(request, 'planificacion/form_planificacion.html', {'form': form})

            planificacion = form.save(commit=False)
            planificacion.creado_por = request.user
            planificacion.save()

            config = ConfiguracionCapacidad.objects.first()
            limite = config.limite_max if config else 7
            total_placas = PlanificacionDespacho.objects.filter(fecha_despacho=nueva_fecha).values('unidad_placa').distinct().count()
            
            if total_placas > limite:
                messages.warning(request, f"⚠️ Unidad {planificacion.unidad_placa} registrada, pero se ha superado el límite de flota ({total_placas}/{limite}).")
            else:
                messages.success(request, f"✅ Unidad {planificacion.unidad_placa} programada correctamente.")

            return redirect('dashboard')
        else:
            messages.error(request, "Error en el formulario. Verifique los datos.")
    else:
        initial_data = {}
        if fecha_predefinida:
            initial_data['fecha_despacho'] = fecha_predefinida
        form = PlanificacionForm(initial=initial_data)

    return render(request, 'planificacion/form_planificacion.html', {'form': form, 'edit_mode': False})

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def editar_planificacion(request, pk):
    """Edición de planificaciones existentes."""
    registro = get_object_or_404(PlanificacionDespacho, pk=pk)
    
    if registro.fecha_despacho < date.today():
        messages.error(request, "⚠️ No se pueden editar registros de fechas pasadas.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = PlanificacionForm(request.POST, instance=registro)
        if form.is_valid():
            form.save()
            config = ConfiguracionCapacidad.objects.first()
            limite = config.limite_max if config else 7
            total_placas = PlanificacionDespacho.objects.filter(fecha_despacho=registro.fecha_despacho).values('unidad_placa').distinct().count()
            
            if total_placas > limite:
                messages.warning(request, f"⚠️ Planificación actualizada. Nota: La flota actual ({total_placas}) excede el límite nominal ({limite}).")
            else:
                messages.success(request, "✅ Planificación actualizada correctamente.")
            return redirect('dashboard')
    else:
        form = PlanificacionForm(instance=registro)

    return render(request, 'planificacion/form_planificacion.html', {'form': form, 'edit_mode': True})

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def eliminar_planificacion(request, pk):
    """Eliminación de registros con bloqueo histórico."""
    registro = get_object_or_404(PlanificacionDespacho, pk=pk)
    
    if registro.fecha_despacho < date.today():
        messages.error(request, "🚫 No se pueden eliminar registros de fechas pasadas.")
        return redirect('lista_busqueda')
        
    if request.method == 'POST':
        placa = registro.unidad_placa
        fecha = registro.fecha_despacho
        registro.delete()
        messages.success(request, f"🗑️ Se eliminó la programación de la unidad {placa} para el {fecha}.")
        
        referer = request.META.get('HTTP_REFERER')
        if referer and 'busqueda' in referer:
            return redirect('lista_busqueda')
        return redirect('dashboard')
    
    return redirect('dashboard')
