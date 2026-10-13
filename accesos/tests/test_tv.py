"""Modo TV por CD (F6.4): acceso por rol y CD, sesión larga de la pantalla, datos en JSON."""
from django.urls import reverse

from .. import jornadas, pantalla, tiempo
from ..models import UsuarioCD
from ..permisos import GRUPO_PANTALLA
from .test_jornada import JornadaBase
from .test_programacion import HOY
from .utilidades import CLAVE, crear_usuario


class ModoTvTests(JornadaBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tv = crear_usuario('tv_lurin', GRUPO_PANTALLA)
        UsuarioCD.objects.create(usuario=cls.tv, cd=cls.lurin)

    def test_la_pantalla_entra_directo_a_su_cd_y_nada_mas(self):
        self.entrar(self.tv)
        respuesta = self.client.get(reverse('accesos:inicio'), follow=True)
        self.assertEqual(respuesta.redirect_chain[-1][0], reverse('accesos:tv', args=['LURIN']))
        self.assertContains(respuesta, 'Flota propia hoy')
        self.assertEqual(self.client.get(reverse('accesos:tv', args=['PUNTA_NEGRA'])).status_code, 404)
        for nombre in ('accesos:dashboard', 'accesos:tablero_hoy', 'accesos:porteria', 'accesos:programacion_agenda'):
            self.assertEqual(self.client.get(reverse(nombre)).status_code, 403, nombre)
        # Planificación lo devuelve al módulo (aislamiento).
        self.assertRedirects(self.client.get('/'), reverse('accesos:inicio'), fetch_redirect_response=False)

    def test_sesion_de_30_dias_solo_para_la_pantalla(self):
        self.client.login(username='tv_lurin', password=CLAVE)
        self.assertEqual(self.client.session.get_expiry_age(), 30 * 24 * 3600)
        self.client.logout()
        self.client.login(username='consulta', password=CLAVE)
        self.assertNotEqual(self.client.session.get_expiry_age(), 30 * 24 * 3600)

    def test_permisos_por_rol(self):
        for usuario, codigo in ((self.admin_accesos, 200), (self.despacho, 200), (self.consulta, 200),
                                (self.vigilante, 403), (self.comercial, 403)):
            with self.subTest(usuario=usuario.username):
                self.entrar(usuario)
                self.assertEqual(self.client.get(reverse('accesos:tv', args=['PUNTA_NEGRA'])).status_code, codigo)

    def test_datos_en_json_con_flota_y_alertas(self):
        self.dia_completo(HOY, retorno=(16, 40))
        self.mover_reloj(HOY, 18, 0)
        jornadas.registrar_no_disponibilidad(transporte_id=self.externo.pk, tipo_id=self.averia.pk,
                                             descripcion='Taller', usuario=self.despacho, ahora=tiempo.ahora())
        self.entrar(self.tv)
        datos = self.client.get(reverse('accesos:tv_datos', args=['LURIN'])).json()
        self.assertEqual(datos['cd'], 'Lurín')
        self.assertEqual(datos['cifras']['fuera_de_horario'], 1)
        self.assertEqual(datos['cifras']['flota_dentro'], 1)  # duerme en el CD
        self.assertEqual(len(datos['gantt']['filas']), 1)
        self.assertEqual(datos['gantt']['filas'][0]['conductor'], self.conductor.nombre_completo)
        self.assertTrue(any('no disponible' in a['texto'] for a in datos['alertas']))
        self.assertEqual(datos['dentro'][0]['descripcion'], 'Flota propia · pernocta')

    def test_dia_no_laborable(self):
        self.mover_reloj(tiempo.combinar(HOY, tiempo.ahora().time()).date().replace(day=11), 10)  # domingo 11/10
        self.assertEqual(pantalla.datos(self.lurin, tiempo.ahora())['no_laborable'], 'Domingo')
