from datetime import date, datetime, time, timezone as dt_timezone
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .. import servicios, tiempo
from ..forms import ProgramacionForm
from ..models import (CentroDistribucion, ConfiguracionAccesos, Conductor, Programacion, RegistroAuditoria,
                      Transporte, ruc_valido)
from .utilidades import UsuariosMixin

# Hora fija de las pruebas: miércoles 07/10/2026 09:00 en Lima (14:00 UTC).
AHORA_UTC = datetime(2026, 10, 7, 14, 0, tzinfo=dt_timezone.utc)
HOY = date(2026, 10, 7)
MANANA = date(2026, 10, 8)
RUC_DARYZA = '20144109458'


class ProgramacionBase(UsuariosMixin, TestCase):

    def setUp(self):
        reloj = mock.patch('accesos.tiempo.timezone.now', return_value=AHORA_UTC)
        self.reloj = reloj.start()
        self.addCleanup(reloj.stop)

    def mover_reloj(self, dia, hora, minuto=0):
        """Fija «ahora» en una fecha y hora de Lima."""
        self.reloj.return_value = tiempo.combinar(dia, time(hora, minuto)).astimezone(dt_timezone.utc)

    def programar(self, hora, minuto=0, transporte=None, cd=None, fecha=HOY, tipo='CLIENTE'):
        """Crea una programación directamente (sin la regla de no programar en el pasado)."""
        return Programacion.objects.create(
            cd=cd or self.lurin, fecha=fecha, hora=time(hora, minuto), tipo=tipo,
            transporte=transporte or self.f7j, conductor=self.conductor, documento=RUC_DARYZA,
            razon_social='Cliente XYZ', motivo='Recojo de pedido', creado_por=self.despacho)

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lurin = CentroDistribucion.objects.get(codigo='LURIN')
        cls.punta_negra = CentroDistribucion.objects.get(codigo='PUNTA_NEGRA')
        cls.f7j = Transporte.objects.create(placa='F7J-011', tamano=cls.tamano, es_interno=True,
                                            creado_por=cls.admin_accesos)
        cls.externo = Transporte.objects.create(placa='ABC-123', tamano=cls.tamano, creado_por=cls.admin_accesos)
        cls.conductor = Conductor.objects.create(tipo_documento=cls.dni, numero_documento='12345678',
                                                 apellido_paterno='PÉREZ', nombres='JUAN', creado_por=cls.admin_accesos)

    def datos(self, **cambios):
        datos = {'cd': self.lurin.pk, 'fecha': MANANA.isoformat(), 'hora': '08:10', 'tipo': 'CLIENTE',
                 'transporte': self.externo.pk, 'conductor': self.conductor.pk, 'documento': RUC_DARYZA,
                 'razon_social': 'Cliente XYZ', 'motivo': 'Recojo de pedido', 'observacion_despacho': ''}
        datos.update(cambios)
        return datos

    def crear(self, **cambios):
        form = ProgramacionForm(data=self.datos(**cambios), ahora=tiempo.ahora())
        self.assertTrue(form.is_valid(), form.errors)
        programacion = form.save(commit=False)
        programacion.creado_por = self.despacho
        programacion.save()
        return programacion


class ReglasDeFormularioTests(ProgramacionBase):

    def test_ruc_valido(self):
        self.assertTrue(ruc_valido(RUC_DARYZA))
        self.assertFalse(ruc_valido('20144109457'))
        self.assertFalse(ruc_valido('2014410945'))

    def test_r1_no_se_programa_en_el_pasado(self):
        pasada = ProgramacionForm(data=self.datos(fecha=HOY.isoformat(), hora='08:59'), ahora=tiempo.ahora())
        self.assertFalse(pasada.is_valid())
        self.assertIn('hora', pasada.errors)
        futura = ProgramacionForm(data=self.datos(fecha=HOY.isoformat(), hora='09:01'), ahora=tiempo.ahora())
        self.assertTrue(futura.is_valid(), futura.errors)

    def test_r7_interno_usa_ruc_y_razon_social_de_la_empresa(self):
        programacion = self.crear(tipo='INTERNO', transporte=self.f7j.pk, documento='', razon_social='')
        self.assertEqual(programacion.documento, RUC_DARYZA)
        self.assertEqual(programacion.razon_social, 'Daryza S.A.C.')

    def test_r8_cliente_exige_documento_valido(self):
        for documento in ('', '2014410945', '20144109457', '1234567X'):
            form = ProgramacionForm(data=self.datos(documento=documento), ahora=tiempo.ahora())
            self.assertFalse(form.is_valid(), documento)
            self.assertIn('documento', form.errors)
        self.assertTrue(ProgramacionForm(data=self.datos(documento='12345678'), ahora=tiempo.ahora()).is_valid())

    def test_r9_transporte_inactivo_no_seleccionable(self):
        Transporte.objects.filter(pk=self.externo.pk).update(activo=False)
        form = ProgramacionForm(data=self.datos(), ahora=tiempo.ahora())
        self.assertFalse(form.is_valid())
        self.assertIn('transporte', form.errors)

    def test_misma_placa_mismo_dia_y_hora_bloqueada_pero_otro_horario_permitido(self):
        self.crear(hora='08:10')
        repetida = ProgramacionForm(data=self.datos(hora='08:10', cd=self.punta_negra.pk), ahora=tiempo.ahora())
        self.assertFalse(repetida.is_valid())
        self.assertIn('transporte', repetida.errors)
        # Ejemplo aprobado: la misma placa con 3 programaciones el mismo día.
        self.crear(hora='10:30')
        self.crear(hora='14:00')
        self.assertEqual(Programacion.objects.filter(transporte=self.externo, fecha=MANANA).count(), 3)

    def test_la_cancelacion_exige_motivo_en_la_base_de_datos(self):
        programacion = self.crear()
        programacion.estado = Programacion.Estado.CANCELADA
        with self.assertRaises(IntegrityError), transaction.atomic():
            programacion.save()

    def test_codigo_visible(self):
        programacion = self.crear()
        self.assertEqual(programacion.codigo, f"PRG-{programacion.pk:06d}")


class EdicionYCancelacionTests(ProgramacionBase):

    def test_r2_editable_solo_antes_de_la_hora(self):
        manana = self.crear()
        self.assertIsNone(servicios.motivo_no_editable(manana, tiempo.ahora()))
        hoy_temprano = Programacion.objects.create(
            cd=self.lurin, fecha=HOY, hora=time(8, 0), tipo='CLIENTE', transporte=self.f7j, conductor=self.conductor,
            documento=RUC_DARYZA, razon_social='X', motivo='Y', creado_por=self.despacho)
        self.assertIsNotNone(servicios.motivo_no_editable(hoy_temprano, tiempo.ahora()))

        self.entrar(self.despacho)
        respuesta = self.client.get(reverse('accesos:programacion_editar', args=[hoy_temprano.pk]))
        self.assertRedirects(respuesta, reverse('accesos:programacion_detalle', args=[hoy_temprano.pk]))

    def test_edicion_auditada(self):
        programacion = self.crear()
        self.entrar(self.despacho)
        self.client.post(reverse('accesos:programacion_editar', args=[programacion.pk]), self.datos(hora='09:30'))
        registro = RegistroAuditoria.objects.get(entidad='Programacion', objeto_id=programacion.pk, accion='MODIFICAR')
        self.assertEqual(registro.datos_antes['hora'], '08:10:00')
        self.assertEqual(registro.datos_despues['hora'], '09:30:00')

    def test_r3_despacho_cancela_antes_de_la_hora_con_motivo(self):
        programacion = self.crear()
        self.entrar(self.despacho)
        url = reverse('accesos:programacion_cancelar', args=[programacion.pk])
        self.client.post(url, {'motivo': ''})
        programacion.refresh_from_db()
        self.assertFalse(programacion.esta_cancelada)

        self.client.post(url, {'motivo': 'El cliente reprogramó'})
        programacion.refresh_from_db()
        self.assertTrue(programacion.esta_cancelada)
        self.assertEqual(programacion.cancelado_por, self.despacho)
        registro = RegistroAuditoria.objects.get(entidad='Programacion', objeto_id=programacion.pk, accion='CANCELAR')
        self.assertEqual(registro.motivo, 'El cliente reprogramó')
        self.assertIsNotNone(servicios.motivo_no_editable(programacion, tiempo.ahora()))

    def test_r3_pasada_la_hora_solo_cancela_el_administrador(self):
        pasada = Programacion.objects.create(
            cd=self.lurin, fecha=HOY, hora=time(8, 0), tipo='CLIENTE', transporte=self.f7j, conductor=self.conductor,
            documento=RUC_DARYZA, razon_social='X', motivo='Y', creado_por=self.despacho)
        url = reverse('accesos:programacion_cancelar', args=[pasada.pk])
        self.entrar(self.despacho)
        self.client.post(url, {'motivo': 'No llegó'})
        pasada.refresh_from_db()
        self.assertFalse(pasada.esta_cancelada)
        self.entrar(self.admin_accesos)
        self.client.post(url, {'motivo': 'No llegó'})
        pasada.refresh_from_db()
        self.assertTrue(pasada.esta_cancelada)


class EstadoYAvisosTests(ProgramacionBase):

    def test_r4_estado_visible(self):
        programacion = self.crear()
        self.assertEqual(servicios.estado_visible(programacion, HOY), servicios.PENDIENTE)
        self.assertEqual(servicios.estado_visible(programacion, date(2026, 10, 9)), servicios.NO_ATENDIDA)
        servicios.cancelar(programacion, self.despacho, 'Prueba', tiempo.ahora())
        self.assertEqual(servicios.estado_visible(programacion, HOY), servicios.CANCELADA)

    def test_r5_aviso_de_capacidad_con_duracion_estimada(self):
        config = ConfiguracionAccesos.obtener()
        placas = []
        for i in range(6):
            placas.append(Transporte.objects.create(placa=f'CAP-00{i}', tamano=self.tamano, creado_por=self.admin_accesos))
        for i, hora in enumerate(['09:00', '09:10', '09:20', '09:30', '09:40']):
            programacion = self.crear(transporte=placas[i].pk, hora=hora)
        self.assertEqual(servicios.avisos(programacion, config), [])  # 5 de capacidad 5
        sexta = self.crear(transporte=placas[5].pk, hora='09:50')
        self.assertTrue(any('Capacidad' in a for a in servicios.avisos(sexta, config)))
        lejana = self.crear(transporte=self.externo.pk, hora='11:00')
        self.assertEqual(servicios.avisos(lejana, config), [])

    def test_r6_misma_placa_cerca_en_otro_cd(self):
        config = ConfiguracionAccesos.obtener()
        self.crear(hora='09:00', cd=self.lurin.pk)
        otra = self.crear(hora='09:30', cd=self.punta_negra.pk)
        avisos = servicios.avisos(otra, config)
        self.assertTrue(any('también está programada' in a for a in avisos))

    def test_r7_bloquea_interno_con_transporte_externo(self):
        form = ProgramacionForm(data=self.datos(tipo='INTERNO', documento='', razon_social=''), ahora=tiempo.ahora())
        self.assertFalse(form.is_valid())
        self.assertIn('transporte propio', ' '.join(form.errors['transporte']))
        interna = ProgramacionForm(data=self.datos(tipo='INTERNO', transporte=self.f7j.pk, documento='',
                                                   razon_social=''), ahora=tiempo.ahora())
        self.assertTrue(interna.is_valid(), interna.errors)


class VistasYPermisosTests(ProgramacionBase):

    def test_permisos_de_programacion(self):
        programacion = self.crear()
        lectura = [reverse('accesos:programacion_agenda'), reverse('accesos:programacion_detalle', args=[programacion.pk])]
        escritura = [reverse('accesos:programacion_crear'), reverse('accesos:programacion_editar', args=[programacion.pk])]
        for usuario in (self.admin_accesos, self.despacho):
            self.entrar(usuario)
            for url in lectura + escritura:
                self.assertEqual(self.client.get(url).status_code, 200, (usuario.username, url))
        self.entrar(self.consulta)
        for url in lectura:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for url in escritura:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(reverse('accesos:programacion_cancelar', args=[programacion.pk]),
                                          {'motivo': 'x'}).status_code, 403)
        self.entrar(self.vigilante)
        self.assertEqual(self.client.get(lectura[0]).status_code, 403)

    def test_alta_desde_la_vista_audita_y_avisa(self):
        self.entrar(self.despacho)
        # Ruta INTERNO con salida a las 06:20 (turno día), antes del inicio de la Pre-Ruta (06:30): aviso de salida
        # anticipada. Hasta las 06:15 sería del turno noche del día anterior.
        respuesta = self.client.post(reverse('accesos:programacion_crear'),
                                     self.datos(tipo='INTERNO', transporte=self.f7j.pk, hora='06:20', documento='',
                                                razon_social=''), follow=True)
        programacion = Programacion.objects.get()
        self.assertEqual(respuesta.redirect_chain[-1][0], reverse('accesos:programacion_detalle', args=[programacion.pk]))
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='Programacion', accion='CREAR').exists())
        self.assertContains(respuesta, 'antes del inicio de la Pre-Ruta')

    def test_nueva_en_otro_horario_copia_los_datos(self):
        base = self.crear(motivo='Entrega de insumos')
        self.entrar(self.despacho)
        form = self.client.get(reverse('accesos:programacion_crear'), {'desde': base.pk}).context['form']
        self.assertEqual(form.initial['motivo'], 'Entrega de insumos')
        self.assertEqual(form.initial['transporte'], self.externo.pk)
        self.assertNotIn('hora', form.initial)

    def test_agenda_usa_el_dia_de_lima(self):
        # 23:30 del 07/10 en Lima = 04:30 del 08/10 en UTC.
        with mock.patch('accesos.tiempo.timezone.now', return_value=datetime(2026, 10, 8, 4, 30, tzinfo=dt_timezone.utc)):
            self.entrar(self.consulta)
            respuesta = self.client.get(reverse('accesos:programacion_agenda'))
        self.assertEqual(respuesta.context['fecha'], HOY)
        self.assertContains(respuesta, 'Miércoles 07/10/2026')

    def test_ruc_configurado_por_migracion(self):
        self.assertEqual(ConfiguracionAccesos.obtener().ruc_empresa, RUC_DARYZA)


class RegistroRapidoDesdeProgramacionTests(ProgramacionBase):
    """Despacho registra transporte y conductor sin salir del formulario ni entrar a Django Admin."""

    def test_el_formulario_ofrece_el_registro_rapido(self):
        self.entrar(self.despacho)
        pagina = self.client.get(reverse('accesos:programacion_crear'))
        self.assertContains(pagina, 'dialogo-transporte')
        self.assertContains(pagina, reverse('accesos:transporte_crear_rapido'))
        self.assertContains(pagina, reverse('accesos:conductor_crear_rapido'))

    def test_despacho_registra_transporte_y_lo_recibe_listo_para_seleccionar(self):
        self.entrar(self.despacho)
        respuesta = self.client.post(reverse('accesos:transporte_crear_rapido'),
                                     {'tr-placa': 'nue-777', 'tr-tamano': self.tamano.pk, 'tr-activo': 'on'})
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        transporte = Transporte.objects.get(placa_normalizada='NUE777')
        self.assertEqual(datos, {'ok': True, 'id': transporte.pk, 'etiqueta': 'NUE-777 · Camión grande'})
        self.assertEqual(transporte.creado_por, self.despacho)
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='Transporte', objeto_id=transporte.pk,
                                                         accion='CREAR', usuario=self.despacho).exists())
        # Y queda disponible de inmediato para la programación.
        form = ProgramacionForm(data=self.datos(transporte=transporte.pk), ahora=tiempo.ahora())
        self.assertTrue(form.is_valid(), form.errors)

    def test_registro_rapido_valida_igual_que_el_maestro(self):
        self.entrar(self.despacho)
        repetida = self.client.post(reverse('accesos:transporte_crear_rapido'),
                                    {'tr-placa': 'f7j 011', 'tr-tamano': self.tamano.pk, 'tr-activo': 'on'})
        self.assertEqual(repetida.status_code, 400)
        self.assertIn('placa', repetida.json()['errores'])
        dni_corto = self.client.post(reverse('accesos:conductor_crear_rapido'), {
            'co-tipo_documento': self.dni.pk, 'co-numero_documento': '123',
            'co-apellido_paterno': 'Ríos', 'co-nombres': 'Ana', 'co-activo': 'on'})
        self.assertEqual(dni_corto.status_code, 400)
        self.assertIn('numero_documento', dni_corto.json()['errores'])

    def test_despacho_registra_conductor(self):
        self.entrar(self.despacho)
        respuesta = self.client.post(reverse('accesos:conductor_crear_rapido'), {
            'co-tipo_documento': self.dni.pk, 'co-numero_documento': '44556677',
            'co-apellido_paterno': 'Ríos', 'co-apellido_materno': '', 'co-nombres': 'Ana', 'co-activo': 'on'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['etiqueta'], 'RÍOS, ANA · DNI 44556677')

    def test_permisos_del_registro_rapido(self):
        url = reverse('accesos:transporte_crear_rapido')
        datos = {'tr-placa': 'PER-001', 'tr-tamano': self.tamano.pk, 'tr-activo': 'on'}
        self.entrar(self.consulta)
        self.assertEqual(self.client.post(url, datos).status_code, 403)
        self.entrar(self.despacho)
        self.assertEqual(self.client.get(url).status_code, 405)
