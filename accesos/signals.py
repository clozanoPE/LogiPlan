from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .permisos import VIGILANCIA, roles_de


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
