"""
Pantallas de la jornada de la flota propia (F6.2): portería (inicio de jornada, Post-Ruta, avería,
salidas con motivo), disponibilidad de flota, campanita, agenda, tablero «Hoy», carga masiva y
conversión de la última visita en Post-Ruta.
"""
from datetime import datetime, time

from django.urls import reverse

from .. import jornadas, servicios, tiempo
from ..models import Incidencia, Jornada, Programacion, Tramo
from .test_carga_masiva import COLUMNAS_PROG, excel
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA, RUC_DARYZA

PORTERIA = reverse('accesos:porteria')


class PorteriaPantallasTests(JornadaBase):

    def setUp(self):
        super().setUp()
        self.entrar(self.vigilante)

    def escanear(self, **extra):
        return self.client.get(PORTERIA, {'placa': 'F7J-011', **extra})

    def test_dia_completo_por_pantalla(self):
        self.mover_reloj(HOY, 5, 0)
        once = self.interna(11, 0)
        self.interna(15, 0)

        self.mover_reloj(HOY, 7, 30)
        pagina = self.escanear()
        self.assertContains(pagina, 'Inicio de jornada')
        self.assertContains(pagina, 'Está saliendo')
        respuesta = self.client.post(reverse('accesos:porteria_inicio_jornada'),
                                     {'transporte': self.f7j.pk, 'sentido': 'sale', 'placa': 'F7J-011'})
        self.assertRedirects(respuesta, f"{PORTERIA}?registrado=PRE_RUTA&registrado_placa=F7J-011")
        self.assertContains(self.client.get(respuesta.url), 'SALIDA A RUTA registrada')

        self.mover_reloj(HOY, 11, 0)
        pagina = self.escanear()
        self.assertContains(pagina, 'Registrar entrada')
        self.assertContains(pagina, 'Fin de jornada (Post-Ruta)')
        self.client.post(reverse('accesos:porteria_entrada'),
                         {'transporte': self.f7j.pk, 'programacion': once.pk, 'km': 1000, 'placa': 'F7J-011'})
        self.mover_reloj(HOY, 11, 50)
        visita = Tramo.objects.get(programacion=once)
        self.client.post(reverse('accesos:porteria_salida'), {'transporte': self.f7j.pk, 'tramo': visita.pk})

        # Vuelve a las 16:30 sin atender la de 15:00: elige «Fin de jornada».
        self.mover_reloj(HOY, 16, 30)
        pagina = self.escanear(modo='post_ruta')
        self.assertContains(pagina, 'Fin de jornada · Post-Ruta')
        self.assertContains(pagina, 'fuera de horario')
        self.assertContains(pagina, '1 programación pendiente')
        respuesta = self.client.post(reverse('accesos:porteria_post_ruta'),
                                     {'transporte': self.f7j.pk, 'km': 1100, 'placa': 'F7J-011'})
        self.assertRedirects(respuesta, f"{PORTERIA}?registrado=POST_RUTA&registrado_placa=F7J-011")
        jornada = self.jornada()
        self.assertEqual(jornadas.calificacion_cierre(jornada), jornadas.FUERA_DE_HORARIO)
        self.assertEqual(jornadas.minutos_sobre_cierre(jornada), 30)

        # Dentro ahora: la pernocta aparece como flota propia, no como permanencia prolongada.
        self.mover_reloj(MANANA, 5, 0)
        self.entrar(self.vigilante)  # nuevo turno: la sesión de Vigilancia dura 12 h
        pagina = self.client.get(reverse('accesos:porteria_dentro'))
        self.assertContains(pagina, 'en el CD desde su Post-Ruta')
        self.assertNotContains(pagina, 'Más de 240 min')

        self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 15)
        pagina = self.escanear()
        self.assertContains(pagina, 'Salida a ruta · fin de la Pre-Ruta')
        post = Tramo.objects.get(transporte=self.f7j, abierto=True)
        respuesta = self.client.post(reverse('accesos:porteria_salida'), {'transporte': self.f7j.pk, 'tramo': post.pk})
        self.assertIn('registrado=PRE_RUTA', respuesta.url)
        self.assertEqual(self.jornada(MANANA).estado, Jornada.Estado.EN_RUTA)

    def test_ingreso_por_averia_exige_tipo_y_descripcion(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(14, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 12, 0)
        self.assertContains(self.escanear(modo='averia'), 'Ingresa por avería o falla')
        url = reverse('accesos:porteria_post_ruta')
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'km': 1000, 'averia': 'on'}, follow=True)
        self.assertContains(respuesta, 'Elija el tipo de avería')
        self.assertFalse(Incidencia.objects.exists())
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'km': 1000, 'averia': 'on',
                                           'averia_tipo': self.averia.pk, 'averia_descripcion': 'Llanta reventada',
                                           'placa': 'F7J-011'})
        self.assertIn('registrado=AVERIA', respuesta.url)
        self.assertTrue(Incidencia.objects.filter(transporte=self.f7j, abierta=True).exists())

    def test_inicio_de_jornada_desde_afuera_pide_km(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(10, 0)
        self.mover_reloj(HOY, 6, 45)
        url = reverse('accesos:porteria_inicio_jornada')
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'sentido': 'ingresa'}, follow=True)
        self.assertContains(respuesta, 'Ingrese el kilometraje')
        self.client.post(url, {'transporte': self.f7j.pk, 'sentido': 'ingresa', 'km': 5000})
        self.assertTrue(Tramo.objects.filter(transporte=self.f7j, clase=Tramo.Clase.PRE_RUTA, abierto=True).exists())

    def test_salida_despues_de_la_post_ruta_pide_motivo(self):
        self.dia_completo(HOY)
        self.mover_reloj(HOY, 18, 0)
        self.assertContains(self.escanear(), 'Motivo de la salida *')
        post = Tramo.objects.get(transporte=self.f7j, abierto=True)
        respuesta = self.client.post(reverse('accesos:porteria_salida'),
                                     {'transporte': self.f7j.pk, 'tramo': post.pk}, follow=True)
        self.assertContains(respuesta, 'Indique el motivo de la salida')
        self.assertTrue(Tramo.objects.filter(pk=post.pk, abierto=True).exists())

    def test_programaciones_de_porteria_muestran_la_jornada(self):
        self.interna(11, 0)
        self.assertContains(self.client.get(reverse('accesos:porteria_programaciones')), 'Flota propia · 1 jornada')


class DisponibilidadTests(JornadaBase):

    URL = reverse('accesos:flota_disponibilidad')

    def test_permisos(self):
        for usuario, codigo, gestiona in ((self.admin_accesos, 200, True), (self.despacho, 200, True),
                                          (self.planeamiento, 200, True), (self.consulta, 200, False),
                                          (self.vigilante, 403, False), (self.comercial, 403, False)):
            with self.subTest(usuario=usuario.username):
                self.entrar(usuario)
                respuesta = self.client.get(self.URL)
                self.assertEqual(respuesta.status_code, codigo)
                if codigo == 200:
                    self.assertEqual('Marcar no disponible' in respuesta.content.decode(), gestiona)
        self.entrar(self.consulta)
        self.assertEqual(self.client.post(reverse('accesos:flota_registrar'), {}).status_code, 403)

    def test_registrar_y_habilitar(self):
        futura = self.interna(10, 0, fecha=MANANA)
        self.entrar(self.despacho)
        respuesta = self.client.post(reverse('accesos:flota_registrar'), {
            'transporte': self.f7j.pk, 'tipo': self.averia.pk, 'descripcion': 'Falla de motor'}, follow=True)
        self.assertContains(respuesta, 'queda NO DISPONIBLE')
        self.assertContains(respuesta, futura.codigo)  # programación afectada por reasignar
        incidencia = Incidencia.objects.get()
        self.assertContains(self.client.get(reverse('accesos:transportes_lista')), 'No disponible')
        # La campanita lo avisa.
        self.assertContains(self.client.get(reverse('accesos:inicio')), 'F7J-011 no disponible')
        respuesta = self.client.post(reverse('accesos:flota_habilitar', args=[incidencia.pk]),
                                     {'observacion': 'Reparado'}, follow=True)
        self.assertContains(respuesta, 'vuelve a estar disponible')
        self.assertContains(respuesta, 'Toda la flota está disponible')
        self.assertContains(respuesta, 'Reparado')  # en el historial


class PantallasDeControlTests(JornadaBase):

    def test_agenda_y_hoy_muestran_la_flota_propia(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(11, 0)
        self.mover_reloj(HOY, 6, 45)
        self.entrar(self.despacho)
        self.assertContains(self.client.get(reverse('accesos:programacion_agenda')), 'Flota propia · 1 jornada')
        pagina = self.client.get(reverse('accesos:tablero_hoy'))
        self.assertContains(pagina, 'Flota propia hoy')
        self.assertContains(pagina, 'Por llegar')  # sin registro dentro del CD

    def test_dia_no_laborable_en_la_agenda(self):
        self.entrar(self.despacho)
        pagina = self.client.get(reverse('accesos:programacion_agenda'), {'fecha': '2026-10-11'})
        self.assertContains(pagina, 'Día no laborable (Domingo)')

    def test_detalle_de_programacion_con_jornada_y_no_atendida(self):
        self.mover_reloj(HOY, 5, 0)
        quince = self.interna(15, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 12, 0)
        self.post_ruta()
        self.entrar(self.despacho)
        pagina = self.client.get(reverse('accesos:programacion_detalle', args=[quince.pk]))
        self.assertContains(pagina, 'No atendida por cierre de jornada')
        self.assertContains(pagina, 'Jornada de la flota propia')

    def test_aviso_de_capacidad_cuenta_la_flota_propia(self):
        self.lurin.capacidad_simultanea = 1
        self.lurin.save()
        self.interna(10, 0, fecha=MANANA)
        visita = self.programar(7, 0, transporte=self.externo, fecha=MANANA)
        avisos = servicios.avisos(visita, self.config)
        self.assertTrue(any('unidades propias en Pre-Ruta o pernocta' in a for a in avisos))

    def test_convertir_en_post_ruta_desde_el_detalle(self):
        self.mover_reloj(HOY, 5, 0)
        programacion = self.interna(15, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 15, 0)
        visita = self.entrar_en(programacion)
        self.mover_reloj(HOY, 21, 0)
        self.entrar(self.admin_accesos)
        url = reverse('accesos:tramo_detalle', args=[visita.pk])
        self.assertContains(self.client.get(url), 'Convertir en Post-Ruta')
        self.client.post(reverse('accesos:tramo_convertir_post_ruta', args=[visita.pk]),
                         {'convertir-fin_atencion': '2026-10-07T15:50', 'convertir-motivo': 'Se quedó a dormir'})
        self.assertEqual(self.jornada().estado, Jornada.Estado.CERRADA)
        self.assertTrue(Tramo.objects.filter(transporte=self.f7j, clase=Tramo.Clase.POST_RUTA, abierto=True).exists())


class CargaMasivaPreRutaTests(JornadaBase):

    def test_la_vista_previa_informa_la_pre_ruta(self):
        self.entrar(self.despacho)
        fila = {'cd': 'LURIN', 'fecha': datetime(2026, 10, 8), 'hora': time(10, 0), 'tipo': 'INTERNO',
                'placa': 'F7J-011', 'conductor_documento': 12345678, 'ruc_dni': '', 'razon_social': '',
                'motivo': 'Ruta', 'observacion': ''}
        otra = dict(fila, hora=time(14, 0))
        archivo = excel(COLUMNAS_PROG, [[fila[c] for c in COLUMNAS_PROG], [otra[c] for c in COLUMNAS_PROG]])
        url = reverse('accesos:carga_masiva', args=['programaciones'])
        respuesta = self.client.post(url, {'accion': 'previsualizar', 'archivo': archivo})
        mensajes = [' '.join(f.mensajes) for f in respuesta.context['filas']]
        self.assertIn('Se creará la Pre-Ruta automática (06:30)', mensajes[0])
        self.assertIn('Pre-Ruta del día: ya existe', mensajes[1])
        respuesta = self.client.post(url, {'accion': 'confirmar'})
        self.assertIn('Se creó la Pre-Ruta', ' '.join(respuesta.context['filas'][0].mensajes))
        self.assertEqual(Jornada.objects.filter(transporte=self.f7j, fecha=MANANA).count(), 1)
        self.assertEqual(Programacion.objects.filter(transporte=self.f7j, documento=RUC_DARYZA).count(), 2)
