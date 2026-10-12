"""
Listados de Django Admin del módulo: responden y no unen tablas sin límite. Sin list_select_related,
Django hace select_related() completo al mostrar llaves foráneas: el listado de Tramos llegó a unir
19 tablas y MySQL tardaba ~100 s solo en planificar la consulta.
"""
import re

from django.contrib import admin
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .test_programacion import ProgramacionBase

MAX_JOINS = 4


class ListadosAdminTests(ProgramacionBase):

    def test_listados_sin_uniones_desbordadas(self):
        self.programar(10, 0)
        self.entrar(self.superusuario)
        modelos = [m for m in admin.site._registry if m._meta.app_label == 'accesos']
        self.assertGreaterEqual(len(modelos), 10)
        for modelo in modelos:
            url = reverse(f'admin:accesos_{modelo._meta.model_name}_changelist')
            with self.subTest(modelo=modelo.__name__), CaptureQueriesContext(connection) as consultas:
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.get(url, {'_facets': 'True'}).status_code, 200)
                peor = max(len(re.findall(r'\bJOIN\b', c['sql'])) for c in consultas.captured_queries)
                self.assertLessEqual(peor, MAX_JOINS, f"{modelo.__name__}: una consulta une {peor} tablas")

    def test_cada_listado_declara_sus_relaciones(self):
        for modelo, modelo_admin in admin.site._registry.items():
            if modelo._meta.app_label != 'accesos':
                continue
            llaves = [c for c in modelo_admin.list_display
                      if isinstance(getattr(modelo, c, None), object) and c in {f.name for f in modelo._meta.fields}
                      and modelo._meta.get_field(c).is_relation]
            if llaves:
                with self.subTest(modelo=modelo.__name__):
                    self.assertTrue(modelo_admin.list_select_related, "falta list_select_related")
                    self.assertNotEqual(modelo_admin.list_select_related, True)
