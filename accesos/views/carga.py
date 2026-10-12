"""
Carga masiva por Excel (Administrador y Despacho): plantilla, vista previa y confirmación.
La lógica y las validaciones están en accesos/carga_masiva.py.
"""
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from .. import carga_masiva, tiempo
from ..permisos import ADMINISTRADOR, DESPACHO, rol_requerido

CLAVE_SESION = 'accesos_carga_{}'
DESTINOS = {'transportes': 'accesos:transportes_lista', 'conductores': 'accesos:conductores_lista',
            'programaciones': 'accesos:programacion_agenda', 'licencias': 'accesos:conductores_lista'}


def _tipo(clave):
    tipo = carga_masiva.TIPOS.get(clave)
    if tipo is None:
        raise Http404
    return tipo


def _conteo(filas):
    conteo = {estado: 0 for estado in (carga_masiva.OK, carga_masiva.AVISO, carga_masiva.OMITIDA, carga_masiva.ERROR)}
    for fila in filas:
        conteo[fila.estado] += 1
    conteo['grabables'] = conteo[carga_masiva.OK] + conteo[carga_masiva.AVISO]
    conteo['total'] = len(filas)
    return conteo


@rol_requerido(ADMINISTRADOR, DESPACHO)
def plantilla(request, tipo):
    tipo = _tipo(tipo)
    respuesta = HttpResponse(carga_masiva.libro_plantilla(tipo),
                             content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = f'attachment; filename="Plantilla_{tipo.titulo}.xlsx"'
    return respuesta


@rol_requerido(ADMINISTRADOR, DESPACHO)
def carga(request, tipo):
    tipo = _tipo(tipo)
    clave_sesion = CLAVE_SESION.format(tipo.clave)
    contexto = {'tipo': tipo, 'destino': DESTINOS[tipo.clave], 'max_filas': carga_masiva.MAX_FILAS,
                'listas': tipo.listas()}
    accion = request.POST.get('accion', '')

    if request.method == 'POST' and accion == 'previsualizar':
        archivo = request.FILES.get('archivo')
        if archivo is None:
            messages.error(request, "Seleccione el archivo Excel.")
            return redirect('accesos:carga_masiva', tipo=tipo.clave)
        try:
            crudas = carga_masiva.leer_archivo(archivo, tipo.columnas)
        except carga_masiva.ArchivoInvalido as error:
            messages.error(request, str(error))
            return redirect('accesos:carga_masiva', tipo=tipo.clave)
        filas = tipo.evaluar(crudas, tiempo.ahora())
        # Se guardan en la sesión los valores ya normalizados (texto) para confirmar sin volver a subir.
        request.session[clave_sesion] = {'archivo': archivo.name[:120],
                                         'filas': [[f.numero, f.valores] for f in filas]}
        contexto.update(paso='vista_previa', filas=filas, conteo=_conteo(filas), archivo=archivo.name)
        return render(request, 'accesos/carga/carga.html', contexto)

    if request.method == 'POST' and accion == 'confirmar':
        pendiente = request.session.pop(clave_sesion, None)
        if not pendiente:
            messages.error(request, "La vista previa ya no está disponible. Vuelva a subir el archivo.")
            return redirect('accesos:carga_masiva', tipo=tipo.clave)
        filas = tipo.confirmar(pendiente['filas'], request.user, tiempo.ahora(), pendiente['archivo'])
        conteo = _conteo(filas)
        grabadas = sum(1 for f in filas if getattr(f, 'objeto', None) is not None)
        conteo['grabadas'] = grabadas
        if grabadas and conteo[carga_masiva.ERROR] == 0:
            messages.success(request, f"Se {tipo.verbo_plural} {grabadas} {tipo.titulo.lower()}.")
        elif grabadas:
            messages.warning(request, f"Se {tipo.verbo_plural} {grabadas} {tipo.titulo.lower()}; "
                                      f"{conteo[carga_masiva.ERROR]} filas con error no se grabaron.")
        else:
            messages.error(request, "No se registró ninguna fila. Revise el detalle.")
        contexto.update(paso='resultado', filas=filas, conteo=conteo, archivo=pendiente['archivo'])
        return render(request, 'accesos/carga/carga.html', contexto)

    if request.method == 'POST' and accion == 'cancelar':
        request.session.pop(clave_sesion, None)
        messages.info(request, "Carga cancelada; no se grabó nada.")
        return redirect('accesos:carga_masiva', tipo=tipo.clave)

    contexto['paso'] = 'subir'
    return render(request, 'accesos/carga/carga.html', contexto)
