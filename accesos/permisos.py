"""
Roles del módulo de Accesos, basados en grupos de Django con prefijo propio para no
mezclarse con los grupos existentes (Planeamiento, Comercial). Un usuario puede tener
varios roles; el superusuario equivale a Administrador y el grupo Planeamiento equivale a
Despacho (decisión del negocio: Planeamiento programa los ingresos sin un grupo adicional).
"""
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

GRUPO_ADMINISTRADOR = 'Accesos - Administrador'
GRUPO_DESPACHO = 'Accesos - Despacho'
GRUPO_VIGILANCIA = 'Accesos - Vigilancia'
GRUPO_CONSULTA = 'Accesos - Consulta'

ADMINISTRADOR = 'administrador'
DESPACHO = 'despacho'
VIGILANCIA = 'vigilancia'
CONSULTA = 'consulta'

GRUPO_PLANEAMIENTO = 'Planeamiento'  # grupo existente de Planificación

# Grupos propios del módulo. Solo estos cuentan para el aislamiento (solo_accesos).
GRUPOS_ACCESOS = (GRUPO_ADMINISTRADOR, GRUPO_DESPACHO, GRUPO_VIGILANCIA, GRUPO_CONSULTA)

ROL_POR_GRUPO = {
    GRUPO_ADMINISTRADOR: ADMINISTRADOR,
    GRUPO_DESPACHO: DESPACHO,
    GRUPO_VIGILANCIA: VIGILANCIA,
    GRUPO_CONSULTA: CONSULTA,
    GRUPO_PLANEAMIENTO: DESPACHO,  # Planeamiento = Despacho en Accesos
}


def _nombres_grupos(user):
    # Se cachea en el objeto user para no repetir la consulta en la misma petición.
    if not hasattr(user, '_accesos_grupos'):
        user._accesos_grupos = set(user.groups.values_list('name', flat=True))
    return user._accesos_grupos


def en_piloto(user):
    """True si el usuario puede usar el módulo según ACCESOS_USUARIOS_PILOTO ('*' = todos)."""
    permitidos = {u.strip().lower() for u in str(getattr(settings, 'ACCESOS_USUARIOS_PILOTO', '*')).split(',')}
    return '*' in permitidos or user.get_username().lower() in permitidos


def roles_de(user):
    """Conjunto de roles de Accesos del usuario (vacío si el piloto no lo incluye)."""
    if not user.is_authenticated or not en_piloto(user):
        return set()
    roles = {ROL_POR_GRUPO[g] for g in _nombres_grupos(user) if g in ROL_POR_GRUPO}
    if user.is_superuser:
        roles.add(ADMINISTRADOR)
    return roles


def tiene_rol(user, *roles):
    return bool(roles_de(user) & set(roles))


def usa_accesos(user):
    """True si el usuario tiene algún rol del módulo (o es superusuario)."""
    return bool(roles_de(user))


def solo_accesos(user):
    """True si TODOS los grupos del usuario son de Accesos (p. ej. solo Vigilancia o Consulta).

    Estos usuarios no deben ver el módulo de Planificación. Los superusuarios, los usuarios
    sin grupo y los que además tienen grupos existentes (Planeamiento, Comercial) quedan fuera.
    """
    if not user.is_authenticated or user.is_superuser:
        return False
    grupos = _nombres_grupos(user)
    return bool(grupos) and grupos.issubset(GRUPOS_ACCESOS)


def rol_requerido(*roles):
    """Exige sesión iniciada y al menos uno de los roles indicados (403 si no lo tiene)."""
    def decorador(vista):
        @wraps(vista)
        @login_required
        def envoltura(request, *args, **kwargs):
            if not tiene_rol(request.user, *roles):
                raise PermissionDenied("No tiene un rol autorizado para esta pantalla del módulo de Accesos.")
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
