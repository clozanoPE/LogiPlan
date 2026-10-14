"""
Dashboard en cuatro pestañas (F6.3): resumen ejecutivo, flota propia (jornada) y disponibilidad. Los
indicadores de la flota se verifican contra un día armado a mano con la portería real.
"""
from datetime import date, time

from django.urls import reverse

from .. import indicadores_flota, jornadas, tiempo
from ..models import Jornada
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA

AYER = date(2026, 10, 6)


class IndicadoresFlotaTests(JornadaBase):

    def setUp(self):
        super().setUp()
        # Ayer: retorno 16:40 (fuera de horario, +25 min sobre las 16:15). Hoy: Pre-Ruta de 60 min y retorno 16:10.
        self.dia_completo(AYER, retorno=(16, 40))
        self.dia_completo(HOY, retorno=(16, 10))
        self.mover_reloj(HOY, 18, 0)

    def lista(self, desde=AYER, hasta=HOY, **filtros):
        return indicadores_flota.jornadas_del_periodo(desde, hasta, filtros)

    def test_kpis_de_la_jornada(self):
        k = indicadores_flota.kpis(self.lista(), tiempo.ahora(),
                                   indicadores_flota.programaciones_internas(AYER, HOY, {}))
        self.assertEqual((k['jornadas'], k['con_salida'], k['cierres']), (2, 2, 2))
        self.assertEqual(k['pct_pre_ruta_a_tiempo'], 100.0)  # 06:30 → 07:30 = 60 min (meta 60)
        self.assertEqual(k['pre_ruta_promedio'], 60)
        self.assertEqual((k['pct_cierre_en_horario'], k['fuera_de_horario'], k['minutos_sobre_cierre']), (50.0, 1, 25))
        self.assertEqual((k['rutas'], k['rutas_atendidas']), (2, 2))  # solo los días terminados (ayer)

    def test_un_filtro_de_otro_tipo_vacia_la_flota(self):
        self.assertEqual(self.lista(tipo='CLIENTE'), [])
        self.assertEqual(len(self.lista(placa='F7J011')), 2)
        self.assertEqual(self.lista(placa='ABC123'), [])

    def test_ranking_por_conductor_no_muestra_minutos(self):
        por_placa = indicadores_flota.ranking(self.lista(), 'placa')
        por_conductor = indicadores_flota.ranking(self.lista(), 'conductor')
        self.assertEqual(por_placa[0]['minutos_sobre_cierre'], 25)
        self.assertNotIn('minutos_sobre_cierre', por_conductor[0])

    def test_gantt_del_dia(self):
        g = indicadores_flota.gantt(HOY, tiempo.ahora(), {})
        fila = g['filas'][0]
        self.assertEqual([s['tipo'] for s in fila['segmentos']], ['pre', 'visita', 'post'])
        self.assertEqual((fila['segmentos'][0]['desde'], fila['segmentos'][0]['hasta']), (390, 450))
        self.assertEqual(fila['en_ruta'], [450, 16 * 60 + 10])
        self.assertEqual(g['ahora'], 18 * 60)
        self.assertTrue(g['desde'] <= 390 and g['hasta'] >= 18 * 60)

    def test_matriz_y_distribuciones(self):
        m = indicadores_flota.matriz(self.lista(), AYER, HOY, tiempo.ahora())
        self.assertEqual([c['clase'] for c in m['filas'][0]['celdas']], ['critico', 'ok'])
        retorno = indicadores_flota.distribucion_retorno(self.lista())
        self.assertEqual(sum(retorno['conteos']), 2)
        self.assertEqual(retorno['bandas'][retorno['etiquetas'].index('16:30')], 'fuera')
        pre = indicadores_flota.distribucion_pre_ruta(self.lista())
        self.assertEqual(pre['conteos'][3], 2)  # 60–75 min

    def test_disponibilidad(self):
        self.mover_reloj(MANANA, 9, 0)
        jornadas.registrar_no_disponibilidad(transporte_id=self.f7j.pk, tipo_id=self.averia.pk, descripcion='Taller',
                                             usuario=self.despacho, ahora=tiempo.ahora())
        d = indicadores_flota.disponibilidad(AYER, MANANA, {}, tiempo.ahora())
        self.assertEqual((d['flota'], d['dias'], d['dias_unidad_fuera']), (1, 3, 1))  # 1 unidad propia, 3 días
        self.assertEqual(d['pct_disponibilidad'], 66.7)
        self.assertEqual((d['averias'], d['no_disponibles_ahora']), (1, 1))
        self.assertEqual(len(d['linea']), 1)


class PestanasTests(JornadaBase):

    def test_las_cuatro_pestanas_por_rol(self):
        self.dia_completo(HOY, retorno=(16, 40))
        self.mover_reloj(HOY, 18, 0)
        url = reverse('accesos:dashboard')
        for usuario in (self.admin_accesos, self.consulta, self.vigilante):
            self.entrar(usuario)
            for vista, texto in (('resumen', 'Calendario de la flota propia'), ('planta', 'Mapa de calor'),
                                 ('flota', 'Línea de tiempo del día'), ('disponibilidad', 'fuera de servicio')):
                with self.subTest(usuario=usuario.username, vista=vista):
                    respuesta = self.client.get(url, {'vista': vista})
                    self.assertContains(respuesta, texto)
        # Rango de varios días: matriz en vez de línea de tiempo; la pestaña por defecto es el resumen.
        respuesta = self.client.get(url, {'vista': 'flota', 'desde': '2026-10-01', 'hasta': '2026-10-07'})
        self.assertContains(respuesta, 'Cómo cerró cada jornada')
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.context['vista'], 'resumen')
        self.assertEqual(len(respuesta.context['resumen']['tarjetas']), 8)

    def test_el_aviso_operativo_acompana_al_exceso(self):
        self.entrar(self.consulta)
        respuesta = self.client.get(reverse('accesos:dashboard'), {'vista': 'flota'})
        self.assertContains(respuesta, 'No es un registro de asistencia ni sustenta horas extra')
        self.assertNotContains(respuesta, 'Horas extra')

    def test_filtros_que_solo_aplican_a_planta_se_avisan(self):
        self.entrar(self.consulta)
        respuesta = self.client.get(reverse('accesos:dashboard'), {'vista': 'flota', 'motivo': 'Recojo'})
        self.assertContains(respuesta, 'solo aplican a la operación en planta')
        self.assertEqual(self.client.get(reverse('accesos:dashboard'), {'vista': 'xx'}).context['vista'], 'resumen')

    def test_vigilancia_no_ve_enlaces_a_pantallas_sin_permiso(self):
        """Encontrado en la prueba integral: «Gestionar averías» llevaba a Vigilancia a un 403."""
        self.entrar(self.vigilante)
        respuesta = self.client.get(reverse('accesos:dashboard'), {'vista': 'disponibilidad'})
        self.assertNotContains(respuesta, reverse('accesos:flota_disponibilidad'))
        self.entrar(self.consulta)
        respuesta = self.client.get(reverse('accesos:dashboard'), {'vista': 'disponibilidad'})
        self.assertContains(respuesta, 'Ver averías')
