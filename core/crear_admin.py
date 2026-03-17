import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User, Group

try:
    # Grupos
    Group.objects.get_or_create(name='Planeamiento')
    Group.objects.get_or_create(name='Comercial')
    
    # Admin
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', 'clozano@daryza.com', 'daryzA26%')
        print(">>> DATOS CREADOS CORRECTAMENTE")
    else:
        print(">>> EL ADMIN YA EXISTE")
except Exception as e:
    print(f">>> ERROR: {e}")