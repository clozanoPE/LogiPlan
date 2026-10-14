"""
Django Admin del módulo de Accesos. Todo cambio hecho aquí queda en RegistroAuditoria con
los valores de antes y después. Nada se borra (se desactiva), salvo las asignaciones de CD
a usuarios, cuyo retiro también se audita. La auditoría es de solo lectura para todos.
"""
from django.contrib import admin
from django.db.models import Q

from . import auditoria, licencias, tiempo
from .permisos import en_piloto
from .models import (CentroDistribucion, ConfiguracionAccesos, Conductor, Feriado, Incidencia, Jornada, Programacion,
                     RegistroAuditoria, TamanoVehiculo, TipoDocumentoIdentidad, TipoIncidencia, Tramo, Transporte,
                     UsuarioCD)

Accion = RegistroAuditoria.Accion


# Cada listado declara list_select_related: sin él, Django hace select_related() sin límites al mostrar
# llaves foráneas y une hasta 19 tablas; MySQL tardaba ~100 s solo en planificar esa consulta (Tramos).


class PilotoAdminMixin:
    """Durante el piloto (ACCESOS_USUARIOS_PILOTO), el módulo no aparece en Django Admin a otros usuarios,
    aunque sean superusuarios."""

    def has_module_permission(self, request):
        return en_piloto(request.user) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return en_piloto(request.user) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return en_piloto(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return en_piloto(request.user) and super().has_change_permission(request, obj)


class AuditoriaAdminMixin(PilotoAdminMixin):
    accion_modificacion = Accion.MODIFICAR

    def save_model(self, request, obj, form, change):
        foto_antes = auditoria.foto(type(obj).objects.get(pk=obj.pk)) if change else None
        if not change and hasattr(obj, 'creado_por_id') and not obj.creado_por_id:
            obj.creado_por = request.user
        if change and hasattr(obj, 'modificado_por_id'):
            obj.modificado_por = request.user
        super().save_model(request, obj, form, change)
        if change:
            auditoria.registrar_modificacion(request.user, obj, foto_antes, accion=self.accion_modificacion)
        else:
            auditoria.registrar(request.user, Accion.CREAR, obj, despues=auditoria.foto(obj))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CentroDistribucion)
class CentroDistribucionAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'capacidad_simultanea', 'activo')
    accion_modificacion = Accion.CONFIG


@admin.register(TamanoVehiculo)
class TamanoVehiculoAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('nombre', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    accion_modificacion = Accion.CONFIG


@admin.register(TipoDocumentoIdentidad)
class TipoDocumentoIdentidadAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'longitud_min', 'longitud_max', 'solo_digitos', 'activo')
    accion_modificacion = Accion.CONFIG


@admin.register(Feriado)
class FeriadoAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    """Días no laborables además de los domingos: no se programa y no hay jornada."""
    list_display = ('fecha', 'descripcion')
    date_hierarchy = 'fecha'
    search_fields = ('descripcion',)
    accion_modificacion = Accion.CONFIG


@admin.register(TipoIncidencia)
class TipoIncidenciaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('nombre', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    accion_modificacion = Accion.CONFIG


@admin.register(ConfiguracionAccesos)
class ConfiguracionAccesosAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'ventana_sugerencia_min', 'tolerancia_puntualidad_min',
                    'umbral_prolongada_min', 'meta_permanencia_min', 'hora_pre_ruta', 'hora_cierre_operativo',
                    'modificado_en')
    accion_modificacion = Accion.CONFIG
    readonly_fields = ('licencias_revisadas_el',)

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not ConfiguracionAccesos.objects.exists()


@admin.register(Transporte)
class TransporteAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('placa', 'tamano', 'es_interno', 'activo', 'km_actual', 'km_actualizado_en', 'creado_por', 'creado_en')
    list_select_related = ('tamano', 'creado_por')
    list_filter = ('es_interno', 'activo', 'tamano')
    search_fields = ('placa', 'placa_normalizada')
    # El último kilometraje lo mantiene la portería (kilometraje.py): solo lectura.
    readonly_fields = ('placa_normalizada', 'km_actual', 'km_actualizado_en', 'km_origen', 'creado_por', 'creado_en',
                       'modificado_por', 'modificado_en')


class VencimientoLicenciaFilter(admin.SimpleListFilter):
    title = "vencimiento de licencias"
    parameter_name = 'licencia'

    def lookups(self, request, model_admin):
        return [('alerta', "En aviso (por vencer o recién vencidas)"), ('vencidas', "Con alguna vencida"),
                ('sin', "Sin licencia registrada")]

    def queryset(self, request, queryset):
        hoy = tiempo.hoy()
        if self.value() == 'alerta':
            return queryset.filter(licencias.filtro_en_alerta(hoy, ConfiguracionAccesos.obtener()))
        if self.value() == 'vencidas':
            return queryset.filter(Q(licencia_vencimiento__lt=hoy) | Q(licencia_especial_vencimiento__lt=hoy))
        if self.value() == 'sin':
            return queryset.filter(licencia_vencimiento__isnull=True, licencia_especial_vencimiento__isnull=True)
        return queryset


@admin.register(Conductor)
class ConductorAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('numero_documento', 'tipo_documento', 'apellido_paterno', 'apellido_materno', 'nombres',
                    'licencia_numero', 'licencia_categoria', 'licencia_vencimiento', 'licencia_especial_categoria',
                    'licencia_especial_vencimiento', 'notificar_vencimiento', 'activo')
    list_select_related = ('tipo_documento',)
    list_filter = ('tipo_documento', 'activo', 'notificar_vencimiento', VencimientoLicenciaFilter, 'licencia_categoria')
    search_fields = ('numero_documento', 'apellido_paterno', 'apellido_materno', 'nombres', 'licencia_numero')
    readonly_fields = ('creado_por', 'creado_en', 'modificado_por', 'modificado_en')


@admin.register(UsuarioCD)
class UsuarioCDAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ('usuario', 'cd')
    list_select_related = ('usuario', 'cd')
    list_filter = ('cd',)
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name')
    accion_modificacion = Accion.CONFIG

    def has_delete_permission(self, request, obj=None):
        return en_piloto(request.user) and admin.ModelAdmin.has_delete_permission(self, request, obj)

    def delete_model(self, request, obj):
        auditoria.registrar(request.user, Accion.CONFIG, obj, antes=auditoria.foto(obj),
                            motivo="Se retiró la autorización del usuario en el CD.")
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)


class SoloLecturaAdmin(PilotoAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Programacion)
class ProgramacionAdmin(SoloLecturaAdmin):
    """Solo consulta: las programaciones se crean, editan y cancelan desde el módulo, donde
    se aplican las reglas de horario y se auditan los cambios."""
    list_display = ('codigo', 'fecha', 'hora', 'cd', 'transporte', 'tipo', 'razon_social', 'estado')
    list_select_related = ('cd', 'transporte')
    list_filter = ('estado', 'tipo', 'cd', 'fecha')
    search_fields = ('transporte__placa_normalizada', 'razon_social', 'documento', 'motivo')
    date_hierarchy = 'fecha'


@admin.register(Tramo)
class TramoAdmin(SoloLecturaAdmin):
    """Solo consulta: entradas y salidas se registran en Portería y se corrigen desde el módulo."""
    list_display = ('transporte', 'clase', 'cd', 'fecha_local_entrada', 'hora_local_entrada', 'salida_en',
                    'minutos_permanencia', 'vigente', 'corregido', 'cierre_administrativo')
    list_select_related = ('transporte', 'cd')
    list_filter = ('clase', 'cd', 'vigente', 'corregido', 'cierre_administrativo', 'fecha_local_entrada')
    search_fields = ('transporte__placa_normalizada',)


@admin.register(Jornada)
class JornadaAdmin(SoloLecturaAdmin):
    """Solo consulta: la jornada se crea con la primera programación INTERNO y avanza con la portería."""
    list_display = ('codigo', 'fecha', 'transporte', 'cd', 'conductor', 'estado', 'primera_salida', 'post_ruta_en',
                    'motivo_cierre')
    list_select_related = ('transporte', 'cd', 'conductor')
    list_filter = ('estado', 'motivo_cierre', 'cd', 'fecha')
    search_fields = ('transporte__placa_normalizada',)
    date_hierarchy = 'fecha'


@admin.register(Incidencia)
class IncidenciaAdmin(SoloLecturaAdmin):
    """Solo consulta: las averías se registran en Portería o en la ficha del transporte."""
    list_display = ('codigo', 'transporte', 'tipo', 'inicio', 'fin', 'origen', 'registrada_por')
    list_select_related = ('transporte', 'tipo', 'registrada_por')
    list_filter = ('tipo', 'origen', 'abierta')
    search_fields = ('transporte__placa_normalizada', 'descripcion')


@admin.register(RegistroAuditoria)
class RegistroAuditoriaAdmin(SoloLecturaAdmin):
    list_display = ('fecha_lima', 'autor', 'accion', 'entidad', 'objeto_repr', 'motivo')
    list_select_related = ('usuario',)
    list_filter = ('accion', 'entidad')
    search_fields = ('objeto_repr', 'usuario__username', 'motivo')

    @admin.display(description="Usuario", ordering='usuario')
    def autor(self, obj):
        return obj.autor

    @admin.display(description="Fecha (hora de Lima)", ordering='fecha')
    def fecha_lima(self, obj):
        return tiempo.a_lima(obj.fecha).strftime('%d/%m/%Y %H:%M:%S')
