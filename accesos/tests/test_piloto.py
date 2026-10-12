"""
Piloto del módulo (ACCESOS_USUARIOS_PILOTO): solo los usuarios listados usan Accesos, aunque otros tengan
rol o sean superusuarios; Planificación sigue igual para todos. '*' = lo planificado (todos los que tengan rol).
"""
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .utilidades import UsuariosMixin, crear_usuario

PANTALLAS = ('accesos:inicio', 'accesos:programacion_agenda', 'accesos:conductores_lista', 'accesos:dashboard',
             'accesos:tablero_hoy')


@override_settings(ACCESOS_USUARIOS_PILOTO='clozano')
class PilotoTests(UsuariosMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.clozano = crear_usuario('clozano', 'Planeamiento', is_staff=True, is_superuser=True)

    def test_solo_el_usuario_del_piloto_entra(self):
        self.client.force_login(self.clozano)
        self.assertContains(self.client.get('/'), reverse('accesos:inicio'))
        for nombre in PANTALLAS:
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200)

    def test_los_demas_no_ven_el_enlace_ni_las_pantallas(self):
        for usuario in (self.superusuario, self.despacho, self.planeamiento, self.admin_accesos, self.consulta):
            self.client.force_login(usuario)
            with self.subTest(usuario=usuario.username):
                if usuario in (self.superusuario, self.despacho, self.planeamiento):
                    self.assertNotContains(self.client.get('/'), reverse('accesos:inicio'))
                for nombre in PANTALLAS:
                    self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)

    def test_planificacion_no_cambia_para_los_demas(self):
        for usuario in (self.planeamiento, self.despacho, self.superusuario):
            self.client.force_login(usuario)
            with self.subTest(usuario=usuario.username):
                self.assertEqual(self.client.get('/').status_code, 200)
                self.assertEqual(self.client.get(reverse('registrar_planificacion')).status_code, 200)
                self.assertEqual(self.client.get(reverse('lista_busqueda')).status_code, 200)

    def test_django_admin_oculta_el_modulo_a_otros_superusuarios(self):
        self.client.force_login(self.superusuario)
        self.assertNotContains(self.client.get('/admin/'), '/admin/accesos/')
        self.assertIn(self.client.get('/admin/accesos/tramo/').status_code, (403, 404))
        self.assertIn(self.client.get('/admin/accesos/configuracionaccesos/').status_code, (403, 404))
        self.client.force_login(self.clozano)
        self.assertContains(self.client.get('/admin/'), '/admin/accesos/')
        self.assertEqual(self.client.get('/admin/accesos/tramo/').status_code, 200)

    @override_settings(ACCESOS_USUARIOS_PILOTO=' CLOZANO , despacho ')
    def test_lista_de_varios_usuarios_sin_distinguir_mayusculas(self):
        for usuario in (self.clozano, self.despacho):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('accesos:programacion_agenda')).status_code, 200)
        self.client.force_login(self.planeamiento)
        self.assertEqual(self.client.get(reverse('accesos:programacion_agenda')).status_code, 403)

    @override_settings(ACCESOS_USUARIOS_PILOTO='*')
    def test_asterisco_libera_lo_planificado(self):
        for usuario in (self.despacho, self.planeamiento, self.superusuario):
            self.client.force_login(usuario)
            self.assertContains(self.client.get('/'), reverse('accesos:inicio'))
            self.assertEqual(self.client.get(reverse('accesos:programacion_agenda')).status_code, 200)


class PilotoPorDefectoTests(TestCase):

    def test_con_base_local_no_hay_restriccion(self):
        # Con la base local (desarrollo y pruebas) el módulo queda abierto; en Railway, solo 'clozano'.
        self.assertEqual(settings.ACCESOS_USUARIOS_PILOTO, '*')
