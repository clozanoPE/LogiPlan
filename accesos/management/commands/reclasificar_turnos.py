"""
Turno noche (15/10/2026): las rutas INTERNO que ya estaban cargadas para el futuro cuando se desplegó la regla de
turnos quedaron ligadas al turno día (migración 0016). Este comando las vuelve a ligar a su turno según la franja
de su hora de salida (desde las 20:00, turno noche; hasta las 06:15, turno noche del día anterior). Solo toca
rutas pendientes, sin ingreso y cuya hora todavía no pasó; es idempotente. Ejecutarlo una vez después de migrar:

    python manage.py reclasificar_turnos            (muestra lo que cambiaría, sin grabar)
    python manage.py reclasificar_turnos --grabar
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accesos import jornadas, servicios, tiempo
from accesos.models import ConfiguracionAccesos, Programacion


class Command(BaseCommand):
    help = "Liga las rutas INTERNO futuras ya cargadas a su turno (día o noche) según su hora de salida."

    def add_arguments(self, parser):
        parser.add_argument('--grabar', action='store_true', help="Graba los cambios (sin esto solo los muestra).")

    def handle(self, *args, **options):
        ahora = tiempo.ahora()
        config = ConfiguracionAccesos.obtener()
        rutas = (Programacion.objects.filter(tipo=Programacion.Tipo.INTERNO, estado=Programacion.Estado.PROGRAMADA,
                                             no_atendida_por_cierre=False, fecha__gte=ahora.date())
                 .exclude(tramos__vigente=True).select_related('jornada', 'transporte').order_by('fecha', 'hora'))
        cambios = []
        for ruta in rutas:
            if servicios.momento(ruta) <= ahora:
                continue
            fecha, turno = jornadas.turno_de(ruta.fecha, ruta.hora, config)
            actual = (ruta.jornada.fecha, ruta.jornada.turno) if ruta.jornada_id else None
            if actual != (fecha, turno):
                cambios.append((ruta, fecha, turno))
        for ruta, fecha, turno in cambios:
            self.stdout.write(f"{ruta.codigo} {ruta.transporte} {ruta.fecha:%d/%m} {ruta.hora:%H:%M} → turno "
                              f"{'noche' if turno == jornadas.NOCHE else 'día'} del {fecha:%d/%m}")
        if options['grabar']:
            with transaction.atomic():
                for ruta, _, _ in cambios:
                    jornadas.sincronizar(ruta, ruta.jornada_id, ahora=ahora)
            self.stdout.write(self.style.SUCCESS(f"Rutas reclasificadas: {len(cambios)}."))
        else:
            self.stdout.write(f"Rutas por reclasificar: {len(cambios)} (use --grabar para aplicar).")
