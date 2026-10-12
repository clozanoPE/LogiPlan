"""
Regresión de Planificación: cada vista existente debe responder exactamente igual con el
middleware de Accesos activo que sin él, para los perfiles que ya usaban el sistema.
"""
from datetime import date

from django.conf import settings
from django.test import Client, TestCase, override_settings

from .utilidades import UsuariosMixin

MIDDLEWARE_SIN_ACCESOS = [m for m in settings.MIDDLEWARE if not m.startswith('accesos.')]


class PlanificacionSinCambiosTests(UsuariosMixin, TestCase):

    def rutas(self):
        hoy = date.today().isoformat()  # mismo criterio de fecha que usa planificacion
        return ['/', '/dashboard/', '/busqueda/', '/estadisticas/', '/nuevo/', '/programar/masivo/',
                f'/detalle/{hoy}/', '/busqueda/exportar/', '/login/']

    def huella(self, usuario):
        # Cliente nuevo en cada llamada: el handler del cliente guarda la cadena de middleware
        # en caché, así que solo uno creado dentro de override_settings la carga sin Accesos.
        cliente = Client()
        resultado = {}
        for ruta in self.rutas():
            if usuario is not None:
                cliente.force_login(usuario)
            respuesta = cliente.get(ruta)
            resultado[ruta] = (respuesta.status_code, respuesta.get('Location'))
        return resultado

    def test_mismas_respuestas_con_y_sin_el_middleware(self):
        for usuario in (None, self.planeamiento, self.comercial, self.sin_grupo, self.superusuario, self.despacho):
            nombre = usuario.username if usuario else 'anónimo'
            con_middleware = self.huella(usuario)
            with override_settings(MIDDLEWARE=MIDDLEWARE_SIN_ACCESOS):
                sin_middleware = self.huella(usuario)
            self.assertEqual(con_middleware, sin_middleware, nombre)

    def test_planeamiento_sigue_operando(self):
        self.client.force_login(self.planeamiento)
        for ruta in ('/', '/busqueda/', '/estadisticas/', '/nuevo/'):
            self.assertEqual(self.client.get(ruta).status_code, 200, ruta)
