from django.urls import path
from django.contrib.auth import views as auth_views
from . import views


urlpatterns = [
     # --- AUTENTICACIÓN (LOGIN/LOGOUT) ---
    # Se recomienda gestionar el login desde aquí para evitar usar el admin de Django
    path('login/', auth_views.LoginView.as_view(template_name='planificacion/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # Ruta principal del Dashboard
    path('', views.dashboard, name='dashboard'),
    
    # Ruta para el formulario de nuevo registro
    path('nuevo/', views.registrar_planificacion, name='registrar_planificacion'),
    
    # Ruta para la edición de un registro existente (usa el ID o PK)
    path('editar/<int:pk>/', views.editar_planificacion, name='editar_planificacion'),
       # NUEVAS RUTAS
    path('detalle/<str:fecha>/', views.detalle_dia, name='detalle_dia'),
    path('busqueda/', views.lista_busqueda, name='lista_busqueda'),
    path('capacidad/actualizar/', views.actualizar_capacidad, name='actualizar_capacidad'),
    path('eliminar-planificacion/<int:pk>/', views.eliminar_planificacion, name='eliminar_planificacion'),
    # RUTA CRÍTICA PARA EL AJUSTE DE CAPACIDAD
    path('dashboard/', views.dashboard, name='dashboard'),
    path('ajustar-capacidad/', views.gestionar_capacidad_diaria, name='gestionar_capacidad_diaria'),

    # --- MÓDULO DE ESTADÍSTICA (SOLO LECTURA) ---
    path('estadisticas/', views.estadisticas_dashboard, name='estadisticas_dashboard'),

    # --- CARGA MASIVA POR EXCEL ---
    path('programar/masivo/', views.carga_masiva_excel, name='carga_masiva_excel'),
    path('programar/masivo/plantilla/', views.descargar_plantilla_excel, name='descargar_plantilla_excel'),

    # --- EXPORTACIÓN A EXCEL ---
    path('busqueda/exportar/', views.exportar_busqueda_excel, name='exportar_busqueda_excel'),
    path('detalle/<str:fecha>/exportar/', views.exportar_detalle_dia_excel, name='exportar_detalle_dia_excel'),
]