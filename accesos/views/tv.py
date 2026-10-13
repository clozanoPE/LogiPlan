"""
Modo TV (F6.4): una pantalla por CD, para un televisor grande. Solo lectura, sin menú ni filtros, siempre
«hoy». La página se actualiza sola cada minuto pidiendo los datos en JSON (sin recargar): si la red se cae,
mantiene lo último y avisa «Sin conexión desde hh:mm».

Acceso: el rol Pantalla (usuario propio de cada TV, sesión de 30 días) solo a sus CD autorizados;
Administrador, Despacho y Consulta a cualquier CD (para verla o configurarla).
"""
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .. import pantalla, tiempo
from ..models import CentroDistribucion
from ..permisos import ADMINISTRADOR, CONSULTA, DESPACHO, PANTALLA, rol_requerido, tiene_rol
from .porteria import cds_autorizados

SEGUNDOS_ACTUALIZACION = 60


def _cd_permitido(request, codigo):
    cd = get_object_or_404(CentroDistribucion, codigo__iexact=codigo, activo=True)
    if not tiene_rol(request.user, ADMINISTRADOR, DESPACHO, CONSULTA) and not cds_autorizados(request.user).filter(pk=cd.pk).exists():
        raise Http404("CD no autorizado para esta pantalla.")
    return cd


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, PANTALLA)
def elegir(request):
    """Lista de CD con modo TV. Un usuario de pantalla con un solo CD entra directo."""
    if tiene_rol(request.user, ADMINISTRADOR, DESPACHO, CONSULTA):
        cds = CentroDistribucion.objects.filter(activo=True)
    else:
        cds = cds_autorizados(request.user)
    if cds.count() == 1:
        return redirect('accesos:tv', codigo=cds.first().codigo)
    return render(request, 'accesos/tv/elegir.html', {'cds': cds})


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, PANTALLA)
def tv(request, codigo):
    cd = _cd_permitido(request, codigo)
    return render(request, 'accesos/tv/pantalla.html', {
        'cd': cd, 'datos': pantalla.datos(cd, tiempo.ahora()), 'segundos': SEGUNDOS_ACTUALIZACION,
    })


@rol_requerido(ADMINISTRADOR, DESPACHO, CONSULTA, PANTALLA)
def tv_datos(request, codigo):
    cd = _cd_permitido(request, codigo)
    return JsonResponse(pantalla.datos(cd, tiempo.ahora()))
