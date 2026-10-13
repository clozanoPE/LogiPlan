from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .permisos import PANTALLA, VIGILANCIA, roles_de

SESION_PANTALLA_DIAS = 30


@receiver(user_logged_in)
def limitar_sesion_vigilancia(sender, request, user, **kwargs):
    """Las tablets se comparten entre turnos: la sesión de Vigilancia dura solo N horas.

    Afecta únicamente a la sesión de ese usuario; la duración global del proyecto no cambia.
    """
    if request is None or VIGILANCIA not in roles_de(user):
        return
    from .models import ConfiguracionAccesos
    horas = ConfiguracionAccesos.obtener().sesion_vigilancia_horas
    request.session.set_expiry(horas * 3600)


@receiver(user_logged_in)
def sesion_larga_pantalla(sender, request, user, **kwargs):
    """La TV de un CD queda encendida días seguidos: su sesión dura 30 días (solo la del rol Pantalla)."""
    if request is None or roles_de(user) != {PANTALLA}:
        return
    request.session.set_expiry(SESION_PANTALLA_DIAS * 24 * 3600)
