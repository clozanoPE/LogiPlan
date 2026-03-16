"""
WSGI config for core project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Inicialización manual para ejecutar lógica antes de la aplicación
django.setup()

from django.contrib.auth.models import User, Group

def setup_initial_data():
    try:
        # 1. Crear Grupos fundamentales
        grupos = ['Planeamiento', 'Comercial']
        for nombre in grupos:
            Group.objects.get_or_create(name=nombre)
        
        # 2. Crear el Usuario Administrador de Daryza
        # Importante: Si tus datos locales dependen de un ID específico, 
        # asegúrate de que este sea el primer usuario creado.
        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser(
                'admin_daryza', 
                'clozano@daryza.com', 
                'daryzA26%'
            )
            print("Estructura inicial: Grupos y Usuario creados.")
            
    except Exception as e:
        print(f"Error en la configuración inicial: {e}")

# Ejecutar la configuración
setup_initial_data()

application = get_wsgi_application()


