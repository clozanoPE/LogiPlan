"""
Hora de SALIDA en las rutas INTERNO, kilometraje del maestro de Transporte y trazabilidad de una placa por día
(14/10/2026). Decisiones del usuario: la hora programada de una ruta INTERNO es la de salida del CD; con 15 min
de tolerancia, 15 min de demora ya es retraso; los minutos fuera de horario de la Post-Ruta se cuentan sobre las
16:15. La primera ruta del día la atiende la salida de la Pre-Ruta.
"""
import importlib
from datetime import time

from django.apps import apps
from django.urls import reverse

from .. import indicadores, indicadores_flota, jornadas, servicios, tiempo, trazabilidad
from ..models import Programacion, Tramo, UsuarioCD
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA

TRAZABILIDAD = reverse('accesos:trazabilidad')


class EjemploF5J011Base(JornadaBase):
    """El ejemplo del usuario: rutas de 08:00 y 11:15 (horas de salida); sale a las 07:50 (Pre-Ruta), entra a
    las 11:00 y sale a las 11:30; vuelve a las 16:50 (Post-Ruta)."""

    def armar_dia(self, retorno=(16, 50)):
        self.mover_reloj(HOY, 5, 0)
        self.ocho, self.once = self.interna(8, 0), self.interna(11, 15)
        self.mover_reloj(HOY, 7, 50)
        self.pre = self.salida_sin_registro(km=130000)
        self.mover_reloj(HOY, 11, 0)
        self.visita = self.entrar_en(self.once, km=130062)
        self.mover_reloj(HOY, 11, 30)
        self.visita = self.salida(self.visita)
        self.mover_reloj(HOY, *retorno)
        self.post, _ = self.post_ruta(km=130151)


class EjemploDelUsuarioTests(EjemploF5J011Base):

    def test_lectura_de_cada_tramo(self):
        self.armar_dia()
        # 1. La salida de la Pre-Ruta (07:50) atiende la ruta de las 08:00: 10 min antes, a tiempo.
        self.ocho.refresh_from_db()
        self.assertEqual((self.pre.programacion, self.ocho.estado), (self.ocho, Programacion.Estado.CERRADA))
        self.assertEqual(self.pre.desviacion_salida_min, -10)
        self.assertEqual(servicios.puntualidad(self.pre.desviacion_min, 15), servicios.PUNTUAL)
        # 2. La ruta de las 11:15 sale a las 11:30: 15 min de demora = retraso (decisión b).
        self.assertEqual(self.visita.desviacion_salida_min, 15)
        self.assertEqual(servicios.puntualidad(self.visita.desviacion_min, 15), servicios.RETRASADO)
        # 3. La Post-Ruta de las 16:50 queda fuera de horario: 35 min sobre las 16:15 (decisión c).
        jornada = self.jornada()
        self.assertEqual(jornadas.calificacion_cierre(jornada), jornadas.FUERA_DE_HORARIO)
        self.assertEqual(jornadas.minutos_sobre_cierre(jornada), 35)
        # Ninguna ruta quedó «No atendida».
        self.assertFalse(Programacion.objects.filter(no_atendida_por_cierre=True).exists())

    def test_trazabilidad_del_dia(self):
        self.armar_dia()
        datos = trazabilidad.del_dia(self.f7j, HOY, tiempo.ahora(), self.config)
        eventos = [(e.hora, e.tipo, e.etiqueta) for e in datos['eventos']]
        self.assertEqual(eventos, [('06:30', 'pre_inicio', ''), ('07:50', 'salida_ruta', 'A tiempo'),
                                   ('11:00', 'entrada', ''), ('11:30', 'salida_ruta', 'Retraso'),
                                   ('16:50', 'post_ruta', 'Fuera de horario')])
        salida_1, salida_2, post = datos['eventos'][1], datos['eventos'][3], datos['eventos'][4]
        self.assertEqual((salida_1.programada, salida_1.lectura, salida_1.km), (time(8, 0), '10 min antes', 130000))
        self.assertEqual((salida_2.programada, salida_2.lectura), (time(11, 15), '15 min tarde'))
        self.assertEqual((post.desviacion, post.lectura), (35, '35 min tarde (sobre las 16:15)'))
        r = datos['resumen']
        self.assertEqual((r['programadas'], r['atendidas'], r['no_atendidas']), (2, 2, 0))
        self.assertEqual((r['salidas'], r['salidas_a_tiempo'], r['salidas_retrasadas'], r['retraso_total']), (2, 1, 1, 15))
        self.assertEqual((r['km_inicial'], r['km_final'], r['km_recorridos']), (130000, 130151, 151))
        self.assertEqual((r['pre_ruta_min'], r['minutos_fuera']), (80, 35))

    def test_trazabilidad_muestra_lo_no_atendido_y_la_pernocta_del_dia_siguiente(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        quince = self.interna(15, 0)
        self.mover_reloj(HOY, 7, 40)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 13, 0)
        self.post_ruta()
        self.mover_reloj(MANANA, 5, 0)
        self.interna(9, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 30)
        self.salida(Tramo.objects.get(transporte=self.f7j, abierto=True))  # fin de la pernocta a las 06:30
        datos = trazabilidad.del_dia(self.f7j, HOY, tiempo.ahora(), self.config)
        sin_atender = [e for e in datos['eventos'] if e.tipo == 'programada']
        self.assertEqual([(e.programacion, e.etiqueta) for e in sin_atender], [(quince, 'No atendida (cierre de jornada)')])
        ultimo = datos['eventos'][-1]
        self.assertEqual((ultimo.tipo, ultimo.otro_dia), ('fin_pernocta', True))
        self.assertEqual(datos['resumen']['no_atendidas'], 1)

    def test_indicadores_del_dashboard(self):
        self.armar_dia()
        self.mover_reloj(HOY, 18, 0)
        lista = indicadores_flota.jornadas_del_periodo(HOY, HOY, {})
        k = indicadores_flota.kpis(lista, tiempo.ahora())
        self.assertEqual((k['salidas'], k['salidas_retrasadas'], k['pct_salidas_a_tiempo'], k['retraso_salida_promedio']),
                         (2, 1, 50.0, 15))
        self.assertEqual(k['minutos_sobre_cierre'], 35)
        self.assertEqual(indicadores_flota.ranking(lista, 'conductor')[0]['pct_salidas_a_tiempo'], 50.0)
        # Planta: la visita INTERNO se califica con su salida (15 min tarde = retraso).
        tramos = indicadores.tramos_del_periodo(HOY, HOY, {}, self.config)
        self.assertEqual([t.pk for t in tramos], [self.visita.pk])
        self.assertEqual(indicadores.kpis(tramos, [], self.config, HOY)['pct_puntual'], 0.0)
        # «¿Llegar tarde alarga la estadía?» solo cuenta clientes y proveedores.
        self.assertEqual(sum(indicadores.permanencia_segun_llegada(tramos, self.config)['tramos']), 0)
        segmentos = indicadores_flota.gantt(HOY, tiempo.ahora(), {})['filas'][0]['segmentos']
        self.assertIn('Sale a la ruta de las 08:00: 10 min antes', segmentos[0]['ruta'])
        self.assertIn('(retraso)', segmentos[1]['ruta'])


class PuntualidadTests(JornadaBase):

    def test_desde_la_tolerancia_exacta_es_retraso(self):
        for desviacion, esperado in ((-16, servicios.ADELANTADO), (-15, servicios.PUNTUAL), (0, servicios.PUNTUAL),
                                     (14, servicios.PUNTUAL), (15, servicios.RETRASADO), (40, servicios.RETRASADO),
                                     (None, None)):
            with self.subTest(desviacion=desviacion):
                self.assertEqual(servicios.puntualidad(desviacion, 15), esperado)

    def test_clientes_y_proveedores_siguen_calificando_la_llegada(self):
        cita = self.programar(10, 0, transporte=self.externo)
        self.mover_reloj(HOY, 10, 20)
        tramo = self.entrar_en(cita)
        self.mover_reloj(HOY, 11, 0)
        tramo = self.salir(tramo)
        self.assertEqual((tramo.desviacion_llegada_min, tramo.desviacion_salida_min, tramo.desviacion_min), (20, None, 20))
        self.assertFalse(tramo.califica_salida)


class PrimeraRutaTests(JornadaBase):

    def test_al_ingresar_desde_afuera_la_salida_atiende_la_primera_ruta(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 6, 50)
        pre, _ = servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=131000, obs='',
                                                    conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        self.mover_reloj(HOY, 8, 20)
        resultado = self.evaluar()
        self.assertEqual((resultado.tipo_salida, resultado.ruta_salida), (servicios.SALIDA_PRE_RUTA, ocho))
        pre = self.salida(pre)
        self.assertEqual((pre.programacion, pre.desviacion_salida_min), (ocho, 20))

    def test_anular_la_pre_ruta_devuelve_la_ruta_a_pendiente(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        pre = self.salida_sin_registro()
        servicios.anular_tramo(pre, self.admin_accesos, motivo='Registro por error')
        ocho.refresh_from_db()
        self.assertEqual(ocho.estado, Programacion.Estado.PROGRAMADA)

    def test_cierre_administrativo_de_la_pre_ruta_atiende_la_primera_ruta(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 6, 40)
        pre, _ = servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=131000, obs='',
                                                    conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        self.mover_reloj(HOY, 10, 0)
        servicios.cierre_administrativo(pre, self.admin_accesos, salida_en=tiempo.combinar(HOY, time(8, 5)),
                                        motivo='Vigilancia no registró la salida', ahora=tiempo.ahora())
        pre.refresh_from_db()
        self.assertEqual((pre.programacion, pre.desviacion_salida_min), (ocho, 5))

    def test_sin_ruta_en_ese_cd_no_se_vincula_ninguna(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0, cd=self.punta_negra)
        self.mover_reloj(HOY, 7, 50)
        pre = self.salida_sin_registro()  # sale de Lurín; su ruta es de Punta Negra
        self.assertIsNone(pre.programacion)

    def test_corregir_la_salida_de_la_pre_ruta_recalcula_la_desviacion(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        pre = self.salida_sin_registro()
        self.mover_reloj(HOY, 12, 0)
        servicios.corregir_tramo(pre, self.admin_accesos, motivo='Hora mal registrada', ahora=tiempo.ahora(),
                                 entrada_en=pre.entrada_en, salida_en=tiempo.combinar(HOY, time(8, 25)),
                                 km_entrada=pre.km_entrada, obs_entrada='', obs_salida='', programacion=None,
                                 conductor=self.conductor)
        pre.refresh_from_db()
        self.assertEqual((pre.programacion, pre.desviacion_salida_min, pre.desviacion_llegada_min), (ocho, 25, None))


class KilometrajeTests(JornadaBase):

    def test_esta_saliendo_exige_kilometraje_y_actualiza_el_maestro(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        with self.assertRaises(Exception):
            servicios.registrar_salida_sin_registro(transporte_id=self.f7j.pk, cd=self.lurin, km=None, obs='',
                                                    conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        pre = self.salida_sin_registro(km=150000)
        self.f7j.refresh_from_db()
        self.assertEqual((self.f7j.km_actual, self.f7j.km_actualizado_en), (150000, pre.salida_en))
        self.assertIn('Salida a ruta (Pre-Ruta)', self.f7j.km_origen)

    def test_otro_conductor_al_salir(self):
        from ..models import Conductor
        otro = Conductor.objects.create(tipo_documento=self.dni, numero_documento='87654321', apellido_paterno='RÍOS',
                                        nombres='ANA', creado_por=self.admin_accesos)
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        self.assertEqual(self.salida_sin_registro(conductor_id=otro.pk).conductor, otro)

    def test_el_maestro_sigue_a_entradas_correcciones_y_anulaciones(self):
        cita = self.programar(10, 0, transporte=self.externo)
        self.mover_reloj(HOY, 10, 0)
        tramo = self.entrar_en(cita, km=200000)
        self.externo.refresh_from_db()
        self.assertEqual((self.externo.km_actual, self.externo.km_origen), (200000, 'Entrada al CD · CD Lurín'))
        self.mover_reloj(HOY, 10, 30)
        tramo = self.salir(tramo)
        servicios.corregir_tramo(tramo, self.admin_accesos, motivo='Error de digitación', ahora=tiempo.ahora(),
                                 entrada_en=tramo.entrada_en, salida_en=tramo.salida_en, km_entrada=200500,
                                 obs_entrada='', obs_salida='', programacion=cita, conductor=self.conductor)
        self.externo.refresh_from_db()
        self.assertEqual(self.externo.km_actual, 200500)
        servicios.anular_tramo(tramo, self.admin_accesos, motivo='No correspondía')
        self.externo.refresh_from_db()
        self.assertIsNone(self.externo.km_actual)
        # No cambia los datos de modificación del maestro (no es una edición del usuario).
        self.assertIsNone(self.externo.modificado_por)

    def test_la_pantalla_propone_el_ultimo_kilometraje(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        self.entrar(self.vigilante)
        pagina = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J-011'})
        self.assertContains(pagina, 'no hay uno registrado')  # sin lecturas previas: el campo aparece vacío
        type(self.f7j).objects.filter(pk=self.f7j.pk).update(km_actual=123456)
        pagina = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J-011'})
        self.assertContains(pagina, 'value="123456"')
        self.assertContains(pagina, 'Último registrado: 123456 km')

    def test_la_lista_de_transportes_muestra_el_ultimo_kilometraje(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        self.salida_sin_registro(km=150123)
        self.entrar(self.despacho)
        pagina = self.client.get(reverse('accesos:transportes_lista'))
        self.assertContains(pagina, 'Último km')
        self.assertContains(pagina, '150123')


class PantallaTrazabilidadTests(EjemploF5J011Base):

    def test_permisos_y_contenido(self):
        self.armar_dia()
        parametros = {'placa': 'f7j-011', 'fecha': HOY.isoformat()}  # minúsculas y guion: se normaliza
        for usuario, codigo in ((self.admin_accesos, 200), (self.despacho, 200), (self.consulta, 200),
                                (self.vigilante, 200), (self.comercial, 403), (self.sin_grupo, 403)):
            with self.subTest(usuario=usuario.username):
                self.entrar(usuario)
                self.assertEqual(self.client.get(TRAZABILIDAD, parametros).status_code, codigo)
        self.entrar(self.despacho)
        pagina = self.client.get(TRAZABILIDAD, parametros)
        for texto in ('Salida a ruta · ruta de las 08:00', '10 min antes', '15 min tarde', 'Fuera de horario',
                      '35 min sobre las 16:15', '151 km', 'No es un registro de asistencia'):
            self.assertContains(pagina, texto)
        self.assertContains(self.client.get(reverse('accesos:inicio')), 'Trazabilidad')  # en el menú

    def test_vigilancia_solo_ve_sus_cd(self):
        self.armar_dia()
        UsuarioCD.objects.filter(usuario=self.vigilante).update(cd=self.punta_negra)
        self.entrar(self.vigilante)
        pagina = self.client.get(TRAZABILIDAD, {'placa': 'F7J-011', 'fecha': HOY.isoformat()})
        self.assertContains(pagina, 'Sin movimientos ni programaciones')

    def test_placa_desconocida_y_exportacion(self):
        self.armar_dia()
        self.entrar(self.consulta)
        self.assertContains(self.client.get(TRAZABILIDAD, {'placa': 'ZZZ-999'}), 'no está registrada')
        self.assertContains(self.client.get(TRAZABILIDAD), 'Escriba una placa')
        respuesta = self.client.get(reverse('accesos:trazabilidad_exportar'), {'placa': 'F7J-011', 'fecha': HOY.isoformat()})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('Trazabilidad_F7J011_20261007.xlsx', respuesta['Content-Disposition'])

    def test_enlaces_desde_otras_pantallas(self):
        self.armar_dia()
        self.entrar(self.admin_accesos)
        url = f"{TRAZABILIDAD}?placa=F7J011&fecha=2026-10-07"
        self.assertContains(self.client.get(reverse('accesos:tramo_detalle', args=[self.pre.pk])), url)
        self.assertContains(self.client.get(reverse('accesos:programacion_detalle', args=[self.ocho.pk])), url)
        detalle = self.client.get(reverse('accesos:tramo_detalle', args=[self.visita.pk]))
        self.assertContains(detalle, 'Salida programada')
        self.assertContains(detalle, 'Retrasado')
        agenda = self.client.get(reverse('accesos:programacion_agenda'), {'fecha': HOY.isoformat()})
        self.assertContains(agenda, '15 min tarde · retraso')
        self.assertContains(agenda, 'Pre-Ruta')

    def test_salida_de_porteria_muestra_la_ruta_que_atiende(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 8, 20)
        self.entrar(self.vigilante)
        pagina = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J-011'})
        self.assertContains(pagina, 'Sale a la ruta de las')
        self.assertContains(pagina, ocho.codigo)
        self.assertContains(pagina, '20 min tarde')


class MigracionDeDatosTests(JornadaBase):
    """0014: las Pre-Rutas registradas antes del cambio pasan a atender su primera ruta y el maestro recibe el km."""

    def test_vincula_la_primera_ruta_y_calcula_el_kilometraje(self):
        self.mover_reloj(HOY, 5, 0)
        ocho = self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        pre = self.salida_sin_registro(km=140000)
        # Como estaba antes del cambio: sin ruta vinculada y la ruta pendiente (quedaría «No atendida»).
        Tramo.objects.filter(pk=pre.pk).update(programacion=None, desviacion_salida_min=None)
        Programacion.objects.filter(pk=ocho.pk).update(estado='PROGRAMADA', no_atendida_por_cierre=True)
        type(self.f7j).objects.filter(pk=self.f7j.pk).update(km_actual=None)
        migracion = importlib.import_module('accesos.migrations.0014_datos_km_salida')
        migracion.adelante(apps, None)
        migracion.adelante(apps, None)  # idempotente
        pre.refresh_from_db()
        ocho.refresh_from_db()
        self.f7j.refresh_from_db()
        self.assertEqual((pre.programacion, pre.desviacion_salida_min), (ocho, -10))
        self.assertEqual((ocho.estado, ocho.no_atendida_por_cierre), (Programacion.Estado.CERRADA, False))
        self.assertEqual(self.f7j.km_actual, 140000)
        migracion.atras(apps, None)
        pre.refresh_from_db()
        self.assertIsNone(pre.programacion)
