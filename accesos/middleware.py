from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .permisos import solo_accesos

PREFIJOS_PERMITIDOS = ('/accesos/', '/login/', '/logout/', '/favicon.ico')
# El Administrador del módulo (is_staff) gestiona catálogos, configuración y CD autorizados en Django Admin.
PREFIJO_ADMIN = '/admin/'


class AislamientoAccesosMiddleware:
    """
    Mantiene dentro de /accesos/ a los usuarios cuyos grupos son todos del módulo de Accesos
    (Vigilancia, Consulta): cualquier otra ruta, incluida la raíz tras el login, los lleva al
    inicio del módulo. Excepción: Django Admin para los que tienen is_staff (el rol Administrador
    del módulo), donde solo ven los modelos para los que tienen permiso.

    No actúa sobre nadie más: superusuarios, usuarios sin grupo y usuarios con grupos
    existentes (Planeamiento, Comercial) navegan exactamente igual que antes.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and solo_accesos(user) and not self._ruta_permitida(request.path, user):
            return redirect(reverse('accesos:inicio'))
        return self.get_response(request)

    @staticmethod
    def _ruta_permitida(ruta, user):
        static_url = '/' + settings.STATIC_URL.lstrip('/')
        if ruta.startswith(PREFIJO_ADMIN):
            return user.is_staff
        return ruta.startswith(PREFIJOS_PERMITIDOS) or ruta.startswith(static_url)
