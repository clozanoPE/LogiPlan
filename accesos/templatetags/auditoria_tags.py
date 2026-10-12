from django import template

register = template.Library()


@register.filter
def valor_auditoria(valor):
    """Valor de un campo en la auditoría: Sí/No para booleanos (False no es «vacío») y «—» si no hay dato."""
    if valor is True:
        return "Sí"
    if valor is False:
        return "No"
    if valor is None or valor == '':
        return "—"
    return valor
