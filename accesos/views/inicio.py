from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render

from ..models import ConfiguracionAccesos
from ..permisos import solo_pantalla, usa_accesos


@login_required
def inicio(request):
    """Página de entrada del módulo. Muestra solo las secciones del rol del usuario."""
    if not usa_accesos(request.user):
        raise PermissionDenied("Su usuario no tiene un rol del módulo de Accesos.")
    if solo_pantalla(request.user):
        return redirect('accesos:tv_elegir')  # usuario de una TV: va directo a su pantalla
    return render(request, 'accesos/inicio.html', {
        'config': ConfiguracionAccesos.obtener(),
    })
