from django.urls import path

from .views import carga, control, dashboard, flota, inicio, maestros, porteria, programacion, trazabilidad, tv

app_name = 'accesos'

urlpatterns = [
    path('', inicio.inicio, name='inicio'),

    # --- PORTERÍA (F3, rol Vigilancia) ---
    path('porteria/', porteria.porteria, name='porteria'),
    path('porteria/cd/', porteria.elegir_cd, name='porteria_cd'),
    path('porteria/entrada/', porteria.registrar_entrada, name='porteria_entrada'),
    path('porteria/salida/', porteria.registrar_salida, name='porteria_salida'),
    path('porteria/inicio-jornada/', porteria.registrar_inicio_jornada, name='porteria_inicio_jornada'),
    path('porteria/post-ruta/', porteria.registrar_post_ruta, name='porteria_post_ruta'),
    path('porteria/dentro/', porteria.dentro_ahora, name='porteria_dentro'),
    path('porteria/programaciones/', porteria.programaciones_hoy, name='porteria_programaciones'),
    path('porteria/programaciones/exportar/', porteria.programaciones_exportar, name='porteria_programaciones_exportar'),

    # --- DASHBOARD DE GERENCIA (F5, F6.3) Y MODO TV (F6.4) ---
    path('dashboard/', dashboard.dashboard, name='dashboard'),
    path('tv/', tv.elegir, name='tv_elegir'),
    path('tv/<slug:codigo>/', tv.tv, name='tv'),
    path('tv/<slug:codigo>/datos/', tv.tv_datos, name='tv_datos'),

    # --- CONTROL Y CORRECCIONES (F4) ---
    path('hoy/', control.tablero_hoy, name='tablero_hoy'),
    path('tramos/', control.tramos_lista, name='tramos_lista'),
    path('tramos/exportar/', control.tramos_exportar, name='tramos_exportar'),
    path('tramos/<int:pk>/', control.tramo_detalle, name='tramo_detalle'),
    path('tramos/<int:pk>/corregir/', control.tramo_corregir, name='tramo_corregir'),
    path('tramos/<int:pk>/anular/', control.tramo_anular, name='tramo_anular'),
    path('tramos/<int:pk>/cierre/', control.tramo_cierre, name='tramo_cierre'),
    path('tramos/<int:pk>/convertir-post-ruta/', control.tramo_convertir_post_ruta, name='tramo_convertir_post_ruta'),
    path('auditoria/', control.auditoria_lista, name='auditoria_lista'),
    path('auditoria/exportar/', control.auditoria_exportar, name='auditoria_exportar'),

    # --- TRAZABILIDAD DE UNA PLACA EN UN DÍA ---
    path('trazabilidad/', trazabilidad.trazabilidad_placa, name='trazabilidad'),
    path('trazabilidad/exportar/', trazabilidad.trazabilidad_exportar, name='trazabilidad_exportar'),

    # --- PROGRAMACIONES (F2) ---
    path('programaciones/', programacion.agenda, name='programacion_agenda'),
    path('programaciones/exportar/', programacion.exportar, name='programacion_exportar'),
    path('programaciones/nueva/', programacion.crear, name='programacion_crear'),
    path('programaciones/<int:pk>/', programacion.detalle, name='programacion_detalle'),
    path('programaciones/<int:pk>/editar/', programacion.editar, name='programacion_editar'),
    path('programaciones/<int:pk>/cancelar/', programacion.cancelar, name='programacion_cancelar'),
    path('programaciones/<int:pk>/pase/', programacion.pase, name='programacion_pase'),

    # --- DISPONIBILIDAD DE FLOTA (F6: averías) ---
    path('flota/disponibilidad/', flota.disponibilidad, name='flota_disponibilidad'),
    path('flota/disponibilidad/exportar/', flota.disponibilidad_exportar, name='flota_exportar'),
    path('flota/no-disponible/', flota.registrar_no_disponible, name='flota_registrar'),
    path('flota/incidencias/<int:pk>/habilitar/', flota.habilitar, name='flota_habilitar'),

    # --- MAESTROS ---
    path('transportes/', maestros.transportes_lista, name='transportes_lista'),
    path('transportes/exportar/', maestros.transportes_exportar, name='transportes_exportar'),
    path('transportes/nuevo/', maestros.transporte_crear, name='transporte_crear'),
    path('transportes/nuevo-rapido/', maestros.transporte_crear_rapido, name='transporte_crear_rapido'),
    path('transportes/<int:pk>/editar/', maestros.transporte_editar, name='transporte_editar'),
    path('transportes/<int:pk>/qr/', maestros.transporte_qr, name='transporte_qr'),
    path('transportes/<int:pk>/qr.png', maestros.transporte_qr_png, name='transporte_qr_png'),
    path('conductores/', maestros.conductores_lista, name='conductores_lista'),
    path('conductores/exportar/', maestros.conductores_exportar, name='conductores_exportar'),
    path('conductores/nuevo/', maestros.conductor_crear, name='conductor_crear'),
    path('conductores/nuevo-rapido/', maestros.conductor_crear_rapido, name='conductor_crear_rapido'),
    path('conductores/<int:pk>/editar/', maestros.conductor_editar, name='conductor_editar'),

    # --- CARGA MASIVA POR EXCEL (transportes, conductores, programaciones) ---
    path('carga/<slug:tipo>/', carga.carga, name='carga_masiva'),
    path('carga/<slug:tipo>/plantilla/', carga.plantilla, name='carga_plantilla'),
]
