from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .. import qr
from ..forms import ConductorForm, TransporteForm
from ..models import (CentroDistribucion, ConfiguracionAccesos, Conductor, RegistroAuditoria,
                      Transporte, normalizar_placa)
from .utilidades import UsuariosMixin


class DatosInicialesTests(TestCase):

    def test_la_migracion_crea_grupos_cd_tipos_y_configuracion(self):
        from django.contrib.auth.models import Group
        self.assertEqual(
            set(Group.objects.filter(name__startswith='Accesos - ').values_list('name', flat=True)),
            {'Accesos - Administrador', 'Accesos - Despacho', 'Accesos - Vigilancia', 'Accesos - Consulta'},
        )
        self.assertEqual(CentroDistribucion.objects.get(codigo='LURIN').capacidad_simultanea, 5)
        self.assertEqual(CentroDistribucion.objects.get(codigo='PUNTA_NEGRA').capacidad_simultanea, 3)
        self.assertEqual(ConfiguracionAccesos.obtener().ventana_sugerencia_min, 60)

    def test_el_administrador_puede_gestionar_catalogos_pero_no_borrar(self):
        from django.contrib.auth.models import Group
        permisos = set(Group.objects.get(name='Accesos - Administrador').permissions.values_list('codename', flat=True))
        self.assertIn('change_transporte', permisos)
        self.assertIn('view_registroauditoria', permisos)
        self.assertNotIn('delete_transporte', permisos)
        self.assertNotIn('change_registroauditoria', permisos)


class PlacaTests(UsuariosMixin, TestCase):

    def test_normalizar_placa(self):
        for texto in ('F7J-011', 'f7j011', ' F7J 011 ', 'F7J.011'):
            self.assertEqual(normalizar_placa(texto), 'F7J011')

    def test_al_guardar_se_normaliza_y_se_usa_como_contenido_del_qr(self):
        transporte = Transporte.objects.create(placa='f7j-011', tamano=self.tamano, creado_por=self.admin_accesos)
        self.assertEqual(transporte.placa, 'F7J-011')
        self.assertEqual(transporte.placa_normalizada, 'F7J011')
        self.assertEqual(transporte.contenido_qr, 'F7J011')

    def test_no_se_permite_la_misma_placa_escrita_distinto(self):
        Transporte.objects.create(placa='F7J-011', tamano=self.tamano, creado_por=self.admin_accesos)
        form = TransporteForm(data={'placa': 'f7j 011', 'tamano': self.tamano.pk, 'activo': True})
        self.assertFalse(form.is_valid())
        self.assertIn('placa', form.errors)

    def test_placa_demasiado_corta(self):
        form = TransporteForm(data={'placa': 'AB-1', 'tamano': self.tamano.pk, 'activo': True})
        self.assertFalse(form.is_valid())


class ConductorTests(UsuariosMixin, TestCase):

    def datos(self, **cambios):
        datos = {'tipo_documento': self.dni.pk, 'numero_documento': '12345678',
                 'apellido_paterno': 'pérez', 'apellido_materno': 'gómez', 'nombres': 'juan', 'activo': True}
        datos.update(cambios)
        return datos

    def test_dni_de_8_digitos(self):
        self.assertTrue(ConductorForm(data=self.datos()).is_valid())
        self.assertFalse(ConductorForm(data=self.datos(numero_documento='1234567')).is_valid())
        self.assertFalse(ConductorForm(data=self.datos(numero_documento='1234567A')).is_valid())

    def test_carne_de_extranjeria_alfanumerico(self):
        self.assertTrue(ConductorForm(data=self.datos(tipo_documento=self.ce.pk, numero_documento='ab1234567')).is_valid())
        self.assertFalse(ConductorForm(data=self.datos(tipo_documento=self.ce.pk, numero_documento='12345678')).is_valid())

    def test_documento_unico_por_tipo(self):
        form = ConductorForm(data=self.datos())
        conductor = form.save(commit=False)
        conductor.creado_por = self.admin_accesos
        conductor.save()
        self.assertEqual(conductor.apellido_paterno, 'PÉREZ')
        repetido = ConductorForm(data=self.datos(nombres='otro'))
        self.assertFalse(repetido.is_valid())

    def test_validacion_del_modelo_fuera_del_formulario(self):
        conductor = Conductor(tipo_documento=self.dni, numero_documento='123', apellido_paterno='X',
                              nombres='Y', creado_por=self.admin_accesos)
        with self.assertRaises(ValidationError):
            conductor.full_clean()


class QRTests(SimpleTestCase):

    def test_svg_y_png(self):
        self.assertIn('<svg', qr.svg('F7J011'))
        self.assertTrue(qr.png('F7J011').startswith(b'\x89PNG'))


class AuditoriaDeMaestrosTests(UsuariosMixin, TestCase):

    def test_alta_y_edicion_de_transporte_quedan_auditadas(self):
        self.entrar(self.admin_accesos)
        self.client.post(reverse('accesos:transporte_crear'),
                         {'placa': 'abc-123', 'tamano': self.tamano.pk, 'es_interno': 'on', 'activo': 'on'})
        transporte = Transporte.objects.get(placa_normalizada='ABC123')
        alta = RegistroAuditoria.objects.get(entidad='Transporte', objeto_id=transporte.pk, accion='CREAR')
        self.assertEqual(alta.usuario, self.admin_accesos)
        self.assertEqual(alta.datos_despues['placa'], 'ABC-123')

        self.client.post(reverse('accesos:transporte_editar', args=[transporte.pk]),
                         {'placa': 'ABC-123', 'tamano': self.tamano.pk, 'activo': 'on'})
        cambio = RegistroAuditoria.objects.get(entidad='Transporte', objeto_id=transporte.pk, accion='MODIFICAR')
        self.assertEqual(cambio.datos_antes, {'es_interno': True, 'modificado_por_id': None})
        self.assertEqual(cambio.datos_despues, {'es_interno': False, 'modificado_por_id': self.admin_accesos.pk})

    def test_guardar_sin_cambios_no_genera_registro(self):
        self.entrar(self.admin_accesos)
        transporte = Transporte.objects.create(placa='XYZ-999', tamano=self.tamano, creado_por=self.admin_accesos,
                                               modificado_por=self.admin_accesos)
        self.client.post(reverse('accesos:transporte_editar', args=[transporte.pk]),
                         {'placa': 'XYZ-999', 'tamano': self.tamano.pk, 'activo': 'on'})
        self.assertFalse(RegistroAuditoria.objects.filter(accion='MODIFICAR').exists())

    def test_alta_de_conductor_desde_despacho(self):
        self.entrar(self.despacho)
        respuesta = self.client.post(reverse('accesos:conductor_crear'), {
            'tipo_documento': self.dni.pk, 'numero_documento': '87654321',
            'apellido_paterno': 'Quispe', 'apellido_materno': '', 'nombres': 'Rosa', 'activo': 'on'})
        self.assertRedirects(respuesta, reverse('accesos:conductores_lista'))
        conductor = Conductor.objects.get(numero_documento='87654321')
        self.assertEqual(conductor.creado_por, self.despacho)
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='Conductor', objeto_id=conductor.pk).exists())

    def test_qr_solo_para_internos(self):
        self.entrar(self.admin_accesos)
        externo = Transporte.objects.create(placa='EXT-001', tamano=self.tamano, creado_por=self.admin_accesos)
        interno = Transporte.objects.create(placa='INT-001', tamano=self.tamano, es_interno=True,
                                            creado_por=self.admin_accesos)
        self.assertRedirects(self.client.get(reverse('accesos:transporte_qr', args=[externo.pk])),
                             reverse('accesos:transportes_lista'))
        pagina = self.client.get(reverse('accesos:transporte_qr', args=[interno.pk]))
        self.assertContains(pagina, '<svg')
        self.assertContains(pagina, 'INT001')
        png = self.client.get(reverse('accesos:transporte_qr_png', args=[interno.pk]))
        self.assertEqual(png['Content-Type'], 'image/png')
        self.assertEqual(self.client.get(reverse('accesos:transporte_qr_png', args=[externo.pk])).status_code, 404)
