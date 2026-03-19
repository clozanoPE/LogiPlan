from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
# Se sincroniza con models incluyendo CapacidadDiaria
from .models import PlanificacionDespacho, ConfiguracionCapacidad, CapacidadDiaria
from .forms import PlanificacionForm
from datetime import date, timedelta
import collections
from django.utils import timezone
from django.db import models # Añadido para Q objects en búsqueda

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


def calcular_kpis_dashboard(lunes_actual, conteo_por_fecha, capacidad_limite_nominal, excepciones_externas=None):
    kpis = {
        'tendencia': [],
        'eficiencia': 0,
        'dia_pico': "N/A",
        'alertas': 0,
        'total_unidades': 0
    }
    
    # Prioridad: Excepciones externas (pasadas desde la vista dashboard)
    excepciones_semana = excepciones_externas if excepciones_externas is not None else {}

    max_unidades = -1
    total_unidades_semana = 0
    dias_criticos = 0
    capacidad_total_semana = 0
    nombres_cortos = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB"]

    for i in range(6): 
        fecha = lunes_actual + timedelta(days=i)
        # Obtenemos placas únicas para ese día
        unidades = len(conteo_por_fecha.get(fecha, set()))
        
        # LÓGICA CLAVE: Si hay excepción se usa, si no, el nominal (global)
        limite_real = excepciones_semana.get(fecha, capacidad_limite_nominal)
        capacidad_total_semana += limite_real
        
        porcentaje = (unidades / limite_real * 100) if limite_real > 0 else 0
        
        kpis['tendencia'].append({
            'dia': nombres_cortos[i],
            'valor': min(porcentaje, 100),
            'real': porcentaje,
            'unidades': unidades
        })

        total_unidades_semana += unidades
        if unidades > max_unidades:
            max_unidades = unidades
            kpis['dia_pico'] = obtener_nombre_dia_es(fecha)
        
        # ALERTA: Si se iguala o supera el límite real de ese día específico
        if unidades >= limite_real and limite_real > 0:
            dias_criticos += 1

    kpis['total_unidades'] = total_unidades_semana
    kpis['eficiencia'] = round((total_unidades_semana / capacidad_total_semana * 100), 1) if capacidad_total_semana > 0 else 0
    kpis['alertas'] = dias_criticos
    
    return kpis

# --- VISTAS DEL SISTEMA ---
@login_required
def dashboard(request):
    config = ConfiguracionCapacidad.objects.first()
    capacidad_limite_global = config.limite_max if config else 7

    hoy = date.today()    
    fecha_inicio = hoy - timedelta(days=hoy.weekday())
    fecha_fin = fecha_inicio + timedelta(days=13)
    lunes_esta_semana = fecha_inicio 

    # 1. Traer excepciones del rango
    excepciones = {
        cap.fecha: cap.limite_personalizado 
        for cap in CapacidadDiaria.objects.filter(fecha__range=[fecha_inicio, fecha_fin])
    }
    
    # 2. Consultar despachos
    despachos = PlanificacionDespacho.objects.filter(
        fecha_despacho__range=[fecha_inicio, fecha_fin]
    ).order_by('fecha_despacho', 'cliente')

    datos_por_dia = collections.defaultdict(list)
    conteo_por_fecha = collections.defaultdict(set) 
    
    for d in despachos:
        datos_por_dia[d.fecha_despacho].append(d)
        conteo_por_fecha[d.fecha_despacho].add(d.unidad_placa)

    agenda = collections.defaultdict(list)
    
    # 3. Construir calendario
    for i in range(14):
        dia_actual = fecha_inicio + timedelta(days=i)
        if dia_actual.weekday() == 6: continue

        registros_dia = datos_por_dia.get(dia_actual, [])
        conteo_placas = len(conteo_por_fecha.get(dia_actual, set()))
        
        # LÓGICA CLAVE: Jerarquía de límites
        limite_dia = excepciones.get(dia_actual, capacidad_limite_global)
        
        # Porcentaje basado en el límite de este día
        porcentaje = (conteo_placas / limite_dia) * 100 if limite_dia > 0 else 0
        
        # SEMÁFORO SINCRONIZADO:
        # Rojo si SUPERA el límite del día. Ámbar si alcanza el 100% o está cerca (>=80%).
        if conteo_placas > limite_dia:
            estado, color_class = "SOBRESATURADO", "rose"
            estilo_card = "border-rose-500 shadow-lg shadow-rose-100/50"
            estilo_header = "bg-rose-600 text-white"
        elif porcentaje >= 100:
            estado, color_class = "CRÍTICO", "amber"
            estilo_card = "border-amber-500 shadow-md shadow-amber-50/50"
            estilo_header = "bg-amber-600 text-white"
        elif porcentaje >= 80:
            estado, color_class = "ADVERTENCIA", "amber"
            estilo_card = "border-amber-300"
            estilo_header = "bg-amber-500 text-white"
        else:
            estado, color_class = "NORMAL", "emerald"
            estilo_card = "border-slate-100"
            estilo_header = "bg-slate-100 text-slate-700"

        # Agrupación de empresas para el detalle del card
        dict_empresas = {}
        for r in registros_dia:
            if r.cliente not in dict_empresas:
                dict_empresas[r.cliente] = {'nombre': r.cliente, 'id_referencia': r.id}
        
        dia_info = {
            'fecha': dia_actual,
            'nombre_dia': obtener_nombre_dia_es(dia_actual),
            'mes': obtener_nombre_mes_es(dia_actual),
            'empresas': sorted(dict_empresas.values(), key=lambda x: x['nombre']),
            'conteo_placas': conteo_placas,
            'limite_dia': limite_dia, 
            'porcentaje': porcentaje,
            'estado': estado,
            'color_class': color_class,
            'estilo_card': estilo_card,
            'estilo_header': estilo_header,
        }
        
        num_semana = dia_actual.isocalendar()[1]
        agenda[num_semana].append(dia_info)
    
    # KPIs con excepciones pasadas por parámetro para exactitud
    kpis_data = calcular_kpis_dashboard(lunes_esta_semana, conteo_por_fecha, capacidad_limite_global, excepciones)

    return render(request, 'planificacion/dashboard.html', {
        'agenda': dict(agenda),
        'capacidad_limite': capacidad_limite_global,
        'kpis': kpis_data,
    })

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def actualizar_capacidad(request):
    """Ajusta el límite máximo de la flota global."""
    if request.method == 'POST':
        nuevo_limite = request.POST.get('nuevo_limite')
        if nuevo_limite:
            config, created = ConfiguracionCapacidad.objects.get_or_create(id=1)
            config.limite_max = int(nuevo_limite)
            config.save()
            messages.success(request, f"Capacidad de flota actualizada a {nuevo_limite} unidades.")
    return redirect('dashboard')

@login_required
@user_passes_test(es_planeamiento, login_url='dashboard')
def gestionar_capacidad_diaria(request):
    """
    Vista rápida para que Planeamiento ajuste el límite de camiones por fecha.
    Recibe 'fecha' (YYYY-MM-DD) y 'limite_personalizado' (int) vía POST.
    """
    if request.method == 'POST':
        fecha_str = request.POST.get('fecha')
        nuevo_limite = request.POST.get('limite_personalizado')
        motivo = request.POST.get('motivo', 'Ajuste manual desde Dashboard')

        if not fecha_str or not nuevo_limite:
            messages.error(request, "⚠️ Datos incompletos para actualizar la capacidad.")
            return redirect('dashboard')

        try:
            # 1. Validación de formato y valor numérico
            limite_int = int(nuevo_limite)
            if limite_int < 0:
                messages.error(request, "⚠️ El límite no puede ser negativo.")
                return redirect('dashboard')

            # 2. Validación de Integridad Histórica (Opcional pero Recomendado)
            # Evita que se cambie la capacidad de un día que ya pasó
            from datetime import datetime
            fecha_dt = datetime.strptime(fecha_str, '%Y-%m-%d').date()
            if fecha_dt < timezone.now().date():
                messages.error(request, "🚫 No se puede modificar la capacidad de fechas pasadas.")
                return redirect('dashboard')

            # 3. Lógica central: Actualiza si existe, crea si no.
            obj, created = CapacidadDiaria.objects.update_or_create(
                fecha=fecha_dt,
                defaults={
                    'limite_personalizado': limite_int,
                    'motivo': motivo
                }
            )

            accion = "establecida" if created else "actualizada"
            messages.success(request, f"✅ Capacidad para el {fecha_str} {accion} a {limite_int} unidades.")

        except ValueError:
            messages.error(request, "⚠️ El límite debe ser un número entero válido.")
        except Exception as e:
            messages.error(request, f"❌ Error al procesar el ajuste: {str(e)}")

    return redirect('dashboard')


@login_required
def detalle_dia(request, fecha):
    """Muestra todos los despachos de una fecha específica con su límite real."""
    fecha_dt = date.fromisoformat(fecha)
    despachos = PlanificacionDespacho.objects.filter(fecha_despacho=fecha_dt).order_by('cliente')
    
    # Obtener límite real para este día
    excepcion = CapacidadDiaria.objects.filter(fecha=fecha_dt).first()
    config = ConfiguracionCapacidad.objects.first()
    limite_dia = excepcion.limite_personalizado if excepcion else (config.limite_max if config else 7)
    
    placas_unicas = set(d.unidad_placa for d in despachos)
    conteo_placas = len(placas_unicas)
    
    es_editable = fecha_dt >= date.today()
    
    return render(request, 'planificacion/detalle_dia.html', {
        'despachos': despachos,
        'fecha': fecha_dt,
        'conteo_placas': conteo_placas,
        'capacidad_limite': limite_dia, # Pasamos el límite real de ese día
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
    """Registro de nuevas planificaciones con validación de capacidad dinámica."""
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

            # Lógica de alerta basada en Capacidad Diaria
            excepcion = CapacidadDiaria.objects.filter(fecha=nueva_fecha).first()
            config = ConfiguracionCapacidad.objects.first()
            limite_real = excepcion.limite_personalizado if excepcion else (config.limite_max if config else 7)
            
            total_placas = PlanificacionDespacho.objects.filter(fecha_despacho=nueva_fecha).values('unidad_placa').distinct().count()
            
            if total_placas > limite_real:
                messages.warning(request, f"⚠️ Unidad {planificacion.unidad_placa} registrada, pero se ha superado el límite para este día ({total_placas}/{limite_real}).")
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
    """Edición de planificaciones existentes con validación de capacidad dinámica."""
    registro = get_object_or_404(PlanificacionDespacho, pk=pk)
    
    if registro.fecha_despacho < date.today():
        messages.error(request, "⚠️ No se pueden editar registros de fechas pasadas.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = PlanificacionForm(request.POST, instance=registro)
        if form.is_valid():
            form.save()
            
            # Obtener límite real del día editado
            excepcion = CapacidadDiaria.objects.filter(fecha=registro.fecha_despacho).first()
            config = ConfiguracionCapacidad.objects.first()
            limite_real = excepcion.limite_personalizado if excepcion else (config.limite_max if config else 7)
            
            total_placas = PlanificacionDespacho.objects.filter(fecha_despacho=registro.fecha_despacho).values('unidad_placa').distinct().count()
            
            if total_placas > limite_real:
                messages.warning(request, f"⚠️ Planificación actualizada. Nota: La flota actual ({total_placas}) excede el límite de este día ({limite_real}).")
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
