# Ubicación: planificacion/admin.py
from django.contrib import admin
from .models import PlanificacionDespacho, ConfiguracionCapacidad

@admin.register(PlanificacionDespacho)
class PlanificacionAdmin(admin.ModelAdmin):
    # Esto asegura que Django Admin reconozca el modelo y cree la ruta /add/
    list_display = ('fecha_despacho', 'cliente', 'unidad_placa', 'conductor', 'orden_compra', 'carga_origen', 'pallets')
    list_filter = ('fecha_despacho', 'cliente', 'carga_origen')
    search_fields = ('unidad_placa', 'conductor', 'orden_compra')
    date_hierarchy = 'fecha_despacho'

@admin.register(ConfiguracionCapacidad)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('limite_max', 'updated_at')
