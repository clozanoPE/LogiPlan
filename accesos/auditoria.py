"""
Registro explícito de auditoría. Se llama desde vistas, servicios y Django Admin (no por
señales automáticas) para conocer siempre el usuario y el motivo de cada cambio.
"""
from .models import RegistroAuditoria

Accion = RegistroAuditoria.Accion

# Campos técnicos que no aportan a la trazabilidad y se omiten de las fotos.
CAMPOS_OMITIDOS = {'creado_en', 'modificado_en'}


def foto(obj):
    """Diccionario {campo: valor} de los campos concretos del objeto (FK como id)."""
    if obj is None:
        return None
    return {
        campo.attname: getattr(obj, campo.attname)
        for campo in obj._meta.concrete_fields
        if campo.attname not in CAMPOS_OMITIDOS
    }


def diferencias(antes, despues):
    """Devuelve (antes, despues) reducidos a los campos que cambiaron."""
    cambiados = [clave for clave in despues if antes.get(clave) != despues.get(clave)]
    return ({c: antes.get(c) for c in cambiados}, {c: despues.get(c) for c in cambiados})


def registrar(usuario, accion, obj, antes=None, despues=None, motivo='', cd=None):
    return RegistroAuditoria.objects.create(
        usuario=usuario,
        accion=accion,
        entidad=obj._meta.object_name,
        objeto_id=obj.pk,
        objeto_repr=str(obj)[:200],
        datos_antes=antes,
        datos_despues=despues,
        motivo=motivo,
        cd=cd,
    )


def registrar_creacion(usuario, obj, motivo=''):
    return registrar(usuario, Accion.CREAR, obj, despues=foto(obj), motivo=motivo)


def registrar_modificacion(usuario, obj, foto_antes, accion=Accion.MODIFICAR, motivo=''):
    """Registra solo los campos que cambiaron. Si nada cambió, no registra nada."""
    antes, despues = diferencias(foto_antes, foto(obj))
    if not despues:
        return None
    return registrar(usuario, accion, obj, antes=antes, despues=despues, motivo=motivo)
