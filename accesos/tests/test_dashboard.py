from datetime import date

from django.urls import reverse

from .. import indicadores, servicios
from ..models import Conductor, ConfiguracionAccesos, Transporte
from .test_porteria import PorteriaBase
from .test_programacion import HOY

AYER = date(2026, 10, 6)


class DatosDelMartes(PorteriaBase):
    """Datos del martes 06/10: F7J-011 08:00 (30 min) y 10:40 (60 min, llegó 40 min tarde), OTR-001 09:00
    (300 min, con otro conductor) y una cita de las 16:00 que nunca llegó."""

    def setUp(self):
        super().setUp()
        self.otro = Transporte.objects.create(placa='OTR-001', tamano=self.tamano, creado_por=self.admin_accesos)
        self.luna = Conductor.objects.create(tipo_documento=self.dni, numero_documento='99887766',
                                             apellido_paterno='LUNA', nombres='CARLOS', creado_por=self.admin_accesos)
        recorrido = [(self.f7j, (8, 0), (8, 0), 30, None), (self.f7j, (10, 0), (10, 40), 60, None),
                     (self.otro, (9, 0), (9, 0), 300, self.luna.pk)]
        for transporte, programada, llegada, minutos, conductor in recorrido:
            programacion = self.programar(*programada, transporte=transporte, fecha=AYER)
            self.mover_reloj(AYER, *llegada)
            tramo = self.entrar_en(programacion, conductor_id=conductor)
            self.mover_reloj(AYER, llegada[0] + minutos // 60, llegada[1] + minutos % 60)
            self.salir(tramo)
        self.programar(16, 0, fecha=AYER)  # nunca llegó
        self.mover_reloj(HOY, 9, 0)
        self.config = ConfiguracionAccesos.obtener()
        self.tramos = indicadores.tramos_del_periodo(AYER, HOY, {})
        self.programaciones = indicadores.programaciones_del_periodo(AYER, HOY, {})


class IndicadoresTests(DatosDelMartes):

    def test_kpis(self):
        k = indicadores.kpis(self.tramos, self.programaciones, self.config, HOY)
        self.assertEqual(k['tramos'], 3)
        self.assertEqual(k['permanencia_promedio'], 130)
        self.assertEqual(k['prolongadas'], 1)          # 300 > 240
        self.assertEqual(k['pct_demora'], 33.3)        # sin meta: se usa el umbral de 240
        self.assertEqual(k['pct_puntual'], 66.7)       # 08:00 y 09:00 puntuales; 10:40 llegó 40 min tarde
        self.assertEqual((k['citas_no_atendidas'], k['citas_programadas']), (1, 4))
        self.assertEqual(k['horas_en_planta'], 6.5)

    def test_la_meta_de_gerencia_cambia_que_es_demora(self):
        ConfiguracionAccesos.objects.filter(pk=1).update(meta_permanencia_min=45)
        config = ConfiguracionAccesos.obtener()
        self.assertEqual(indicadores.kpis(self.tramos, self.programaciones, config, HOY)['pct_demora'], 66.7)

    def test_rankings_por_placa_y_por_conductor_real(self):
        por_placa = indicadores.ranking(self.tramos, self.config, 'placa')
        self.assertEqual([f['nombre'] for f in por_placa], ['OTR-001', 'F7J-011'])
        self.assertEqual((por_placa[1]['promedio'], por_placa[1]['tramos'], por_placa[1]['pct_retraso']), (45, 2, 50.0))
        por_conductor = indicadores.ranking(self.tramos, self.config, 'conductor')
        self.assertEqual(por_conductor[0]['nombre'], 'LUNA, CARLOS')  # el conductor que realmente ingresó
        self.assertEqual(por_conductor[0]['demoras'], 1)
        self.assertEqual(set(indicadores.rankings(self.tramos, self.config)), set(indicadores.DIMENSIONES))

    def test_mapa_de_calor(self):
        mapa = indicadores.mapa_de_calor(self.tramos, self.config)
        self.assertEqual(mapa['horas'], [8, 9, 10])
        martes = mapa['celdas'][AYER.weekday()]
        self.assertEqual([c['llegadas'] for c in martes], [1, 1, 1])
        self.assertEqual([c['promedio'] for c in martes], [30, 300, 60])
        self.assertEqual(martes[1]['pct_demora'], 100.0)
        self.assertEqual(mapa['celdas'][0][0]['llegadas'], 0)  # lunes vacío

    def test_distribucion_tendencia_y_llegada(self):
        dist = indicadores.distribucion(self.tramos, self.config)
        self.assertEqual(dist['conteos'], [0, 1, 1, 0, 0, 0, 1])
        self.assertEqual(dist['sobre_limite'][-1], True)
        tendencia = indicadores.tendencia(self.tramos, AYER, HOY, self.config)
        self.assertTrue(tendencia['diaria'])
        self.assertEqual(tendencia['promedio'], [130, None])
        llegada = indicadores.permanencia_segun_llegada(self.tramos, self.config)
        self.assertEqual(dict(zip(llegada['categorias'], llegada['promedio'])),
                         {servicios.ADELANTADO: None, servicios.PUNTUAL: 165, servicios.RETRASADO: 60})
        puntualidad = indicadores.puntualidad_por(self.tramos, self.config, 'cd')
        self.assertEqual(puntualidad['RETRASADO'], [33.3])

    def test_conclusiones_solo_con_datos_suficientes(self):
        mapa = indicadores.mapa_de_calor(self.tramos, self.config)
        k = indicadores.kpis(self.tramos, self.programaciones, self.config, HOY)
        frases = indicadores.conclusiones(k, {}, mapa, indicadores.rankings(self.tramos, self.config),
                                          indicadores.comparativo_cd(self.tramos, self.config),
                                          indicadores.permanencia_segun_llegada(self.tramos, self.config), self.config)
        textos = ' '.join(f['texto'] for f in frases)
        self.assertIn('2 h 10 min', textos)            # permanencia promedio
        self.assertIn('hora pico', textos)
        self.assertNotIn('La placa', textos)           # ninguna placa tiene 3 tramos: no se concluye nada

    def test_variacion_frente_al_periodo_anterior(self):
        self.assertEqual(indicadores.variacion({'a': 50, 'b': None}, {'a': 40, 'b': 3}), {'a': 10, 'b': None})

    def test_dashboard_en_pantalla(self):
        for usuario in (self.consulta, self.vigilante):
            self.entrar(usuario)
            respuesta = self.client.get(reverse('accesos:dashboard'),
                                        {'desde': '2026-10-06', 'hasta': '2026-10-07', 'vista': 'planta'})
            self.assertEqual(respuesta.status_code, 200)
            self.assertContains(respuesta, 'OTR-001')
            self.assertContains(respuesta, 'Mapa de calor')
            self.assertEqual(len(respuesta.context['tarjetas']), 6)
        # Filtros inválidos no rompen la vista.
        self.assertEqual(self.client.get(reverse('accesos:dashboard'), {'desde': 'x', 'cd': 'abc', 'tipo': 'Z'}).status_code, 200)

    def test_filtro_demoras_en_tramos(self):
        self.entrar(self.consulta)
        respuesta = self.client.get(reverse('accesos:tramos_lista'),
                                    {'desde': '2026-10-06', 'hasta': '2026-10-06', 'estado': 'demoras'})
        self.assertEqual(respuesta.context['total'], 1)


class ConsultaYPaseTests(PorteriaBase):

    def setUp(self):
        super().setUp()
        self.cerrada = self.programar(8, 30)
        tramo = self.entrar_en(self.cerrada)
        self.mover_reloj(HOY, 9, 15)
        self.salir(tramo)
        self.pendiente = self.programar(11, 0, transporte=Transporte.objects.create(
            placa='PEN-002', tamano=self.tamano, creado_por=self.admin_accesos))

    def test_programacion_con_entrada_y_salida_queda_cerrada_con_sus_tiempos(self):
        self.cerrada.refresh_from_db()
        self.assertEqual(self.cerrada.estado, 'CERRADA')
        self.entrar(self.vigilante)
        pagina = self.client.get(reverse('accesos:porteria_programaciones'))
        fila = next(p for p in pagina.context['programaciones'] if p.pk == self.cerrada.pk)
        self.assertEqual((fila.estado_codigo, fila.permanencia), ('CERRADA', '15 min'))  # entró 09:00, salió 09:15
        self.assertContains(pagina, 'Cerrada')

    def test_vigilancia_filtra_por_fecha_placa_conductor_y_estado(self):
        self.entrar(self.vigilante)
        url = reverse('accesos:porteria_programaciones')
        placas = lambda params: [p.transporte.placa for p in self.client.get(url, params).context['programaciones']]
        self.assertEqual(placas({'estado': 'CERRADA'}), ['F7J-011'])
        self.assertEqual(placas({'estado': 'PENDIENTE'}), ['PEN-002'])
        self.assertEqual(placas({'q': 'pen002'}), ['PEN-002'])
        self.assertEqual(placas({'conductor': 'pérez'}), ['F7J-011', 'PEN-002'])
        self.assertEqual(placas({'conductor': '12345678', 'estado': 'CERRADA'}), ['F7J-011'])
        self.assertEqual(placas({'fecha': '2026-10-08'}), [])

    def test_vigilancia_solo_ve_su_cd(self):
        self.programar(10, 0, cd=self.punta_negra, transporte=Transporte.objects.create(
            placa='PNG-001', tamano=self.tamano, creado_por=self.admin_accesos))
        self.entrar(self.vigilante)
        placas = [p.transporte.placa for p in self.client.get(reverse('accesos:porteria_programaciones'),
                                                              {'cd': self.punta_negra.pk}).context['programaciones']]
        self.assertNotIn('PNG-001', placas)

    def test_pase_con_qr_para_cualquier_vehiculo(self):
        for usuario in (self.despacho, self.vigilante, self.consulta):
            self.entrar(usuario)
            pase = self.client.get(reverse('accesos:programacion_pase', args=[self.pendiente.pk]))
            self.assertEqual(pase.status_code, 200)
            self.assertContains(pase, '<svg')
            self.assertContains(pase, 'PEN002')  # contenido del QR = placa normalizada
        self.entrar(self.despacho)
        detalle = self.client.get(reverse('accesos:programacion_detalle', args=[self.pendiente.pk]))
        self.assertContains(detalle, 'QR de la placa')

    def test_agenda_muestra_tiempos_y_filtra_por_estado(self):
        self.entrar(self.despacho)
        agenda = self.client.get(reverse('accesos:programacion_agenda'), {'estado': 'CERRADA'})
        self.assertEqual([p.pk for p in agenda.context['programaciones']], [self.cerrada.pk])
        self.assertContains(agenda, '15 min')


class FiltrosCruzadosTests(DatosDelMartes):
    """Cualquier filtro se aplica a todos los paneles a la vez (mismos datos de IndicadoresTests)."""

    def dashboard(self, **params):
        self.entrar(self.consulta)
        return self.client.get(reverse('accesos:dashboard'), params)

    def test_por_defecto_muestra_el_dia_de_hoy(self):
        respuesta = self.dashboard()
        self.assertTrue(respuesta.context['un_dia'])
        self.assertEqual((respuesta.context['desde'], respuesta.context['hasta']), (HOY, HOY))
        self.assertEqual(respuesta.context['datos']['tendencia']['unidad'], 'hora')
        ayer = self.dashboard(desde='2026-10-06', hasta='2026-10-06')
        self.assertEqual(ayer.context['kpis']['tramos'], 3)
        self.assertEqual(ayer.context['datos']['tendencia']['etiquetas'], ['08:00', '09:00', '10:00'])

    def test_filtro_por_placa_afecta_a_todos_los_paneles(self):
        respuesta = self.dashboard(desde='2026-10-06', hasta='2026-10-06', placa='f7j-011')
        contexto = respuesta.context
        self.assertEqual(contexto['kpis']['tramos'], 2)
        self.assertEqual([f['nombre'] for f in contexto['datos']['rankings']['placa']], ['F7J-011'])
        self.assertEqual(sum(contexto['datos']['distribucion']['conteos']), 2)
        self.assertEqual(contexto['kpis']['citas_programadas'], 3)  # 2 de F7J-011 + la cita de las 16:00 (también F7J-011)
        self.assertEqual([c['texto'] for c in contexto['chips']], ['Placa F7J-011'])
        self.assertNotIn('placa=', contexto['chips'][0]['quitar'])

    def test_filtros_por_conductor_dia_hora_llegada_y_rango(self):
        base = {'desde': '2026-10-06', 'hasta': '2026-10-06'}
        self.assertEqual(self.dashboard(**base, conductor=self.luna.pk).context['kpis']['tramos'], 1)
        self.assertEqual(self.dashboard(**base, dia=AYER.weekday(), hora=10).context['kpis']['tramos'], 1)
        self.assertEqual(self.dashboard(**base, dia=0).context['kpis']['tramos'], 0)  # lunes: nada
        self.assertEqual(self.dashboard(**base, llegada='RETRASADO').context['kpis']['tramos'], 1)
        self.assertEqual(self.dashboard(**base, rango=6).context['kpis']['tramos'], 1)  # más de 240 min
        combinado = self.dashboard(**base, placa='F7J011', llegada='PUNTUAL').context
        self.assertEqual(combinado['kpis']['tramos'], 1)
        self.assertEqual(len(combinado['chips']), 2)

    def test_valores_invalidos_se_ignoran(self):
        respuesta = self.dashboard(desde='2026-10-06', hasta='2026-10-06', dia='9', hora='x', rango='99',
                                   llegada='TARDE', conductor='-3', cd='abc')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['kpis']['tramos'], 3)
        self.assertEqual(respuesta.context['chips'], [])

    def test_ranking_entrega_el_valor_para_filtrar(self):
        filas = indicadores.ranking(self.tramos, self.config, 'placa')
        self.assertEqual({f['valor'] for f in filas}, {'F7J011', 'OTR001'})
        por_conductor = indicadores.ranking(self.tramos, self.config, 'conductor')
        self.assertIn(self.luna.pk, {f['valor'] for f in por_conductor})
