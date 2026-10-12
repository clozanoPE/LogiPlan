"""
Paginación común de las grillas del módulo (Transportes, Conductores, Tramos y Auditoría): 15 registros por
página y números con saltos («1 … 4 5 [6] 7 8 … 28»), conservando los filtros de la URL.
"""
from django.core.paginator import Paginator

POR_PAGINA = 15


def paginar(request, objetos, por_pagina=POR_PAGINA):
    """Contexto de la plantilla `accesos/_paginacion.html`. `objetos` debe tener un orden total (sin empates)."""
    pagina = Paginator(objetos, por_pagina).get_page(request.GET.get('page'))
    parametros = request.GET.copy()
    parametros.pop('page', None)
    return {
        'page_obj': pagina,
        'querystring': parametros.urlencode(),
        'paginas': list(pagina.paginator.get_elided_page_range(pagina.number, on_each_side=1, on_ends=1)),
    }
