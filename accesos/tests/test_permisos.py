import os
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from ..models import ConfiguracionAccesos, RegistroAuditoria, Transporte
from .utilidades import CLAVE, UsuariosMixin


class MatrizDePermisosTests(UsuariosMixin, TestCase):

    def estado(self, usuario, url):
        self.client.force_login(usuario)
        return self.client.get(url).status_code

    def test_inicio_del_modulo(self):
        url = reverse('accesos:inicio')
        # Planeamiento equivale a Despacho en Accesos.
        for usuario in (self.admin_accesos, self.despacho, self.vigilante, self.consulta, self.superusuario,
                        self.planeamiento):
            self.assertEqual(self.estado(usuario, url), 200, usuario.username)
        for usuario in (self.comercial, self.sin_grupo):
            self.assertEqual(self.estado(usuario, url), 403, usuario.username)

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(reverse('accesos:inicio'))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse('login'), respuesta['Location'])

    def test_maestros_alta_y_consulta(self):
        for nombre in ('transportes_lista', 'transporte_crear', 'conductores_lista', 'conductor_crear'):
            url = reverse(f'accesos:{nombre}')
            self.assertEqual(self.estado(self.admin_accesos, url), 200, nombre)
            self.assertEqual(self.estado(self.despacho, url), 200, nombre)
            self.assertEqual(self.estado(self.vigilante, url), 403, nombre)
            self.assertEqual(self.estado(self.consulta, url), 403, nombre)

    def test_edicion_y_qr_solo_administrador(self):
        transporte = Transporte.objects.create(placa='F7J-011', tamano=self.tamano, es_interno=True,
                                               creado_por=self.admin_accesos)
        for nombre in ('transporte_editar', 'transporte_qr', 'transporte_qr_png'):
            url = reverse(f'accesos:{nombre}', args=[transporte.pk])
            self.assertEqual(self.estado(self.admin_accesos, url), 200, nombre)
            self.assertEqual(self.estado(self.superusuario, url), 200, nombre)
            self.assertEqual(self.estado(self.despacho, url), 403, nombre)


class AislamientoTests(UsuariosMixin, TestCase):
    """Vigilancia y Consulta no ven Planificación; los demás navegan igual que antes."""

    RUTAS_PLANIFICACION = ['/', '/dashboard/', '/busqueda/', '/estadisticas/', '/nuevo/', '/admin/']

    def test_vigilancia_y_consulta_quedan_dentro_del_modulo(self):
        for usuario in (self.vigilante, self.consulta):
            self.client.force_login(usuario)
            for ruta in self.RUTAS_PLANIFICACION:
                respuesta = self.client.get(ruta)
                self.assertRedirects(respuesta, reverse('accesos:inicio'), fetch_redirect_response=False,
                                     msg_prefix=f"{usuario.username} {ruta}")

    def test_despues_del_login_vigilancia_llega_al_modulo(self):
        respuesta = self.client.post(reverse('login'), {'username': 'vigilante', 'password': CLAVE}, follow=True)
        self.assertEqual(respuesta.redirect_chain[-1][0], reverse('accesos:inicio'))
        self.assertContains(respuesta, 'Cambiar vigilante')

    def test_administrador_del_modulo_entra_a_django_admin_pero_no_a_planificacion(self):
        # Solo tiene el grupo Administrador de Accesos y is_staff (así se configura en producción).
        self.client.force_login(self.admin_accesos)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get('/admin/accesos/tamanovehiculo/add/').status_code, 200)
        respuesta = self.client.post('/admin/accesos/tamanovehiculo/add/', {'nombre': 'Furgón', 'orden': 9,
                                                                              'activo': 'on'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='TamanoVehiculo', objeto_repr='Furgón').exists())
        for ruta in ('/', '/busqueda/', '/estadisticas/'):
            self.assertRedirects(self.client.get(ruta), reverse('accesos:inicio'), fetch_redirect_response=False)
        # Sin is_staff, Django Admin sigue cerrado para un usuario solo de Accesos.
        self.client.force_login(self.vigilante)
        self.assertRedirects(self.client.get('/admin/'), reverse('accesos:inicio'), fetch_redirect_response=False)

    def test_despacho_que_tambien_es_planeamiento_sigue_viendo_planificacion(self):
        self.client.force_login(self.despacho)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_logout_y_login_siguen_disponibles(self):
        self.client.force_login(self.vigilante)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        respuesta = self.client.post(reverse('logout'))
        self.assertEqual(respuesta.status_code, 302)


class SesionDeVigilanciaTests(UsuariosMixin, TestCase):

    def test_la_sesion_de_vigilancia_dura_las_horas_configuradas(self):
        ConfiguracionAccesos.objects.filter(pk=1).update(sesion_vigilancia_horas=12)
        self.client.post(reverse('login'), {'username': 'vigilante', 'password': CLAVE})
        self.assertEqual(self.client.session.get_expiry_age(), 12 * 3600)

    def test_la_sesion_de_otros_roles_no_cambia(self):
        self.client.post(reverse('login'), {'username': 'planeamiento', 'password': CLAVE})
        self.assertNotEqual(self.client.session.get_expiry_age(), 12 * 3600)


class EnlaceEnBaseTests(UsuariosMixin, TestCase):
    """El enlace «Accesos» depende solo de los grupos; ACCESOS_ACTIVO=0 es un interruptor de emergencia."""

    def test_enlace_visible_por_defecto_segun_el_grupo(self):
        with mock.patch.dict('os.environ', {}, clear=False):
            os.environ.pop('ACCESOS_ACTIVO', None)
            for usuario in (self.despacho, self.planeamiento, self.superusuario):
                self.client.force_login(usuario)
                self.assertContains(self.client.get('/'), reverse('accesos:inicio'), msg_prefix=usuario.username)
            for usuario in (self.comercial, self.sin_grupo):
                self.client.force_login(usuario)
                self.assertNotContains(self.client.get('/'), reverse('accesos:inicio'), msg_prefix=usuario.username)

    def test_interruptor_de_emergencia_oculta_el_enlace(self):
        self.client.force_login(self.despacho)
        for valor in ('0', 'false', 'no'):
            with mock.patch.dict('os.environ', {'ACCESOS_ACTIVO': valor}):
                self.assertNotContains(self.client.get('/'), reverse('accesos:inicio'), msg_prefix=valor)
        with mock.patch.dict('os.environ', {'ACCESOS_ACTIVO': '1'}):
            self.assertContains(self.client.get('/'), reverse('accesos:inicio'))


class PlaneamientoEsDespachoTests(UsuariosMixin, TestCase):
    """Un usuario solo del grupo Planeamiento tiene en Accesos los permisos de Despacho."""

    def test_planeamiento_programa_y_registra_maestros(self):
        self.client.force_login(self.planeamiento)
        for nombre in ('accesos:inicio', 'accesos:programacion_agenda', 'accesos:programacion_crear',
                       'accesos:transportes_lista', 'accesos:conductores_lista', 'accesos:tramos_lista',
                       'accesos:dashboard', 'accesos:tablero_hoy'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 200, nombre)
        self.assertEqual(self.client.get(reverse('accesos:carga_masiva', args=['programaciones'])).status_code, 200)
        # Pero no lo propio de otros roles, y sigue viendo Planificación.
        self.assertEqual(self.client.get(reverse('accesos:porteria')).status_code, 403)
        self.assertEqual(self.client.get(reverse('accesos:auditoria_lista')).status_code, 403)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/busqueda/').status_code, 200)

    def test_comercial_sigue_sin_acceso(self):
        self.client.force_login(self.comercial)
        self.assertEqual(self.client.get(reverse('accesos:programacion_agenda')).status_code, 403)
        self.assertEqual(self.client.get('/').status_code, 200)
