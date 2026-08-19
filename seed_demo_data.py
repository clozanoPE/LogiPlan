"""
Script de utilidad para generar datos de prueba (demo) en la base de datos
local de LogiPlan, cubriendo Junio, Julio y parte de Agosto 2026, con el
fin de poder visualizar el módulo "Estadística" con datos representativos.

Uso:
    python seed_demo_data.py

No forma parte del flujo de despliegue (no está referenciado en el Procfile).
Es seguro de borrar una vez que ya no se necesiten los datos de prueba.
"""
import os
import random

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from datetime import date, timedelta  # noqa: E402

from django.contrib.auth.models import User  # noqa: E402

from planificacion.models import CapacidadDiaria, PlanificacionDespacho  # noqa: E402

FECHA_INICIO = date(2026, 6, 1)
FECHA_FIN = date(2026, 8, 19)  # "hoy" según el contexto actual del proyecto

# --- Guardia de seguridad: no duplicar si ya existen despachos en el rango ---
if PlanificacionDespacho.objects.filter(fecha_despacho__range=[FECHA_INICIO, FECHA_FIN]).exists():
    print(">>> Ya existen despachos en el rango Jun-Ago 2026. Abortando para evitar duplicados.")
    raise SystemExit(0)

usuario_registrador = (
    User.objects.filter(groups__name='Planeamiento').first()
    or User.objects.filter(is_superuser=True).first()
)
if not usuario_registrador:
    print(">>> No se encontró ningún usuario para asignar como 'creado_por'. Abortando.")
    raise SystemExit(1)

CLIENTES = ['SPSA', 'SUPERMERCADO PERUANOS', 'TOTTUS PERU', 'PLAZA VEA']
SEDES = ['CD MASS ESTE', 'CD MASS SUR', 'CD MASS PUNTA HERMOSA', 'CD PUNTA NEGRA FLUJO CONTINUO', 'CD LURIN CENTRAL']
ORIGENES = ['Almacén Lurín', 'CD Independencia', 'CD Ventanilla', 'Planta Callao', 'CD Punta Hermosa']
CONDUCTORES = [
    'YOVER ALIPIO GUITIERREZ', 'JESUS CALCIN NANDITO', 'LLILVER ARIMUYA SAMPAYO',
    'WILLIAM ENRIQUE DEL VALLE', 'MARIA PEREZ', 'CARLOS RAMOS TORRES', 'LUIS FERNANDEZ QUISPE',
]
PLACAS = [f'ABC-{100 + i}' for i in range(12)]  # pool de 12 placas

# --- Excepciones de capacidad: feriados peruanos reales + eventos operativos ---
EXCEPCIONES = [
    (date(2026, 6, 29), 3, 'Feriado'),                # San Pedro y San Pablo
    (date(2026, 7, 6), 10, 'Pico de demanda'),         # campaña previa a Fiestas Patrias
    (date(2026, 7, 28), 0, 'Feriado'),                 # Fiestas Patrias
    (date(2026, 7, 29), 0, 'Feriado'),                 # Fiestas Patrias
    (date(2026, 7, 31), 4, 'Mantenimiento de unidades'),
    (date(2026, 8, 6), 3, 'Feriado'),                  # Batalla de Junín
    (date(2026, 8, 12), 10, 'Pico de demanda'),
    (date(2026, 8, 17), 4, 'Mantenimiento de unidades'),
]
for fecha, limite, motivo in EXCEPCIONES:
    CapacidadDiaria.objects.get_or_create(
        fecha=fecha, defaults={'limite_personalizado': limite, 'motivo': motivo}
    )

# --- Generación de despachos día a día (Lunes a Sábado, igual que la operación real) ---
registros = []
oc_counter = 1000
dia = FECHA_INICIO
while dia <= FECHA_FIN:
    if dia.weekday() == 6:  # Domingo: sin operación
        dia += timedelta(days=1)
        continue

    # La mayoría de días entre 3 y 7 unidades; ~1 de cada 5 días se empuja
    # a 8-10 para generar días críticos/sobresaturados en los paneles.
    if random.random() < 0.2:
        num_despachos = random.randint(8, 10)
    else:
        num_despachos = random.randint(3, 7)

    placas_del_dia = random.sample(PLACAS, min(num_despachos, len(PLACAS)))

    for placa in placas_del_dia:
        oc_counter += 1
        registros.append(PlanificacionDespacho(
            fecha_despacho=dia,
            cliente=random.choice(CLIENTES),
            sede=random.choice(SEDES),
            unidad_placa=placa,
            conductor=random.choice(CONDUCTORES),
            orden_compra=f'OC-2026-{oc_counter}',
            carga_origen=random.choice(ORIGENES),
            pallets=random.randint(8, 40),
            creado_por=usuario_registrador,
        ))

    dia += timedelta(days=1)

# bulk_create evita disparar full_clean()/clean() (que bloquea fechas pasadas),
# lo cual es necesario y apropiado para cargar datos históricos de prueba.
PlanificacionDespacho.objects.bulk_create(registros)

print(f">>> Se crearon {len(registros)} despachos de prueba entre {FECHA_INICIO} y {FECHA_FIN}.")
print(f">>> Se registraron {len(EXCEPCIONES)} excepciones de capacidad (CapacidadDiaria).")
print(f">>> creado_por asignado: {usuario_registrador.username}")
