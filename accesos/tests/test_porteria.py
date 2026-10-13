import threading
from datetime import date, time, timezone as dt_timezone
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from .. import servicios, tiempo
from ..models import (CentroDistribucion, ConfiguracionAccesos, Conductor, Programacion, RegistroAuditoria,
                      TamanoVehiculo, TipoDocumentoIdentidad, Tramo, Transporte, UsuarioCD)
from .test_programacion import HOY, MANANA, ProgramacionBase
from .utilidades import crear_usuario


class PorteriaBase(ProgramacionBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        UsuarioCD.objects.create(usuario=cls.vigilante, cd=cls.lurin)
        cls.config = ConfiguracionAccesos.obtener()

    def entrar_en(self, programacion, km=125430, obs='', confirmar=False, conductor_id=None):
        tramo, _ = servicios.registrar_entrada(
            transporte_id=programacion.transporte_id, programacion_id=programacion.pk, cd=programacion.cd, km=km,
            obs=obs, conductor_id=conductor_id, usuario=self.vigilante, ahora=tiempo.ahora(), config=self.config,
            confirmar_fuera_de_ventana=confirmar)
        return tramo

    def salir(self, tramo, obs=''):
        return servicios.registrar_salida(transporte_id=tramo.transporte_id, tramo_id=tramo.pk, cd=tramo.cd,
                                          obs=obs, usuario=self.vigilante, ahora=tiempo.ahora())


class EvaluacionTests(PorteriaBase):

    def evaluar(self, texto, cd=None):
        return servicios.evaluar_porteria(texto, cd or self.lurin, tiempo.ahora(), self.config)

    def test_placa_no_registrada_o_sin_programacion_no_ingresa(self):
        self.assertEqual(self.evaluar('ZZZ-999').accion, servicios.NO_PROGRAMADO)
        self.assertEqual(self.evaluar('F7J-011').accion, servicios.NO_PROGRAMADO)
        self.programar(10, 0, fecha=MANANA)  # mañana no cuenta
        self.assertEqual(self.evaluar('F7J-011').accion, servicios.NO_PROGRAMADO)

    def test_qr_y_placa_digitada_dan_lo_mismo(self):
        programacion = self.programar(9, 20)
        for texto in ('F7J011', 'f7j-011', ' F7J 011 '):
            resultado = self.evaluar(texto)
            self.assertEqual(resultado.accion, servicios.ENTRADA)
            self.assertEqual(resultado.sugerida.programacion, programacion)

    def test_sugiere_la_mas_cercana_dentro_de_la_ventana(self):
        self.programar(7, 30)
        cercana = self.programar(9, 35)
        self.programar(11, 0)
        resultado = self.evaluar('F7J-011')  # son las 09:00
        self.assertEqual(resultado.sugerida.programacion, cercana)
        self.assertEqual(len(resultado.candidatas), 3)

    def test_sin_sugerida_si_ninguna_esta_en_la_ventana(self):
        self.programar(14, 0)
        resultado = self.evaluar('F7J-011')
        self.assertEqual(resultado.accion, servicios.ENTRADA)
        self.assertIsNone(resultado.sugerida)

    def test_transporte_inactivo_no_ingresa(self):
        self.programar(9, 0)
        Transporte.objects.filter(pk=self.f7j.pk).update(activo=False)
        self.assertEqual(self.evaluar('F7J-011').accion, servicios.NO_PROGRAMADO)

    def test_programacion_cancelada_no_cuenta(self):
        programacion = self.programar(9, 30)
        servicios.cancelar(programacion, self.despacho, 'Prueba', tiempo.ahora())
        self.assertEqual(self.evaluar('F7J-011').accion, servicios.NO_PROGRAMADO)


class FlujoDelDiaTests(PorteriaBase):

    def test_ejemplo_aprobado_tres_tramos_de_la_misma_placa(self):
        """F7J-011: 08:10–08:45, 10:30–11:00 y 14:00–14:30, cada uno con su programación."""
        prg_a, prg_b, prg_c = self.programar(8, 10), self.programar(10, 30), self.programar(14, 0)
        recorrido = [(prg_a, (8, 10), (8, 45), 35), (prg_b, (10, 30), (11, 0), 30), (prg_c, (14, 0), (14, 30), 30)]
        for numero, (programacion, entrada, salida, minutos) in enumerate(recorrido, start=1):
            self.mover_reloj(HOY, *entrada)
            resultado = servicios.evaluar_porteria('F7J011', self.lurin, tiempo.ahora(), self.config)
            self.assertEqual(resultado.accion, servicios.ENTRADA)
            self.assertEqual(resultado.sugerida.programacion, programacion)
            tramo = self.entrar_en(programacion)
            programacion.refresh_from_db()
            self.assertEqual(programacion.estado, Programacion.Estado.EN_PLANTA)

            self.mover_reloj(HOY, *salida)
            resultado = servicios.evaluar_porteria('F7J011', self.lurin, tiempo.ahora(), self.config)
            self.assertEqual(resultado.accion, servicios.SALIDA)
            tramo = self.salir(tramo)
            self.assertEqual(tramo.minutos_permanencia, minutos)
            self.assertEqual(servicios.numero_tramo_del_dia(tramo), numero)
            programacion.refresh_from_db()
            self.assertEqual(programacion.estado, Programacion.Estado.CERRADA)  # entrada + salida = cerrada
        self.assertEqual(Tramo.objects.filter(transporte=self.f7j, vigente=True).count(), 3)

    def test_ya_atendida_no_vuelve_a_ofrecerse(self):
        programacion = self.programar(9, 0)
        tramo = self.entrar_en(programacion)
        self.mover_reloj(HOY, 9, 30)
        self.salir(tramo)
        self.mover_reloj(HOY, 9, 40)
        resultado = servicios.evaluar_porteria('F7J011', self.lurin, tiempo.ahora(), self.config)
        self.assertEqual(resultado.accion, servicios.NO_PROGRAMADO)

    def test_fuera_de_ventana_exige_confirmacion(self):
        lejana = self.programar(13, 0)
        with self.assertRaises(ValidationError) as error:
            self.entrar_en(lejana)
        self.assertEqual(error.exception.code, 'confirmar_fuera_de_ventana')
        tramo = self.entrar_en(lejana, confirmar=True)
        self.assertTrue(tramo.fuera_de_ventana)
        self.assertEqual(tramo.desviacion_llegada_min, -240)  # llegó 4 horas antes

    def test_kilometraje_menor_al_anterior_avisa_sin_bloquear(self):
        primera, segunda = self.programar(9, 0), self.programar(9, 30)
        tramo = self.entrar_en(primera, km=125430)
        self.mover_reloj(HOY, 9, 20)
        self.salir(tramo)
        self.mover_reloj(HOY, 9, 30)
        tramo, avisos = servicios.registrar_entrada(
            transporte_id=self.f7j.pk, programacion_id=segunda.pk, cd=self.lurin, km=125000, obs='',
            conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora(), config=self.config)
        self.assertTrue(tramo.km_inconsistente)
        self.assertEqual(len(avisos), 1)

    def test_conductor_distinto_al_programado(self):
        otro = Conductor.objects.create(tipo_documento=self.dni, numero_documento='99887766', apellido_paterno='LUNA',
                                        nombres='CARLOS', creado_por=self.admin_accesos)
        tramo = self.entrar_en(self.programar(9, 0), conductor_id=otro.pk)
        self.assertEqual(tramo.conductor, otro)

    def test_observaciones_por_defecto_ninguna(self):
        tramo = self.entrar_en(self.programar(9, 0))
        self.assertEqual(tramo.obs_entrada, 'Ninguna')
        self.mover_reloj(HOY, 9, 30)
        self.assertEqual(self.salir(tramo).obs_salida, 'Ninguna')

    def test_permanencia_entre_dias(self):
        # Visita de un camión externo (los propios con ruta INTERNO tienen jornada: ver test_jornada.py).
        programacion = self.programar(23, 0, transporte=self.externo)
        self.mover_reloj(HOY, 23, 0)
        tramo = self.entrar_en(programacion)
        self.mover_reloj(MANANA, 7, 0)
        resultado = servicios.evaluar_porteria('ABC123', self.lurin, tiempo.ahora(), self.config)
        self.assertEqual(resultado.accion, servicios.SALIDA)  # no se confunde con una nueva entrada
        tramo = self.salir(tramo)
        self.assertTrue(tramo.entre_dias)
        self.assertEqual(tramo.minutos_permanencia, 480)
        self.assertTrue(servicios.es_prolongada(tramo, tiempo.ahora(), self.config))

    def test_vehiculo_dentro_de_otro_cd(self):
        tramo = self.entrar_en(self.programar(9, 0))
        self.programar(9, 10, cd=self.punta_negra)
        resultado = servicios.evaluar_porteria('F7J011', self.punta_negra, tiempo.ahora(), self.config)
        self.assertEqual(resultado.accion, servicios.OTRO_CD)
        with self.assertRaises(ValidationError):
            servicios.registrar_salida(transporte_id=self.f7j.pk, tramo_id=tramo.pk, cd=self.punta_negra, obs='',
                                       usuario=self.vigilante, ahora=tiempo.ahora())

    def test_segunda_entrada_con_tramo_abierto_se_rechaza(self):
        primera, segunda = self.programar(9, 0), self.programar(9, 20)
        self.entrar_en(primera)
        with self.assertRaises(ValidationError):
            self.entrar_en(segunda)

    def test_programacion_con_tramo_no_se_edita_ni_se_cancela(self):
        programacion = self.programar(10, 0)
        self.entrar_en(programacion)
        programacion.refresh_from_db()
        self.assertIsNotNone(servicios.motivo_no_editable(programacion, tiempo.ahora()))
        self.assertIsNotNone(servicios.motivo_no_cancelable(programacion, self.admin_accesos, tiempo.ahora()))

    def test_entrada_y_salida_quedan_auditadas_con_cd(self):
        tramo = self.entrar_en(self.programar(9, 0))
        self.mover_reloj(HOY, 9, 45)
        self.salir(tramo, obs='Carga completa')
        entrada = RegistroAuditoria.objects.get(entidad='Tramo', objeto_id=tramo.pk, accion='ENTRADA')
        salida = RegistroAuditoria.objects.get(entidad='Tramo', objeto_id=tramo.pk, accion='SALIDA')
        self.assertEqual((entrada.usuario, entrada.cd), (self.vigilante, self.lurin))
        self.assertEqual(salida.datos_despues['obs_salida'], 'Carga completa')
        self.assertEqual(salida.datos_despues['minutos_permanencia'], 45)


class VistasDePorteriaTests(PorteriaBase):

    def test_solo_vigilancia_usa_porteria(self):
        for usuario in (self.admin_accesos, self.despacho, self.consulta, self.superusuario):
            self.entrar(usuario)
            self.assertEqual(self.client.get(reverse('accesos:porteria')).status_code, 403, usuario.username)

    def test_cd_unico_se_asigna_solo_y_sin_cd_pide_elegir(self):
        self.entrar(self.vigilante)
        self.assertEqual(self.client.get(reverse('accesos:porteria')).status_code, 200)
        sin_cd = crear_usuario('vigilante2', 'Accesos - Vigilancia')
        self.entrar(sin_cd)
        self.assertRedirects(self.client.get(reverse('accesos:porteria')), reverse('accesos:porteria_cd'))

    def test_con_dos_cd_elige_y_no_puede_usar_uno_no_autorizado(self):
        dos = crear_usuario('vigilante3', 'Accesos - Vigilancia')
        UsuarioCD.objects.create(usuario=dos, cd=self.lurin)
        UsuarioCD.objects.create(usuario=dos, cd=self.punta_negra)
        self.entrar(dos)
        self.assertRedirects(self.client.get(reverse('accesos:porteria')), reverse('accesos:porteria_cd'))
        self.client.post(reverse('accesos:porteria_cd'), {'cd': self.punta_negra.pk})
        self.assertContains(self.client.get(reverse('accesos:porteria')), 'CD Punta Negra')
        self.entrar(self.vigilante)
        self.client.post(reverse('accesos:porteria_cd'), {'cd': self.punta_negra.pk})
        self.assertContains(self.client.get(reverse('accesos:porteria')), 'CD Lurín')

    def test_flujo_completo_desde_la_tablet(self):
        programacion = self.programar(9, 10)
        self.entrar(self.vigilante)
        pagina = self.client.get(reverse('accesos:porteria'), {'placa': 'f7j011'})
        self.assertContains(pagina, 'Registrar entrada')
        self.assertContains(pagina, 'Sugerida')
        respuesta = self.client.post(reverse('accesos:porteria_entrada'), {
            'transporte': self.f7j.pk, 'programacion': programacion.pk, 'km': 125430, 'obs': '', 'placa': 'f7j011'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('registrado=ENTRADA', respuesta['Location'])
        tramo = Tramo.objects.get()
        self.assertEqual(tramo.vigilante_entrada, self.vigilante)

        self.mover_reloj(HOY, 10, 0)
        pagina = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J-011'})
        self.assertContains(pagina, 'Registrar salida')
        self.client.post(reverse('accesos:porteria_salida'),
                         {'transporte': self.f7j.pk, 'tramo': tramo.pk, 'obs': 'OK', 'placa': 'F7J-011'})
        tramo.refresh_from_db()
        self.assertEqual(tramo.minutos_permanencia, 60)  # entró a las 09:00 (hora simulada), salió a las 10:00
        self.assertEqual(self.client.get(reverse('accesos:porteria_dentro')).status_code, 200)
        self.assertContains(self.client.get(reverse('accesos:porteria_programaciones')), 'Cerrada')

    def test_no_programado_muestra_bloqueo(self):
        self.entrar(self.vigilante)
        self.assertContains(self.client.get(reverse('accesos:porteria'), {'placa': 'XXX-000'}), 'Vehículo no programado')

    def test_entrada_sin_kilometraje_no_se_registra(self):
        programacion = self.programar(9, 0)
        self.entrar(self.vigilante)
        self.client.post(reverse('accesos:porteria_entrada'),
                         {'transporte': self.f7j.pk, 'programacion': programacion.pk, 'km': '', 'placa': 'F7J011'})
        self.assertFalse(Tramo.objects.exists())


@skipUnlessDBFeature('has_select_for_update')
class ConcurrenciaTests(TransactionTestCase):
    """Dos tablets registran la entrada de la misma placa casi a la vez: solo una debe quedar."""

    def setUp(self):
        reloj = mock.patch('accesos.tiempo.timezone.now',
                           return_value=tiempo.combinar(HOY, time(9, 0)).astimezone(dt_timezone.utc))
        reloj.start()
        self.addCleanup(reloj.stop)
        usuario = crear_usuario('tablet', 'Accesos - Vigilancia')
        tamano = TamanoVehiculo.objects.create(nombre='Grande')
        self.cd = CentroDistribucion.objects.create(codigo='TEST', nombre='Test', capacidad_simultanea=5)
        dni, _ = TipoDocumentoIdentidad.objects.get_or_create(
            codigo='DNI', defaults={'nombre': 'DNI', 'longitud_min': 8, 'longitud_max': 8})
        conductor = Conductor.objects.create(tipo_documento=dni, numero_documento='12345678', apellido_paterno='A',
                                             nombres='B', creado_por=usuario)
        self.transporte = Transporte.objects.create(placa='CON-001', tamano=tamano, creado_por=usuario)
        self.programaciones = [
            Programacion.objects.create(cd=self.cd, fecha=HOY, hora=time(9, m), tipo='CLIENTE', transporte=self.transporte,
                                        conductor=conductor, documento='20144109458', razon_social='X', motivo='Y',
                                        creado_por=usuario)
            for m in (0, 5)]
        self.usuario = usuario
        self.config = ConfiguracionAccesos.obtener()

    def test_dos_tablets_a_la_vez_solo_una_entrada(self):
        barrera = threading.Barrier(2)
        resultados = []

        def registrar(programacion):
            try:
                barrera.wait()
                servicios.registrar_entrada(
                    transporte_id=self.transporte.pk, programacion_id=programacion.pk, cd=self.cd, km=100, obs='',
                    conductor_id=None, usuario=self.usuario, ahora=tiempo.ahora(), config=self.config)
                resultados.append('ok')
            except ValidationError:
                resultados.append('rechazada')
            finally:
                connection.close()

        hilos = [threading.Thread(target=registrar, args=(p,)) for p in self.programaciones]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)
        self.assertEqual(sorted(resultados), ['ok', 'rechazada'])
        self.assertEqual(Tramo.objects.filter(transporte=self.transporte, abierto=True).count(), 1)
