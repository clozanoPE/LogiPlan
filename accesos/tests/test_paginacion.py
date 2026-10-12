"""
Paginación de las grillas (15 por página, números con saltos, filtros conservados, orden sin empates) y
actualización automática de las pantallas en vivo (Dashboard solo si el rango incluye hoy, «Hoy», «Dentro ahora»).
"""
from datetime import timedelta

from django.urls import reverse

from ..models import Conductor, RegistroAuditoria, Transporte
from ..paginacion import POR_PAGINA
from .test_porteria import PorteriaBase
from .test_programacion import HOY


class PaginacionTests(PorteriaBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # 40 homónimos: solo el id los distingue, así que prueban que el orden no tenga empates.
        Conductor.objects.bulk_create(
            Conductor(tipo_documento=cls.dni, numero_documento=f'7{n:07d}', apellido_paterno='QUISPE',
                      nombres='ANA', creado_por=cls.admin_accesos) for n in range(40))
        Transporte.objects.bulk_create(
            Transporte(placa=f'P{n:02d}-100', placa_normalizada=f'P{n:02d}100', tamano=cls.tamano,
                       creado_por=cls.admin_accesos) for n in range(40))

    def recorrer(self, url, **filtros):
        """Todas las páginas de una grilla: lista de pks en el orden mostrado."""
        primera = self.client.get(url, filtros)
        vistos = []
        for numero in range(1, primera.context['page_obj'].paginator.num_pages + 1):
            vistos += [o.pk for o in self.client.get(url, {**filtros, 'page': numero}).context['page_obj']]
        return primera, vistos

    def test_quince_por_pagina_sin_repetidos_ni_faltantes(self):
        self.entrar(self.admin_accesos)
        for url, total in ((reverse('accesos:conductores_lista'), Conductor.objects.count()),
                           (reverse('accesos:transportes_lista'), Transporte.objects.count())):
            with self.subTest(url=url):
                primera, vistos = self.recorrer(url)
                self.assertEqual(POR_PAGINA, 15)
                self.assertEqual(len(primera.context['page_obj']), 15)
                self.assertEqual(len(vistos), total)
                self.assertEqual(len(set(vistos)), total)

    def test_numeros_con_saltos_e_inicio_y_ultimo(self):
        self.entrar(self.admin_accesos)
        url = reverse('accesos:transportes_lista')
        respuesta = self.client.get(url, {'page': 2})
        total = Transporte.objects.count()
        paginas = respuesta.context['paginas']
        self.assertEqual(paginas[0], 1)
        self.assertEqual(paginas[-1], respuesta.context['page_obj'].paginator.num_pages)
        self.assertContains(respuesta, f'Mostrando 16–30 de {total} registros')
        self.assertContains(respuesta, 'aria-current="page">2<')
        self.assertContains(respuesta, '>Inicio<')
        self.assertContains(respuesta, '>Último<')
        self.assertContains(respuesta, 'Página 2 de 3')   # versión de teléfono

    def test_saltos_con_muchas_paginas(self):
        self.entrar(self.admin_accesos)
        Conductor.objects.bulk_create(
            Conductor(tipo_documento=self.dni, numero_documento=f'8{n:07d}', apellido_paterno='ROJAS',
                      nombres='LUIS', creado_por=self.admin_accesos) for n in range(150))
        respuesta = self.client.get(reverse('accesos:conductores_lista'), {'page': 6})
        paginas = respuesta.context['paginas']
        ultima = respuesta.context['page_obj'].paginator.num_pages
        self.assertEqual(paginas, [1, '…', 5, 6, 7, '…', ultima])
        self.assertContains(respuesta, '…')

    def test_conserva_filtros_y_tolera_pagina_invalida(self):
        self.entrar(self.admin_accesos)
        url = reverse('accesos:conductores_lista')
        respuesta = self.client.get(url, {'q': 'QUISPE', 'page': 2})
        self.assertEqual(respuesta.context['querystring'], 'q=QUISPE')
        self.assertContains(respuesta, '?page=3&q=QUISPE')
        self.assertContains(respuesta, '?page=1&q=QUISPE')
        for invalida in ('abc', '999', '-1'):
            with self.subTest(page=invalida):
                self.assertEqual(self.client.get(url, {'q': 'QUISPE', 'page': invalida}).status_code, 200)

    def test_sin_paginacion_si_cabe_en_una_pagina(self):
        self.entrar(self.admin_accesos)
        respuesta = self.client.get(reverse('accesos:conductores_lista'), {'q': '70000001'})
        self.assertEqual(len(respuesta.context['page_obj']), 1)
        self.assertNotContains(respuesta, 'aria-label="Paginación"')

    def test_tramos_y_auditoria_paginan_igual(self):
        tramos = []
        for n in range(17):
            transporte = Transporte.objects.get(placa_normalizada=f'P{n:02d}100')
            programacion = self.programar(6, n, transporte=transporte)
            tramos.append(self.salir(self.entrar_en(programacion, confirmar=True)))
        RegistroAuditoria.objects.bulk_create(
            RegistroAuditoria(usuario=self.admin_accesos, accion=RegistroAuditoria.Accion.MODIFICAR,
                              entidad='Prueba', objeto_id=n, objeto_repr=f'Registro {n}') for n in range(20))
        self.entrar(self.admin_accesos)
        _, vistos = self.recorrer(reverse('accesos:tramos_lista'), desde=HOY.isoformat(), hasta=HOY.isoformat())
        self.assertEqual(sorted(vistos), sorted(t.pk for t in tramos))
        _, vistos = self.recorrer(reverse('accesos:auditoria_lista'))
        self.assertEqual(len(vistos), RegistroAuditoria.objects.count())
        self.assertEqual(len(set(vistos)), len(vistos))


class AutorefrescoTests(PorteriaBase):

    def test_dashboard_se_actualiza_solo_si_el_rango_incluye_hoy(self):
        self.entrar(self.consulta)
        url = reverse('accesos:dashboard')
        self.assertContains(self.client.get(url), 'data-autorefresco="120"')
        self.assertContains(self.client.get(url, {'desde': (HOY - timedelta(days=6)).isoformat(),
                                                  'hasta': HOY.isoformat()}), 'data-autorefresco="120"')
        ayer = (HOY - timedelta(days=1)).isoformat()
        self.assertNotContains(self.client.get(url, {'desde': ayer, 'hasta': ayer}), 'data-autorefresco="')

    def test_hoy_y_dentro_ahora_cada_minuto(self):
        self.entrar(self.consulta)
        self.assertContains(self.client.get(reverse('accesos:tablero_hoy')), 'data-autorefresco="60"')
        self.entrar(self.vigilante)
        self.assertContains(self.client.get(reverse('accesos:porteria_dentro')), 'data-autorefresco="60"')

    def test_pantallas_de_trabajo_no_se_recargan(self):
        self.entrar(self.admin_accesos)
        for nombre in ('conductores_lista', 'transportes_lista', 'tramos_lista', 'programacion_agenda'):
            with self.subTest(pantalla=nombre):
                self.assertNotContains(self.client.get(reverse(f'accesos:{nombre}')), 'data-autorefresco="')
