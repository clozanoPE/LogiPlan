"""
Datos de demostración del módulo de Accesos para la base LOCAL (no se usa en producción y no
está en el Procfile). Crea seis semanas de programaciones y tramos con patrones realistas para
evaluar el Dashboard (horas pico, un CD más lento, una placa y un conductor con más demoras,
llegadas tarde que alargan la estadía) y un día de HOY a medio camino: tramos cerrados,
vehículos todavía dentro y programaciones pendientes para la tarde y para mañana.

La flota propia (6 placas internas) tiene su jornada completa cada día laborable: Pre-Ruta desde las
06:30, 1 a 3 rutas INTERNO, Post-Ruta (con una placa que suele volver fuera de horario y otra que
tarda en salir), pernocta en el CD, una avería ya resuelta y otra abierta (vehículo no disponible
con una programación afectada).

Uso:
    python seed_demo_accesos.py            # crea los datos y los usuarios de prueba (si no existen)
    python seed_demo_accesos.py --borrar   # elimina todo lo creado por la demostración

Todo queda a nombre del usuario `demo_accesos` o de los usuarios `demo_*`, así que se puede
borrar sin tocar datos reales.
"""
import os
import random
import sys
from datetime import time, timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import Group, User  # noqa: E402
from django.db import transaction  # noqa: E402
from django.db.models import Q  # noqa: E402

from accesos import jornadas as reglas_jornada, tiempo  # noqa: E402
from accesos.models import (CentroDistribucion, Conductor, Incidencia, Jornada, Programacion,  # noqa: E402
                            TamanoVehiculo, TipoDocumentoIdentidad, TipoIncidencia, Tramo, Transporte, UsuarioCD)

USUARIO_DEMO = 'demo_accesos'
# Usuarios de prueba por rol, para revisar el módulo en LOCAL (y para las capturas del manual).
# Clave solo de demostración, modificable con la variable de entorno DEMO_ACCESOS_CLAVE.
# Nunca crear estos usuarios en producción.
CLAVE_DEMO = os.getenv('DEMO_ACCESOS_CLAVE', 'Demo-Accesos-2026')
USUARIOS_PRUEBA = [
    # usuario, nombre, apellido, grupos, es_staff, CD autorizados
    ('demo_gerencia', 'Gabriela', 'Torres', ['Accesos - Consulta'], False, []),
    ('demo_vigilancia', 'Julio', 'Ramos', ['Accesos - Vigilancia'], False, ['LURIN']),
    ('demo_despacho', 'Diana', 'Salas', ['Planeamiento', 'Accesos - Despacho'], False, []),
    ('demo_admin', 'Andrés', 'Vega', ['Accesos - Administrador'], True, []),
    ('demo_tv', 'Pantalla', 'Lurín', ['Accesos - Pantalla'], False, ['LURIN']),
]
SEMANAS = 6
INTERNOS = 6  # las primeras placas son de la flota propia
PLACAS = ['F7J-011', 'B2K-334', 'C9M-120', 'D4P-778', 'E1R-905', 'G6T-246', 'H3V-581', 'J8W-017',
          'K5X-660', 'M2Y-439', 'N7Z-892', 'P1A-305']
CONDUCTORES = [('41235678', 'QUISPE', 'MAMANI', 'JUAN'), ('42345671', 'FLORES', 'RAMOS', 'PEDRO'),
               ('43456712', 'GARCÍA', 'TORRES', 'LUIS'), ('44567123', 'HUAMÁN', 'CHÁVEZ', 'ROSA'),
               ('45671234', 'MENDOZA', 'ROJAS', 'CARLOS'), ('46712345', 'VARGAS', 'SALAZAR', 'MIGUEL'),
               ('47123456', 'CASTILLO', 'DÍAZ', 'JORGE'), ('48234567', 'RÍOS', 'PAREDES', 'ANA'),
               ('49345678', 'LUNA', 'CÁCERES', 'VÍCTOR')]
CLIENTES = [('20100047218', 'Supermercados Peruanos S.A.'), ('20331066703', 'Distribuidora Lima Norte S.A.C.'),
            ('20508565934', 'Hipermercados Tottus S.A.'), ('20601234567', 'Comercial Andina E.I.R.L.')]
PROVEEDORES = [('20512345678', 'Químicos del Pacífico S.A.'), ('20498765432', 'Envases Plásticos S.A.C.')]
MOTIVOS = {'CLIENTE': ['Recojo de pedido', 'Recojo de devolución'], 'PROVEEDOR': ['Entrega de insumos', 'Entrega de materiales'],
           'INTERNO': ['Traslado entre plantas', 'Mantenimiento', 'Entrega de pedido a cliente']}
HORAS = [6, 7, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 12, 13, 14, 14, 15, 15, 16, 17, 18, 21, 22, 23]
OBSERVACIONES_DEMORA = ['Espera de documentos', 'Esperó turno de muelle', 'Carga incompleta, se completó en planta',
                        'Revisión de calidad', 'Falla mecánica, esperó grúa']


def crear_usuarios_prueba():
    for usuario, nombre, apellido, grupos, staff, cds in USUARIOS_PRUEBA:
        user, creado = User.objects.get_or_create(username=usuario, defaults={
            'first_name': nombre, 'last_name': apellido, 'is_staff': staff})
        if not creado:
            continue
        user.set_password(CLAVE_DEMO)
        user.save()
        for nombre_grupo in grupos:
            user.groups.add(Group.objects.get_or_create(name=nombre_grupo)[0])
        for codigo in cds:
            UsuarioCD.objects.create(usuario=user, cd=CentroDistribucion.objects.get(codigo=codigo))
        print(f"Usuario de prueba «{usuario}» creado ({', '.join(grupos)}).")


def borrar():
    """Elimina todo lo de demostración: lo creado por `demo_accesos` y lo que los usuarios de prueba
    hayan registrado en sus pruebas (a mano o por carga masiva), y luego esos usuarios."""
    from accesos.models import Incidencia, Jornada, RegistroAuditoria
    nombres = [USUARIO_DEMO] + [u[0] for u in USUARIOS_PRUEBA]
    usuarios = User.objects.filter(username__in=nombres)
    with transaction.atomic():
        transportes = Transporte.objects.filter(creado_por__in=usuarios)
        conductores = Conductor.objects.filter(creado_por__in=usuarios)
        programaciones = Programacion.objects.filter(
            Q(creado_por__in=usuarios) | Q(transporte__in=transportes) | Q(conductor__in=conductores))
        tramos = Tramo.objects.filter(
            Q(vigilante_entrada__in=usuarios) | Q(vigilante_salida__in=usuarios) | Q(programacion__in=programaciones)
            | Q(transporte__in=transportes) | Q(conductor__in=conductores))
        jornadas_demo = Jornada.objects.filter(Q(transporte__in=transportes) | Q(conductor__in=conductores))
        incidencias = Incidencia.objects.filter(Q(transporte__in=transportes) | Q(registrada_por__in=usuarios))
        tramos = tramos | Tramo.objects.filter(jornada__in=jornadas_demo)
        print(f"Eliminando {tramos.count()} tramos, {programaciones.count()} programaciones, "
              f"{jornadas_demo.count()} jornadas, {incidencias.count()} incidencias, "
              f"{transportes.count()} transportes y {conductores.count()} conductores de demostración…")
        tramos.delete()
        programaciones.delete()
        Programacion.objects.filter(incidencia__in=incidencias).update(incidencia=None)
        jornadas_demo.delete()
        Jornada.objects.filter(incidencia__in=incidencias).update(incidencia=None)
        incidencias.delete()
        transportes.delete()
        conductores.delete()
        Incidencia.objects.filter(cerrada_por__in=usuarios).update(cerrada_por=None)
        # Referencias de los usuarios de prueba en registros que no son de demostración.
        for modelo in (Transporte, Conductor, Programacion):
            modelo.objects.filter(modificado_por__in=usuarios).update(modificado_por=None)
        Programacion.objects.filter(cancelado_por__in=usuarios).update(cancelado_por=None)
        RegistroAuditoria.objects.filter(usuario__in=usuarios).delete()
        usuarios.delete()
    print("Listo.")


def _tramo(programacion, entrada, minutos, ahora, usuario, abiertos):
    """Crea el tramo de una programación. Si la salida cae después de «ahora», el vehículo sigue dentro."""
    transporte = programacion.transporte
    sigue_dentro = entrada + timedelta(minutes=minutos) > ahora
    if sigue_dentro and transporte.pk in abiertos:
        return None  # una placa no puede tener dos tramos abiertos
    salida = None if sigue_dentro else entrada + timedelta(minutes=minutos)
    tramo = Tramo.objects.create(
        programacion=programacion, transporte=transporte, cd=programacion.cd, conductor=programacion.conductor,
        entrada_en=entrada, fecha_local_entrada=entrada.date(), hora_local_entrada=entrada.time(),
        km_entrada=random.randint(50_000, 250_000), obs_entrada='Ninguna', vigilante_entrada=usuario,
        salida_en=salida, fecha_local_salida=salida.date() if salida else None,
        obs_salida=('Ninguna' if minutos <= 150 else random.choice(OBSERVACIONES_DEMORA)) if salida else '',
        vigilante_salida=usuario if salida else None,
        abierto=True if sigue_dentro else None, minutos_permanencia=None if sigue_dentro else minutos,
        desviacion_llegada_min=round((entrada - tiempo.combinar(programacion.fecha, programacion.hora)).total_seconds() / 60),
        tamano_snapshot=transporte.tamano, tipo_snapshot=programacion.tipo)
    if sigue_dentro:
        abiertos.add(transporte.pk)
    Programacion.objects.filter(pk=programacion.pk).update(estado='EN_PLANTA' if sigue_dentro else 'CERRADA')
    return tramo


def _a_las(fecha, minutos):
    """Datetime de Lima a `minutos` desde la medianoche de `fecha`."""
    return tiempo.combinar(fecha, time(0, 0)) + timedelta(minutes=int(minutos))


def _tramo_jornada(clase, jornada, transporte, cd, conductor, entrada, salida, km, usuario, programacion=None,
                   **marcas):
    """Tramo de la flota propia (Pre-Ruta, visita programada o Post-Ruta). `salida` None = sigue dentro."""
    entrada_lima = tiempo.a_lima(entrada)
    return Tramo.objects.create(
        clase=clase, jornada=jornada, programacion=programacion, transporte=transporte, cd=cd, conductor=conductor,
        entrada_en=entrada, fecha_local_entrada=entrada_lima.date(), hora_local_entrada=entrada_lima.time().replace(microsecond=0),
        km_entrada=km, obs_entrada='Pre-Ruta automática' if clase == Tramo.Clase.PRE_RUTA else 'Ninguna',
        vigilante_entrada=None if clase == Tramo.Clase.PRE_RUTA else usuario,
        salida_en=salida, fecha_local_salida=tiempo.a_lima(salida).date() if salida else None,
        obs_salida='Ninguna' if salida else '', vigilante_salida=usuario if salida else None,
        abierto=None if salida else True,
        minutos_permanencia=int((salida - entrada).total_seconds() // 60) if salida else None,
        desviacion_llegada_min=(round((entrada - tiempo.combinar(programacion.fecha, programacion.hora)).total_seconds() / 60)
                                if programacion else None),
        tamano_snapshot=transporte.tamano, tipo_snapshot='INTERNO', **marcas)


def _cerrar_pernocta(tramo, momento):
    tramo.salida_en = momento
    tramo.fecha_local_salida = tiempo.a_lima(momento).date()
    tramo.obs_salida = 'Fin de la pernocta: inicio de la Pre-Ruta'
    tramo.abierto = None
    tramo.cierre_automatico = True
    tramo.minutos_permanencia = int((momento - tramo.entrada_en).total_seconds() // 60)
    tramo.save()


def flota_propia(internos, conductores, centros, usuario, ahora):
    """Jornadas de la flota propia, día por día, respetando «ahora» (el día de hoy queda a medio camino)."""
    hoy = ahora.date()
    tipo_averia = TipoIncidencia.objects.filter(activo=True).order_by('orden').first()
    km = {t.pk: random.randint(80_000, 200_000) for t in internos}
    pernocta = {}  # placa -> tramo de Post-Ruta abierto (duerme en el CD)
    creados = 0
    laborables = [hoy - timedelta(days=d) for d in range(SEMANAS * 7, -2, -1)
                  if reglas_jornada.es_laborable(hoy - timedelta(days=d))]
    pasados = [f for f in laborables if f < hoy]
    averia_resuelta = (internos[2], pasados[-20] if len(pasados) > 20 else None)
    averia_abierta = (internos[4], pasados[-1] if pasados else None)
    fuera_de_servicio = set()  # (placa, fecha) sin jornada por la avería resuelta
    if averia_resuelta[1]:
        i = pasados.index(averia_resuelta[1])
        fuera_de_servicio = {(averia_resuelta[0].pk, f) for f in pasados[i + 1:i + 3]}

    for fecha in laborables:
        for indice, transporte in enumerate(internos):
            if (transporte.pk, fecha) in fuera_de_servicio:
                continue
            if transporte == averia_abierta[0] and fecha == hoy:
                continue  # sigue no disponible: hoy no tiene rutas
            if fecha < hoy and random.random() < 0.05:
                continue  # día sin rutas
            cd = centros[indice % len(centros)]
            conductor = conductores[indice]
            horas = sorted(random.sample([9 * 60, 10 * 60, 11 * 60 + 30, 13 * 60, 14 * 60, 14 * 60 + 30],
                                         random.randint(1, 3)))
            rutas = [Programacion.objects.create(
                cd=cd, fecha=fecha, hora=time(h // 60, h % 60), tipo='INTERNO', transporte=transporte,
                conductor=conductor, documento='20144109458', razon_social='Daryza S.A.C.',
                motivo=random.choice(MOTIVOS['INTERNO']), observacion_despacho='Dato de demostración',
                creado_por=usuario) for h in horas]
            jornada = Jornada.objects.get(transporte=transporte, fecha=fecha)
            jornada.programada_tarde = False
            if fecha > hoy:
                jornada.save()
                continue

            # Pre-Ruta: placa 1 suele tardar en salir.
            inicio = _a_las(fecha, 6 * 60 + 30)
            salida_pre = inicio + timedelta(minutes=max(15, int(random.gauss(48, 12)) + (30 if indice == 1 and random.random() < .6 else 0)))
            # Post-Ruta: placa 3 suele volver fuera de horario; a veces alguna vuelve antes.
            retorno = int(random.gauss(16 * 60 + 6, 6))
            if indice == 3 and random.random() < .6:
                retorno += random.randint(25, 70)
            elif random.random() < .1:
                retorno = random.randint(14 * 60 + 40, 15 * 60 + 45)
            retorno = _a_las(fecha, retorno)
            averia_hoy = (transporte, fecha) in (averia_resuelta, averia_abierta)
            if averia_hoy:
                retorno = _a_las(fecha, 11 * 60 + 40)

            if inicio > ahora:
                jornada.save()
                continue
            anterior = pernocta.pop(transporte.pk, None)
            if salida_pre > ahora:  # hoy: todavía en su Pre-Ruta (durmió en el CD)
                if anterior:
                    pernocta[transporte.pk] = anterior
                jornada.save()
                continue
            if anterior:
                _cerrar_pernocta(anterior, inicio)
            _tramo_jornada(Tramo.Clase.PRE_RUTA, jornada, transporte, cd, conductor, inicio, salida_pre,
                           km[transporte.pk], usuario)
            creados += 1
            jornada.estado, jornada.inicio_real, jornada.primera_salida = Jornada.Estado.EN_RUTA, inicio, salida_pre

            for ruta in rutas:
                entrada = tiempo.combinar(fecha, ruta.hora) + timedelta(minutes=int(random.gauss(0, 12)))
                if entrada >= retorno or entrada > ahora or entrada <= salida_pre:
                    continue
                km[transporte.pk] += random.randint(20, 60)
                salida = entrada + timedelta(minutes=max(15, int(random.gauss(42, 12))))
                sigue = salida > ahora
                _tramo_jornada(Tramo.Clase.PROGRAMADO, jornada, transporte, cd, ruta.conductor, entrada,
                               None if sigue else salida, km[transporte.pk], usuario, programacion=ruta)
                Programacion.objects.filter(pk=ruta.pk).update(estado='EN_PLANTA' if sigue else 'CERRADA')
                creados += 1
                if sigue:
                    break

            if retorno <= ahora and not Tramo.objects.filter(transporte=transporte, abierto=True).exists():
                km[transporte.pk] += random.randint(30, 80)
                post = _tramo_jornada(Tramo.Clase.POST_RUTA, jornada, transporte, cd, conductor, retorno, None,
                                      km[transporte.pk], usuario)
                creados += 1
                pernocta[transporte.pk] = post
                jornada.estado, jornada.post_ruta_en = Jornada.Estado.CERRADA, retorno
                jornada.motivo_cierre = Jornada.MotivoCierre.NORMAL
                pendientes = Programacion.objects.filter(transporte=transporte, fecha=fecha, estado='PROGRAMADA')
                if averia_hoy:
                    incidencia = Incidencia.objects.create(
                        transporte=transporte, tipo=tipo_averia, descripcion='Falla de frenos (demostración)',
                        origen=Incidencia.Origen.PORTERIA, cd=cd, inicio=retorno, registrada_por=usuario)
                    if (transporte, fecha) == averia_resuelta:
                        incidencia.fin, incidencia.abierta = retorno + timedelta(days=2, hours=3), None
                        incidencia.cerrada_por, incidencia.observacion_cierre = usuario, 'Reparado en taller'
                        incidencia.save()
                    jornada.motivo_cierre, jornada.incidencia = Jornada.MotivoCierre.AVERIA, incidencia
                    for programacion in pendientes:
                        programacion.estado, programacion.incidencia = 'CANCELADA', incidencia
                        programacion.cancelado_por, programacion.cancelado_en = usuario, retorno
                        programacion.motivo_cancelacion = f"Avería del vehículo: {incidencia.codigo}."
                        programacion.save()
                else:
                    pendientes.update(no_atendida_por_cierre=True)
            jornada.save()
    return creados


def crear():
    if User.objects.filter(username=USUARIO_DEMO).exists():
        print("Los datos de demostración ya existen. Use --borrar para eliminarlos primero.")
        return
    random.seed(2026)
    ahora = tiempo.ahora()
    hoy = ahora.date()
    with transaction.atomic():
        usuario = User.objects.create_user(USUARIO_DEMO, password=None, first_name='Demo', last_name='Accesos')
        tamanos = list(TamanoVehiculo.objects.filter(activo=True))
        if not tamanos:
            tamanos = [TamanoVehiculo.objects.create(nombre=n, orden=i) for i, n in
                       enumerate(['Camión pequeño', 'Camión mediano', 'Camión grande', 'Tráiler'], start=1)]
        dni = TipoDocumentoIdentidad.objects.get(codigo='DNI')
        transportes = []
        for i, placa in enumerate(PLACAS):
            transporte = Transporte.objects.filter(placa=placa).first() or Transporte.objects.create(
                placa=placa, tamano=tamanos[i % len(tamanos)], es_interno=i < INTERNOS, creado_por=usuario)
            transportes.append(transporte)
        conductores = [Conductor.objects.filter(tipo_documento=dni, numero_documento=doc).first()
                       or Conductor.objects.create(tipo_documento=dni, numero_documento=doc, apellido_paterno=ap,
                                                   apellido_materno=am, nombres=nom, creado_por=usuario)
                       for doc, ap, am, nom in CONDUCTORES]
        # Licencias con vencimientos variados para ver la campanita: por vencer, recién vencida,
        # vencida hace tiempo (se desactiva sola), A-IV por vencer y vigentes.
        vencimientos = [(12, None), (-3, None), (-40, None), (400, 20), (700, None), (2, 300), (900, 500)]
        for conductor, (dias_lic, dias_esp) in zip(conductores, vencimientos):
            conductor.licencia_numero = f'Q{conductor.numero_documento}'
            conductor.licencia_categoria = 'A-IIIb' if dias_lic % 2 else 'A-IIIc'
            conductor.licencia_vencimiento = hoy + timedelta(days=dias_lic)
            if dias_esp is not None:
                conductor.licencia_especial_categoria = 'A-IV'
                conductor.licencia_especial_vencimiento = hoy + timedelta(days=dias_esp)
            conductor.save()
        centros =list(CentroDistribucion.objects.filter(activo=True))
        lento, conductor_lento = transportes[5], conductores[8]

        tramos = 0
        abiertos = set()
        for dias_atras in range(SEMANAS * 7, -2, -1):  # hasta mañana
            fecha = hoy - timedelta(days=dias_atras)
            if fecha.weekday() == 6:
                continue
            usadas = set()
            for _ in range(random.randint(10, 16)):
                hora, minuto = random.choice(HORAS), random.choice([0, 10, 15, 20, 30, 40, 45, 50])
                transporte = random.choice(transportes[INTERNOS:])  # la flota propia va aparte
                if (transporte.pk, hora, minuto) in usadas:
                    continue
                usadas.add((transporte.pk, hora, minuto))
                tipo = 'INTERNO' if transporte.es_interno else random.choice(['CLIENTE', 'CLIENTE', 'PROVEEDOR'])
                documento, razon = (('20144109458', 'Daryza S.A.C.') if tipo == 'INTERNO'
                                    else random.choice(CLIENTES if tipo == 'CLIENTE' else PROVEEDORES))
                cd = random.choice(centros)
                conductor = conductor_lento if random.random() < 0.12 else random.choice(conductores[:8])
                programacion = Programacion.objects.create(
                    cd=cd, fecha=fecha, hora=time(hora, minuto), tipo=tipo, transporte=transporte, conductor=conductor,
                    documento=documento, razon_social=razon, motivo=random.choice(MOTIVOS[tipo]),
                    observacion_despacho='Dato de demostración', creado_por=usuario)
                if random.random() < 0.08:
                    continue  # cita no atendida (o todavía pendiente)
                desvio = random.choice([0, 0, 0, 5, -5, 10, -10, 25, 40, -30, 60])
                entrada = tiempo.combinar(fecha, programacion.hora) + timedelta(minutes=desvio)
                if entrada > ahora:
                    continue  # aún no llega: queda pendiente
                # Permanencia con patrones: hora pico, CD Punta Negra, placa y conductor lentos, retraso,
                # y algún ingreso de madrugada que se queda varias horas.
                base = random.gauss(55, 18)
                base += 35 if 9 <= hora <= 11 else 0
                base += 25 if cd.codigo == 'PUNTA_NEGRA' else 0
                base += 90 if transporte == lento else 0
                base += 70 if conductor == conductor_lento else 0
                base += 30 if desvio > 15 else 0
                base += 200 if random.random() < 0.04 or hora <= 6 else 0
                base += 420 if hora >= 21 else 0  # ingreso de noche que se queda hasta el amanecer
                if _tramo(programacion, entrada, max(12, int(base)), ahora, usuario, abiertos):
                    tramos += 1
        tramos += flota_propia(transportes[:INTERNOS], conductores, centros, usuario, ahora)
    crear_usuarios_prueba()
    print(f"Listo: {tramos} tramos de demostración ({len(abiertos)} vehículos dentro ahora). "
          "Elimínelos con: python seed_demo_accesos.py --borrar")


def exigir_base_local():
    """Candado: los datos y usuarios de demostración solo se crean o borran en un MySQL local."""
    from django.conf import settings
    host = settings.DATABASES['default']['HOST']
    if host not in ('127.0.0.1', 'localhost', '::1') or os.getenv('RAILWAY_ENVIRONMENT'):
        sys.exit(f"Este script es solo para pruebas locales y la base configurada es {host}. No se hizo nada.")


if __name__ == '__main__':
    exigir_base_local()
    if '--borrar' in sys.argv:
        borrar()
    else:
        crear()
