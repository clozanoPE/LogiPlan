"""
Vencimiento de licencias de conducir: alertas de la campanita y desactivación automática.

- Una licencia «alerta» desde `aviso_licencia_dias` (30) antes de su vencimiento hasta
  `gracia_licencia_dias` (7) después, solo si el conductor está activo y tiene activas las
  notificaciones. Las alertas se calculan al momento (no se guardan): si alguien renueva la
  licencia, la alerta desaparece sola.
- Pasado ese plazo, cuando TODAS las licencias registradas del conductor quedaron atrás, sus
  notificaciones se desactivan solas (`revisar`), con auditoría a nombre de «Sistema». Si una
  licencia aún no vence (p. ej. la A-IV vence meses después), la casilla sigue activa.
- Al renovar (nueva fecha de vencimiento posterior y vigente), las notificaciones se vuelven a
  activar solas (`renovada`), salvo que el usuario las apague en ese mismo cambio.

No hay tareas programadas en Railway: la revisión corre como máximo una vez al día, la primera
vez que alguien con la campanita abre una pantalla del módulo (`revisar_si_corresponde`). También
existe el comando `python manage.py revisar_licencias`.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q

from . import auditoria, tiempo
from .models import Conductor, ConfiguracionAccesos, normalizar_licencia

VIGENTE = 'VIGENTE'
POR_VENCER = 'POR_VENCER'
VENCIDA = 'VENCIDA'

MOTIVO_DESACTIVACION = ("Desactivación automática: todas las licencias del conductor llevan vencidas "
                        "más de {dias} días.")


@dataclass
class Alerta:
    conductor: Conductor
    etiqueta: str        # «Licencia» o «Licencia especial»
    categoria: str
    vencimiento: date
    dias: int            # días que faltan (negativo: días desde que venció)

    @property
    def vencida(self):
        return self.dias < 0

    @property
    def texto(self):
        if self.dias > 1:
            return f"vence en {self.dias} días"
        if self.dias == 1:
            return "vence mañana"
        if self.dias == 0:
            return "vence hoy"
        return f"venció hace {-self.dias} día{'s' if self.dias < -1 else ''}"


def estado(vencimiento, hoy, config):
    """Estado de una licencia: VIGENTE, POR_VENCER (dentro del aviso) o VENCIDA. None si no hay fecha."""
    if vencimiento is None:
        return None
    if vencimiento < hoy:
        return VENCIDA
    if vencimiento <= hoy + timedelta(days=config.aviso_licencia_dias):
        return POR_VENCER
    return VIGENTE


def _ventana(hoy, config):
    return hoy - timedelta(days=config.gracia_licencia_dias), hoy + timedelta(days=config.aviso_licencia_dias)


def filtro_en_alerta(hoy, config):
    """Q de los conductores con alguna licencia dentro de la ventana de aviso (sin mirar la casilla)."""
    desde, hasta = _ventana(hoy, config)
    return (Q(licencia_vencimiento__range=(desde, hasta))
            | Q(licencia_especial_vencimiento__range=(desde, hasta)))


def alertas(hoy=None, config=None):
    """Alertas vigentes de la campanita, de la más urgente a la menos urgente."""
    hoy = hoy or tiempo.hoy()
    config = config or ConfiguracionAccesos.obtener()
    desde, hasta = _ventana(hoy, config)
    conductores = (Conductor.objects.filter(activo=True, notificar_vencimiento=True)
                   .filter(filtro_en_alerta(hoy, config)).select_related('tipo_documento'))
    lista = []
    for conductor in conductores:
        for etiqueta, categoria, vencimiento in conductor.licencias():
            if desde <= vencimiento <= hasta:
                lista.append(Alerta(conductor, etiqueta, categoria, vencimiento, (vencimiento - hoy).days))
    lista.sort(key=lambda a: (a.dias, a.conductor.nombre_completo))
    return lista


def revisar(hoy=None, config=None):
    """Desactiva las notificaciones de los conductores cuyas licencias registradas vencieron todas
    hace más de `gracia_licencia_dias`. Devuelve cuántos conductores se desactivaron."""
    hoy = hoy or tiempo.hoy()
    config = config or ConfiguracionAccesos.obtener()
    limite = hoy - timedelta(days=config.gracia_licencia_dias)
    candidatos = Conductor.objects.filter(notificar_vencimiento=True).filter(
        Q(licencia_vencimiento__isnull=False) | Q(licencia_especial_vencimiento__isnull=False)
    ).exclude(licencia_vencimiento__gte=limite).exclude(licencia_especial_vencimiento__gte=limite)
    desactivados = 0
    for conductor in candidatos:
        with transaction.atomic():
            # Se re-evalúa sobre la fila bloqueada por si alguien la editó mientras tanto.
            actual = Conductor.objects.select_for_update().get(pk=conductor.pk)
            if not actual.notificar_vencimiento or any(v >= limite for _, _, v in actual.licencias()):
                continue
            foto_antes = auditoria.foto(actual)
            actual.notificar_vencimiento = False
            actual.save(update_fields=['notificar_vencimiento', 'modificado_en'])
            auditoria.registrar_modificacion(
                None, actual, foto_antes, motivo=MOTIVO_DESACTIVACION.format(dias=config.gracia_licencia_dias))
            desactivados += 1
    return desactivados


def revisar_si_corresponde(hoy=None):
    """Ejecuta `revisar` una sola vez por día (hora de Lima), aunque haya pedidos simultáneos:
    solo el pedido que logra marcar la fecha en la configuración hace la revisión."""
    hoy = hoy or tiempo.hoy()
    config = ConfiguracionAccesos.obtener()
    marcada = (ConfiguracionAccesos.objects.filter(pk=config.pk)
               .exclude(licencias_revisadas_el=hoy).update(licencias_revisadas_el=hoy))
    if not marcada:
        return 0
    return revisar(hoy, config)


def renovada(antes, despues, hoy):
    """True si alguna fecha de vencimiento cambió a una fecha posterior y aún vigente (renovación)."""
    for anterior, nueva in zip(antes, despues):
        if nueva and nueva != anterior and nueva >= hoy and (anterior is None or nueva > anterior):
            return True
    return False


def normalizar_categoria(texto, opciones):
    """'a iiib', 'A-IIIB', 'AIIIb' -> 'A-IIIb'. Devuelve el texto tal cual si no coincide."""
    clave = normalizar_licencia(texto)
    for valor, _ in opciones:
        if normalizar_licencia(valor) == clave:
            return valor
    return (texto or '').strip()

