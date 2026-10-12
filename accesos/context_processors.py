import os

from django.utils.functional import SimpleLazyObject

from .permisos import ADMINISTRADOR, CONSULTA, DESPACHO, VIGILANCIA, roles_de, solo_accesos

VALORES_FALSOS = {'0', 'false', 'no', 'off'}


def modulo_visible():
    """El enlace «Accesos» de base.html se muestra por defecto a quien tenga un rol del módulo
    (lo deciden los grupos). ACCESOS_ACTIVO=0 es un interruptor de emergencia que lo oculta a todos
    sin volver a desplegar; las pantallas siguen protegidas por rol de todas formas.
    """
    return os.getenv('ACCESOS_ACTIVO', '').strip().lower() not in VALORES_FALSOS


def _alertas_licencias():
    # Import diferido: el context processor se carga con la configuración, antes que los modelos.
    from . import licencias
    licencias.revisar_si_corresponde()  # desactivación automática, como máximo una vez al día
    return licencias.alertas()


def accesos(request):
    user = getattr(request, 'user', None)
    roles = roles_de(user) if user is not None else set()
    ve_licencias = bool(roles & {ADMINISTRADOR, DESPACHO})
    return {
        'accesos_roles': sorted(roles),
        'accesos_solo': solo_accesos(user) if user is not None else False,
        'accesos_puede_programar': bool(roles & {ADMINISTRADOR, DESPACHO}),
        'accesos_lectura': bool(roles & {ADMINISTRADOR, DESPACHO, CONSULTA}),
        # Tablero «Hoy» y Dashboard: también para Vigilancia (solo lectura).
        'accesos_ve_indicadores': bool(roles & {ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA}),
        # Consulta de tramos: Vigilancia también, en solo lectura y limitada a sus CD.
        'accesos_ve_tramos': bool(roles & {ADMINISTRADOR, DESPACHO, CONSULTA, VIGILANCIA}),
        'accesos_enlace_visible': bool(roles) and modulo_visible(),
        # Campanita de vencimiento de licencias (Administrador y Despacho). Perezosa: solo consulta la
        # base cuando una plantilla del módulo la usa, nunca en las pantallas de Planificación.
        'accesos_ve_licencias': ve_licencias,
        'accesos_alertas_licencias': SimpleLazyObject(_alertas_licencias) if ve_licencias else [],
    }
