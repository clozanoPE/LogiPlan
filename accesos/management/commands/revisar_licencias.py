"""
Revisión de vencimiento de licencias: desactiva las notificaciones de los conductores cuyas
licencias vencieron todas hace más del plazo configurado. Normalmente no hace falta ejecutarlo:
el sistema lo hace solo una vez al día, la primera vez que alguien con la campanita entra al módulo.
Sirve para ejecutarlo a mano o desde un cron de Railway si algún día se configura.
"""
from django.core.management.base import BaseCommand

from accesos import licencias, tiempo
from accesos.models import ConfiguracionAccesos


class Command(BaseCommand):
    help = "Desactiva las notificaciones de licencias vencidas hace más del plazo configurado y muestra las alertas."

    def handle(self, *args, **options):
        hoy = tiempo.hoy()
        desactivados = licencias.revisar(hoy)
        ConfiguracionAccesos.objects.filter(pk=ConfiguracionAccesos.obtener().pk).update(licencias_revisadas_el=hoy)
        alertas = licencias.alertas(hoy)
        self.stdout.write(f"Notificaciones desactivadas: {desactivados}. Alertas vigentes: {len(alertas)}.")
        for alerta in alertas:
            self.stdout.write(f"  {alerta.conductor.nombre_completo} · {alerta.etiqueta} {alerta.categoria} "
                              f"{alerta.vencimiento:%d/%m/%Y} · {alerta.texto}")
