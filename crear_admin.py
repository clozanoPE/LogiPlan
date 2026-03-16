import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User, Group

# Crear grupos
for nombre in ['Planeamiento', 'Comercial']:
    Group.objects.get_or_create(name=nombre)

# Crear superusuario
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'clozano@daryza.com', 'daryzA26%')
    print("Datos iniciales Daryza creados.")