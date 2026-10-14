"""
Jornada de la flota propia (F6): Pre-Ruta automática, Post-Ruta, avería (no disponibilidad),
días no laborables y correcciones. Hora fija de las pruebas: miércoles 07/10/2026 (ver
test_programacion); el reloj se mueve con mover_reloj().

En las rutas INTERNO la hora programada es la de SALIDA del CD (14/10/2026): la primera ruta del
día la atiende la salida de la Pre-Ruta; las siguientes, una visita (entrada y salida).
"""
import threading
from datetime import date, time, timedelta, timezone as dt_timezone
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse

from .. import indicadores, jornadas, servicios, tiempo
from ..forms import ProgramacionForm
from ..models import (CentroDistribucion, ConfiguracionAccesos, Conductor, Feriado, Incidencia, Jornada, Programacion,
                      RegistroAuditoria, TamanoVehiculo, TipoDocumentoIdentidad, TipoIncidencia, Tramo, Transporte)
from .test_porteria import PorteriaBase
from .test_programacion import HOY, MANANA
from .utilidades import crear_usuario

AYER = date(2026, 10, 6)       # martes
DOMINGO = date(2026, 10, 11)


class JornadaBase(PorteriaBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.averia = TipoIncidencia.objects.get(nombre='Avería mecánica')

    def interna(self, hora, minuto=0, fecha=HOY, cd=None):
        return self.programar(hora, minuto, fecha=fecha, tipo='INTERNO', cd=cd)

    def evaluar(self, placa='F7J011', cd=None):
        return servicios.evaluar_porteria(placa, cd or self.lurin, tiempo.ahora(), self.config)

    def jornada(self, fecha=HOY):
        return Jornada.objects.get(transporte=self.f7j, fecha=fecha)

    def salida_sin_registro(self, obs='', km=129500, conductor_id=None):
        tramo, _ = servicios.registrar_salida_sin_registro(transporte_id=self.f7j.pk, cd=self.lurin, km=km, obs=obs,
                                                           conductor_id=conductor_id, usuario=self.vigilante,
                                                           ahora=tiempo.ahora())
        return tramo

    def salida(self, tramo, obs=''):
        return servicios.registrar_salida(transporte_id=tramo.transporte_id, tramo_id=tramo.pk, cd=tramo.cd, obs=obs,
                                          usuario=self.vigilante, ahora=tiempo.ahora())

    def post_ruta(self, km=130000, **extra):
        tramo, avisos = servicios.registrar_post_ruta(transporte_id=self.f7j.pk, cd=self.lurin, km=km, obs='',
                                                      conductor_id=None, usuario=self.vigilante,
                                                      ahora=tiempo.ahora(), **extra)
        return tramo, avisos

    def abierto(self):
        return Tramo.objects.get(transporte=self.f7j, abierto=True)

    def dia_completo(self, fecha, retorno=(16, 10)):
        """La unidad hace su jornada en `fecha`: primera ruta a las 08:00 (la atiende la salida de la Pre-Ruta),
        segunda con salida a las 10:40 (entra 10:00) y Post-Ruta a la hora `retorno`. Si no tiene un tramo
        abierto (no durmió registrada en el CD), su primera salida no tiene registro."""
        self.mover_reloj(fecha, 5, 0)
        self.interna(8, 0, fecha=fecha)
        programacion = self.interna(10, 40, fecha=fecha)
        self.mover_reloj(fecha, 7, 30)
        if Tramo.objects.filter(transporte=self.f7j, abierto=True).exists():
            self.salida(self.abierto())
        else:
            self.salida_sin_registro()
        self.mover_reloj(fecha, 10, 0)
        visita = self.entrar_en(programacion)
        self.mover_reloj(fecha, 10, 40)
        self.salida(visita)
        self.mover_reloj(fecha, *retorno)
        return self.post_ruta()[0]


class SincronizacionTests(JornadaBase):

    def test_la_primera_ruta_interna_crea_la_jornada_una_sola_vez(self):
        self.mover_reloj(HOY, 5, 0)
        primera = self.interna(11, 0)
        self.interna(14, 0)
        jornada = self.jornada()
        self.assertEqual(Jornada.objects.count(), 1)
        self.assertEqual((jornada.estado, jornada.hora_inicio, jornada.hora_cierre, jornada.cd),
                         (Jornada.Estado.PLANIFICADA, time(6, 30), time(16, 0), self.lurin))
        self.assertFalse(jornada.programada_tarde)
        registro = RegistroAuditoria.objects.get(entidad='Jornada', accion='CREAR')
        self.assertIsNone(registro.usuario)  # la crea el sistema
        self.assertIn(primera.codigo, registro.motivo)

    def test_clientes_y_proveedores_no_tienen_jornada(self):
        self.programar(11, 0)
        self.programar(12, 0, transporte=self.externo, tipo='PROVEEDOR')
        self.assertFalse(Jornada.objects.exists())

    def test_programada_despues_de_la_pre_ruta_queda_marcada(self):
        self.interna(11, 0)  # son las 09:00
        jornada = self.jornada()
        self.assertTrue(jornada.programada_tarde)
        self.assertEqual(jornada.hora_inicio, time(6, 30))

    def test_cancelar_todas_anula_y_volver_a_programar_reactiva(self):
        uno, dos = self.interna(11, 0), self.interna(14, 0)
        servicios.cancelar(uno, self.despacho, 'Prueba', tiempo.ahora())
        self.assertEqual(self.jornada().estado, Jornada.Estado.PLANIFICADA)
        servicios.cancelar(dos, self.despacho, 'Prueba', tiempo.ahora())
        self.assertEqual(self.jornada().estado, Jornada.Estado.ANULADA)
        self.assertIsNone(jornadas.jornada_vigente(self.f7j.pk, HOY))
        self.interna(15, 0)
        self.assertEqual(self.jornada().estado, Jornada.Estado.PLANIFICADA)
        self.assertEqual(Jornada.objects.count(), 1)

    def test_cambiar_la_fecha_mueve_la_jornada(self):
        programacion = self.interna(11, 0, fecha=MANANA)
        programacion.fecha = date(2026, 10, 9)
        programacion.save()
        self.assertEqual(self.jornada(MANANA).estado, Jornada.Estado.ANULADA)
        self.assertEqual(self.jornada(date(2026, 10, 9)).estado, Jornada.Estado.PLANIFICADA)

    def test_cambio_de_configuracion_solo_rige_para_jornadas_nuevas(self):
        self.interna(11, 0, fecha=MANANA)
        config = ConfiguracionAccesos.obtener()
        config.hora_pre_ruta = time(6, 0)
        config.save()
        self.interna(11, 0, fecha=date(2026, 10, 9))
        self.assertEqual(self.jornada(MANANA).hora_inicio, time(6, 30))
        self.assertEqual(self.jornada(date(2026, 10, 9)).hora_inicio, time(6, 0))

    def test_aviso_si_la_ruta_sale_antes_de_la_pre_ruta(self):
        temprana = self.interna(6, 0, fecha=MANANA)
        self.assertTrue(any('antes del inicio de la Pre-Ruta' in a for a in servicios.avisos(temprana, self.config)))
        # La hora de una ruta INTERNO es la de salida: a las 07:00 la atiende la salida de la Pre-Ruta.
        normal = self.interna(7, 0, fecha=MANANA)
        self.assertFalse(any('Pre-Ruta' in a for a in servicios.avisos(normal, self.config)))


class DiaDeLaUnidadTests(JornadaBase):
    """El ejemplo de la F7K-099: Pre-Ruta 06:30–07:30 (sale a la ruta de las 08:00), visitas 11:00–11:50 y
    14:00–15:45 (rutas con salida programada a las 11:50 y 14:45), Post-Ruta 16:10."""

    def test_ejemplo_completo_y_reinicio_al_dia_siguiente(self):
        self.mover_reloj(HOY, 5, 0)
        ocho, once, catorce = self.interna(8, 0), self.interna(11, 50), self.interna(14, 45)

        self.mover_reloj(HOY, 7, 30)  # primera vez en el sistema: durmió en el CD sin registro previo
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.ruta_salida), (servicios.INICIO_JORNADA, ocho))
        pre = self.salida_sin_registro()
        self.assertEqual((pre.clase, pre.minutos_permanencia, pre.vigilante_entrada), (Tramo.Clase.PRE_RUTA, 60, None))
        self.assertEqual(tiempo.a_lima(pre.entrada_en).time(), time(6, 30))
        # La salida a ruta atiende la primera ruta: sale 30 min antes de su hora programada.
        ocho.refresh_from_db()
        self.assertEqual((pre.programacion, ocho.estado, pre.desviacion_salida_min, pre.desviacion_llegada_min),
                         (ocho, Programacion.Estado.CERRADA, -30, None))
        self.assertEqual(pre.km_entrada, 129500)
        jornada = self.jornada()
        self.assertEqual((jornada.estado, jornadas.minutos_pre_ruta(jornada)), (Jornada.Estado.EN_RUTA, 60))
        self.assertTrue(jornadas.pre_ruta_a_tiempo(jornada))

        self.mover_reloj(HOY, 11, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.sugerida.programacion), (servicios.ENTRADA, once))
        self.assertEqual([c.programacion for c in resultado.candidatas], [once, catorce])  # la de 08:00 ya no
        self.assertTrue(resultado.permite_post_ruta)
        visita = self.entrar_en(once)
        self.assertEqual(visita.jornada, jornada)
        self.mover_reloj(HOY, 11, 50)
        visita = self.salida(visita)
        self.assertEqual(visita.desviacion_salida_min, 0)
        self.mover_reloj(HOY, 14, 0)
        visita = self.entrar_en(catorce)
        self.mover_reloj(HOY, 15, 45)
        visita = self.salida(visita)
        self.assertEqual(visita.desviacion_salida_min, 60)  # salió una hora después de lo programado

        self.mover_reloj(HOY, 16, 10)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.pendientes), (servicios.POST_RUTA, []))
        post, _ = self.post_ruta()
        self.assertTrue(post.esta_abierto)  # duerme en el CD
        jornada.refresh_from_db()
        self.assertEqual(jornada.estado, Jornada.Estado.CERRADA)
        self.assertEqual(jornadas.calificacion_cierre(jornada), jornadas.EN_HORARIO)
        self.assertEqual(jornadas.minutos_sobre_cierre(jornada), 0)
        self.assertEqual(Tramo.objects.filter(jornada=jornada).count(), 4)
        # Ninguna ruta quedó «No atendida»: las tres se cumplieron.
        self.assertFalse(Programacion.objects.filter(no_atendida_por_cierre=True).exists())
        # Los indicadores de visitas solo cuentan las dos visitas programadas (la Pre-Ruta se mide aparte).
        self.assertEqual(len(indicadores.tramos_del_periodo(HOY, HOY, {})), 2)

        # Al día siguiente: la pernocta se cierra a las 06:30 y empieza la Pre-Ruta.
        self.mover_reloj(MANANA, 5, 0)
        diez = self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 20)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.tipo_salida, resultado.ruta_salida),
                         (servicios.SALIDA, servicios.SALIDA_PRE_RUTA_AUTOMATICA, diez))
        pre = self.salida(post)
        post.refresh_from_db()
        self.assertEqual(tiempo.a_lima(post.salida_en).time(), time(6, 30))
        self.assertTrue(post.cierre_automatico)
        self.assertEqual((pre.clase, pre.minutos_permanencia, pre.km_entrada), (Tramo.Clase.PRE_RUTA, 50, 130000))
        self.assertEqual((pre.programacion, pre.desviacion_salida_min), (diez, -160))
        self.assertEqual(self.jornada(MANANA).estado, Jornada.Estado.EN_RUTA)

    def test_calificacion_del_retorno(self):
        for retorno, esperado, minutos in (((15, 30), jornadas.ANTICIPADO, 0), ((16, 0), jornadas.EN_HORARIO, 0),
                                           ((16, 15), jornadas.EN_HORARIO, 0),
                                           ((16, 40), jornadas.FUERA_DE_HORARIO, 25)):  # sobre las 16:15
            with self.subTest(retorno=retorno):
                Tramo.objects.filter(transporte=self.f7j).update(abierto=None)
                fecha = HOY + timedelta(days=[(15, 30), (16, 0), (16, 15), (16, 40)].index(retorno))
                self.dia_completo(fecha, retorno=retorno)
                jornada = self.jornada(fecha)
                self.assertEqual(jornadas.calificacion_cierre(jornada), esperado)
                self.assertEqual(jornadas.minutos_sobre_cierre(jornada), minutos)

    def test_post_ruta_con_pendientes_las_deja_no_atendidas(self):
        self.mover_reloj(HOY, 5, 0)
        ocho, diez, quince = self.interna(8, 0), self.interna(10, 30), self.interna(15, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 10, 0)
        self.salida(self.entrar_en(diez))
        self.mover_reloj(HOY, 15, 50)
        resultado = self.evaluar()
        self.assertEqual(resultado.accion, servicios.ENTRADA)  # la de 15:00 sigue como candidata
        self.assertTrue(resultado.permite_post_ruta)
        _, avisos = self.post_ruta()
        quince.refresh_from_db()
        self.assertTrue(quince.no_atendida_por_cierre)
        self.assertEqual(servicios.estado_visible(quince, HOY), servicios.NO_ATENDIDA)
        ocho.refresh_from_db()  # la atendió la salida de la Pre-Ruta: no queda «No atendida»
        self.assertEqual((ocho.estado, ocho.no_atendida_por_cierre), (Programacion.Estado.CERRADA, False))
        self.assertIsNotNone(servicios.motivo_no_editable(quince, tiempo.ahora()))
        self.assertTrue(any('No atendidas' in a for a in avisos))

    def test_pernocta_fuera_y_ingreso_al_dia_siguiente(self):
        self.dia_completo(HOY)
        self.mover_reloj(HOY, 18, 0)
        resultado = self.evaluar()
        self.assertEqual(resultado.tipo_salida, servicios.SALIDA_TRAS_CIERRE)
        with self.assertRaises(ValidationError):
            self.salida(self.abierto())  # sin motivo
        tramo = self.salida(self.abierto(), obs='Pernocta en domicilio del conductor')
        self.assertTrue(tramo.salida_tras_cierre)
        self.assertTrue(self.jornada().pernocto_fuera)

        self.mover_reloj(MANANA, 5, 0)
        self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 6, 50)
        self.assertEqual(self.evaluar().accion, servicios.INICIO_JORNADA)
        pre, _ = servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=131000, obs='',
                                                    conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        self.assertTrue(pre.esta_abierto)
        self.mover_reloj(MANANA, 7, 20)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_PRE_RUTA)
        self.salida(pre)
        jornada = self.jornada(MANANA)
        self.assertEqual((jornada.estado, jornadas.minutos_pre_ruta(jornada), jornada.ingreso_desde_afuera),
                         (Jornada.Estado.EN_RUTA, 30, True))

    def test_salida_antes_de_la_pre_ruta(self):
        self.dia_completo(HOY)
        self.mover_reloj(MANANA, 5, 0)
        self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 6, 0)
        pre = self.salida(self.abierto())
        jornada = self.jornada(MANANA)
        self.assertTrue(jornada.salida_anticipada)
        self.assertEqual(pre.minutos_permanencia, 0)

    def test_estados_visibles_durante_el_dia(self):
        self.dia_completo(HOY)
        self.mover_reloj(MANANA, 5, 0)
        self.interna(10, 0, fecha=MANANA)

        def estado(hora, minuto=0, reloj=MANANA):
            self.mover_reloj(reloj, hora, minuto)
            return jornadas.jornadas_del_dia(MANANA, tiempo.ahora())[0].estado_v.codigo

        self.assertEqual(estado(6, 0), 'POR_INICIAR')
        self.assertEqual(estado(6, 45), 'EN_PRE_RUTA')
        self.assertEqual(estado(7, 31), 'PRE_RUTA_EXCEDIDA')
        self.salida(self.abierto())
        self.assertEqual(estado(12, 0), 'EN_RUTA')
        self.assertEqual(estado(16, 20), 'SIN_RETORNO')
        self.assertEqual(estado(8, 0, reloj=date(2026, 10, 9)), 'SIN_POST_RUTA')

    def test_primera_salida_que_cierra_una_visita_de_otro_dia(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        programacion = self.interna(10, 30)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 10, 0)
        self.entrar_en(programacion)  # se queda a dormir sin Post-Ruta
        self.mover_reloj(MANANA, 5, 0)
        self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 30)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_SIN_PRE_RUTA)
        self.salida(self.abierto())
        self.assertTrue(self.jornada(MANANA).salida_sin_pre_ruta)
        self.assertEqual(self.jornada(HOY).estado, Jornada.Estado.EN_RUTA)  # quedó sin Post-Ruta


class AveriaTests(JornadaBase):

    def en_ruta_con_pendiente(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)  # la atiende la salida de la Pre-Ruta
        pendiente = self.interna(14, 0)
        futura = self.interna(10, 0, fecha=MANANA)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        return pendiente, futura

    def test_ingreso_por_averia_cierra_la_jornada_y_deja_no_disponible(self):
        pendiente, futura = self.en_ruta_con_pendiente()
        self.mover_reloj(HOY, 12, 0)
        resultado = self.evaluar()
        self.assertTrue(resultado.permite_averia)
        tramo, avisos = self.post_ruta(averia_tipo_id=self.averia.pk, averia_descripcion='Falla de frenos')
        incidencia = Incidencia.objects.get()
        self.assertEqual((incidencia.origen, incidencia.esta_abierta), (Incidencia.Origen.PORTERIA, True))
        jornada = self.jornada()
        self.assertEqual((jornada.motivo_cierre, jornada.incidencia), (Jornada.MotivoCierre.AVERIA, incidencia))
        self.assertEqual(jornadas.calificacion_cierre(jornada), jornadas.POR_AVERIA)
        pendiente.refresh_from_db()
        self.assertEqual((pendiente.estado, pendiente.incidencia), (Programacion.Estado.CANCELADA, incidencia))
        futura.refresh_from_db()
        self.assertEqual(futura.estado, Programacion.Estado.PROGRAMADA)  # no se cancela sola
        self.assertEqual(list(jornadas.programaciones_afectadas(HOY)), [futura])
        self.assertTrue(any('días siguientes' in a for a in avisos))
        # No aparece para programar.
        form = ProgramacionForm(ahora=tiempo.ahora())
        self.assertNotIn(self.f7j, form.fields['transporte'].queryset)

        # Al día siguiente sale al taller: pide motivo y no inicia la Pre-Ruta.
        self.mover_reloj(MANANA, 8, 0)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_NO_DISPONIBLE)
        with self.assertRaises(ValidationError):
            self.salida(tramo)
        self.salida(tramo, obs='Al taller')
        self.assertEqual(self.jornada(MANANA).estado, Jornada.Estado.PLANIFICADA)
        self.assertEqual(jornadas.jornadas_del_dia(MANANA, tiempo.ahora())[0].estado_v.codigo, 'NO_DISPONIBLE')

        jornadas.habilitar(incidencia, usuario=self.despacho, ahora=tiempo.ahora(), observacion='Reparado')
        incidencia.refresh_from_db()
        self.assertFalse(incidencia.esta_abierta)
        self.assertIn(self.f7j, ProgramacionForm(ahora=tiempo.ahora()).fields['transporte'].queryset)
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='Incidencia', accion='HABILITAR').exists())

    def test_averia_en_ruta_registrada_por_despacho_y_reingreso(self):
        pendiente, _ = self.en_ruta_con_pendiente()
        self.mover_reloj(HOY, 11, 0)
        incidencia, canceladas = jornadas.registrar_no_disponibilidad(
            transporte_id=self.f7j.pk, tipo_id=self.averia.pk, descripcion='Choque leve en ruta',
            usuario=self.despacho, ahora=tiempo.ahora())
        self.assertEqual(canceladas, [pendiente])
        jornada = self.jornada()
        self.assertEqual((jornada.estado, jornada.motivo_cierre), (Jornada.Estado.CERRADA, Jornada.MotivoCierre.AVERIA))
        self.assertIsNone(jornada.post_ruta_en)
        with self.assertRaises(ValidationError):  # una sola avería abierta por vehículo
            jornadas.registrar_no_disponibilidad(transporte_id=self.f7j.pk, tipo_id=self.averia.pk,
                                                 descripcion='Otra', usuario=self.despacho, ahora=tiempo.ahora())
        # Vuelve remolcado al CD: puede ingresar a pernoctar aunque no esté disponible.
        self.mover_reloj(HOY, 13, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.reingreso), (servicios.POST_RUTA, True))
        self.post_ruta()
        self.assertEqual(self.jornada().motivo_cierre, Jornada.MotivoCierre.AVERIA)

    def test_carga_masiva_rechaza_un_transporte_no_disponible(self):
        jornadas.registrar_no_disponibilidad(transporte_id=self.f7j.pk, tipo_id=self.averia.pk, descripcion='Taller',
                                             usuario=self.despacho, ahora=tiempo.ahora())
        form = ProgramacionForm(data=self.datos(tipo='INTERNO', transporte=self.f7j.pk, documento='',
                                                razon_social=''), ahora=tiempo.ahora())
        self.assertFalse(form.is_valid())
        self.assertIn('transporte', form.errors)


class DiasNoLaborablesTests(JornadaBase):

    def test_no_se_programa_en_domingo_ni_feriado(self):
        Feriado.objects.create(fecha=date(2026, 10, 9), descripcion='Feriado de prueba')
        for fecha, texto in ((DOMINGO, 'domingo'), (date(2026, 10, 9), 'Feriado de prueba')):
            with self.subTest(fecha=fecha):
                form = ProgramacionForm(data=self.datos(fecha=fecha.isoformat()), ahora=tiempo.ahora())
                self.assertFalse(form.is_valid())
                self.assertIn(texto, ' '.join(form.errors['fecha']))
        self.assertTrue(ProgramacionForm(data=self.datos(), ahora=tiempo.ahora()).is_valid())

    def test_editar_una_programacion_existente_en_un_dia_que_paso_a_feriado(self):
        programacion = self.crear()
        Feriado.objects.create(fecha=MANANA, descripcion='Nuevo feriado')
        form = ProgramacionForm(data=self.datos(motivo='Otro motivo'), instance=programacion, ahora=tiempo.ahora())
        self.assertTrue(form.is_valid(), form.errors)  # no cambió la fecha

    def test_movimiento_en_domingo_queda_marcado(self):
        self.dia_completo(date(2026, 10, 10))  # sábado
        self.mover_reloj(DOMINGO, 9, 0)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_TRAS_CIERRE)
        tramo = self.salida(self.abierto(), obs='Traslado autorizado')
        self.assertTrue(tramo.dia_no_laborable)
        self.assertEqual(jornadas.no_laborables_entre(date(2026, 10, 5), DOMINGO), {DOMINGO: 'Domingo'})


class CorreccionesDeJornadaTests(JornadaBase):

    def test_anular_la_post_ruta_reabre_la_jornada_y_sus_pendientes(self):
        self.mover_reloj(HOY, 5, 0)
        quince = self.interna(15, 0)
        self.interna(10, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 12, 0)
        post, _ = self.post_ruta()
        servicios.anular_tramo(post, self.admin_accesos, motivo='Era una visita, no el fin de jornada')
        jornada = self.jornada()
        self.assertEqual((jornada.estado, jornada.post_ruta_en), (Jornada.Estado.EN_RUTA, None))
        quince.refresh_from_db()
        self.assertFalse(quince.no_atendida_por_cierre)

    def test_corregir_la_post_ruta_actualiza_la_jornada(self):
        post = self.dia_completo(HOY, retorno=(16, 40))
        nueva = tiempo.combinar(HOY, time(16, 5))
        servicios.corregir_tramo(post, self.admin_accesos, motivo='Hora mal registrada', ahora=tiempo.ahora(),
                                 entrada_en=nueva, salida_en=None, km_entrada=130000, obs_entrada='',
                                 obs_salida='', programacion=None, conductor=self.conductor)
        self.assertEqual(jornadas.calificacion_cierre(self.jornada()), jornadas.EN_HORARIO)

    def test_convertir_la_ultima_visita_en_post_ruta(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        programacion = self.interna(15, 0)
        self.mover_reloj(HOY, 7, 30)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 15, 0)
        visita = self.entrar_en(programacion)
        self.mover_reloj(HOY, 21, 0)
        post = servicios.convertir_en_post_ruta(visita, self.admin_accesos, motivo='Se quedó a dormir',
                                                fin_atencion=tiempo.combinar(HOY, time(15, 50)), ahora=tiempo.ahora())
        visita.refresh_from_db()
        self.assertEqual(visita.minutos_permanencia, 50)
        self.assertTrue(post.esta_abierto)
        jornada = self.jornada()
        self.assertEqual((jornada.estado, jornadas.calificacion_cierre(jornada)),
                         (Jornada.Estado.CERRADA, jornadas.ANTICIPADO))


class PantallasConTramosDeJornadaTests(JornadaBase):
    """Las pantallas existentes muestran los tramos sin programación sin fallar."""

    def test_detalle_lista_exportacion_hoy_y_dashboard(self):
        post = self.dia_completo(HOY)
        self.entrar(self.admin_accesos)
        for url in (reverse('accesos:tramo_detalle', args=[post.pk]), reverse('accesos:tramo_corregir', args=[post.pk]),
                    reverse('accesos:tramos_lista') + '?desde=2026-10-07&hasta=2026-10-07',
                    reverse('accesos:tramos_exportar') + '?desde=2026-10-07&hasta=2026-10-07',
                    reverse('accesos:tablero_hoy'), reverse('accesos:dashboard') + '?desde=2026-10-07&hasta=2026-10-07'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class ConcurrenciaJornadaTests(TransactionTestCase):
    """Dos personas programan la primera ruta INTERNO de la misma placa a la vez: una sola jornada."""

    def setUp(self):
        reloj = mock.patch('accesos.tiempo.timezone.now',
                           return_value=tiempo.combinar(HOY, time(5, 0)).astimezone(dt_timezone.utc))
        reloj.start()
        self.addCleanup(reloj.stop)
        self.usuario = crear_usuario('despacho_hilos', 'Planeamiento')
        tamano = TamanoVehiculo.objects.create(nombre='Grande')
        self.cd = CentroDistribucion.objects.create(codigo='HILOS', nombre='Hilos', capacidad_simultanea=5)
        dni, _ = TipoDocumentoIdentidad.objects.get_or_create(
            codigo='DNI', defaults={'nombre': 'DNI', 'longitud_min': 8, 'longitud_max': 8})
        self.conductor = Conductor.objects.create(tipo_documento=dni, numero_documento='87654321',
                                                  apellido_paterno='A', nombres='B', creado_por=self.usuario)
        self.transporte = Transporte.objects.create(placa='HIL-001', tamano=tamano, es_interno=True,
                                                    creado_por=self.usuario)

    def test_dos_programaciones_a_la_vez_una_sola_jornada(self):
        barrera = threading.Barrier(2)
        errores = []

        def programar(hora):
            try:
                with transaction.atomic():  # como la vista de alta
                    barrera.wait()
                    Programacion.objects.create(cd=self.cd, fecha=MANANA, hora=time(hora, 0), tipo='INTERNO',
                                                transporte=self.transporte, conductor=self.conductor,
                                                documento='20144109458', razon_social='Daryza S.A.C.',
                                                motivo='Ruta', creado_por=self.usuario)
            except Exception as error:  # noqa: BLE001 — se informa en la aserción
                errores.append(repr(error))
            finally:
                connection.close()

        hilos = [threading.Thread(target=programar, args=(h,)) for h in (10, 14)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)
        self.assertEqual(errores, [])
        self.assertEqual(Programacion.objects.filter(transporte=self.transporte).count(), 2)
        self.assertEqual(Jornada.objects.filter(transporte=self.transporte, fecha=MANANA).count(), 1)
