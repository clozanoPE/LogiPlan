"""
Turno noche de la flota propia (15/10/2026). Hora de las pruebas: miércoles 07/10/2026 (HOY) y siguientes; el reloj
se mueve con mover_reloj(). Reglas aprobadas por el usuario:
- Salidas de 20:00 a 06:15 (día siguiente) = turno noche; de 06:16 a 19:59 = turno día.
- Pre-Ruta noche 20:00 (meta 60 min); cierre 06:15 + 15 = 06:30, sin «fin anticipado».
- Lunes a viernes; sábado excepcional (aviso); nunca la noche de un feriado ni la anterior.
- Cada turno tiene su Post-Ruta; la del día no toca las rutas nocturnas (T9) y portería solo ofrece las rutas del
  turno en curso (T10). Si un turno vuelve cuando ya empezó el siguiente, ese mismo registro abre su Pre-Ruta (T6/T7).
- Avería: cancela las rutas del turno en curso y las de la fecha. Mismo conductor en ambos turnos: solo aviso.
"""
from datetime import date, time, timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from .. import indicadores_flota, jornadas, servicios, tiempo
from ..forms import ProgramacionForm
from ..models import ConfiguracionAccesos, Conductor, Feriado, Jornada, Programacion, Tramo
from .test_exportaciones import filas, libro
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA

VIERNES = date(2026, 10, 9)
SABADO = date(2026, 10, 10)
DOMINGO = date(2026, 10, 11)
LUNES = date(2026, 10, 12)
PASADO = HOY + timedelta(days=2)   # viernes 09/10
DIA, NOCHE = Jornada.Turno.DIA, Jornada.Turno.NOCHE


class TurnoNocheBase(JornadaBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.nocturno = Conductor.objects.create(tipo_documento=cls.dni, numero_documento='87654321',
                                                apellido_paterno='RAMOS', nombres='LUIS', creado_por=cls.admin_accesos)

    def ruta(self, fecha, hora, minuto=0, conductor=None):
        return Programacion.objects.create(
            cd=self.lurin, fecha=fecha, hora=time(hora, minuto), tipo='INTERNO', transporte=self.f7j,
            conductor=conductor or self.conductor, documento='20144109458', razon_social='Daryza S.A.C.',
            motivo='Ruta', creado_por=self.despacho)

    def turno(self, fecha, turno):
        return Jornada.objects.get(transporte=self.f7j, fecha=fecha, turno=turno)

    def preparar_dia_y_noche(self):
        """HOY: turno día con ruta 08:00 (conductor A) y turno noche con rutas 22:00 y 23:30 (conductor B); MAÑANA:
        turno día con ruta 08:00 (conductor A). Todo programado de madrugada, antes de la Post-Ruta del día."""
        self.mover_reloj(HOY, 5, 0)
        self.ocho = self.ruta(HOY, 8)
        self.diez_noche = self.ruta(HOY, 22, conductor=self.nocturno)
        self.once_y_media = self.ruta(HOY, 23, 30, conductor=self.nocturno)
        self.ocho_manana = self.ruta(MANANA, 8)

    def dia_hasta_post_ruta(self, retorno=(16, 0)):
        self.mover_reloj(HOY, 7, 40)
        self.salida_sin_registro()
        self.mover_reloj(HOY, *retorno)
        return self.post_ruta()


class ClasificacionTests(TurnoNocheBase):

    def test_franjas_de_salida(self):
        casos = [((HOY, time(19, 59)), (HOY, DIA)), ((HOY, time(20, 0)), (HOY, NOCHE)),
                 ((HOY, time(23, 30)), (HOY, NOCHE)), ((MANANA, time(2, 0)), (HOY, NOCHE)),
                 ((MANANA, time(6, 15)), (HOY, NOCHE)), ((MANANA, time(6, 16)), (MANANA, DIA)),
                 ((MANANA, time(6, 30)), (MANANA, DIA)),
                 ((LUNES, time(3, 0)), (LUNES, DIA)),        # no hay turno noche el domingo: madrugada del lunes
                 ((DOMINGO, time(2, 0)), (SABADO, NOCHE))]   # el del sábado (excepcional) termina el domingo
        for (fecha, hora), esperado in casos:
            with self.subTest(fecha=fecha, hora=hora):
                self.assertEqual(jornadas.turno_de(fecha, hora, self.config), esperado)

    def test_feriados_sin_turno_noche_ni_la_noche_anterior(self):
        Feriado.objects.create(fecha=VIERNES, descripcion='Feriado de prueba')
        # La noche anterior al feriado (jueves) no hay turno noche: las 03:00 del viernes son del turno día (y es
        # feriado); las 03:00 del sábado tampoco son turno noche (la noche del feriado no existe).
        self.assertEqual(jornadas.turno_de(VIERNES, time(3, 0), self.config), (VIERNES, DIA))
        self.assertEqual(jornadas.turno_de(SABADO, time(3, 0), self.config), (SABADO, DIA))
        self.assertIn('noche anterior a un feriado',
                      jornadas.motivo_no_programable(MANANA, time(21, 0), 'INTERNO', self.config))
        self.assertIn('feriado', jornadas.motivo_no_programable(VIERNES, time(21, 0), 'INTERNO', self.config))
        self.assertIsNone(jornadas.motivo_no_programable(MANANA, time(15, 0), 'INTERNO', self.config))

    def test_formulario_sabado_domingo_y_feriado(self):
        def form(fecha, hora):
            return ProgramacionForm(data=self.datos(tipo='INTERNO', transporte=self.f7j.pk, fecha=fecha.isoformat(),
                                                    hora=hora, documento='', razon_social=''), ahora=tiempo.ahora())
        self.assertTrue(form(SABADO, '21:00').is_valid())                  # excepcional, con aviso
        self.assertTrue(form(DOMINGO, '02:00').is_valid())                 # madrugada del turno del sábado
        self.assertFalse(form(DOMINGO, '10:00').is_valid())                # el domingo no se programa
        cliente = ProgramacionForm(data=self.datos(fecha=DOMINGO.isoformat(), hora='02:00'), ahora=tiempo.ahora())
        self.assertFalse(cliente.is_valid())                               # los clientes siguen la regla de siempre
        Feriado.objects.create(fecha=VIERNES, descripcion='Feriado de prueba')
        invalido = form(MANANA, '21:00')
        self.assertFalse(invalido.is_valid())
        self.assertIn('noche anterior a un feriado', ' '.join(invalido.errors['fecha']))

    def test_avisos_de_sabado_y_mismo_conductor(self):
        sabado = self.ruta(SABADO, 21)
        self.assertTrue(any('sábado' in a for a in servicios.avisos(sabado, self.config)))
        dia = self.ruta(VIERNES, 9)
        noche = self.ruta(VIERNES, 22)  # el mismo conductor del turno día
        self.assertTrue(any('también tiene ruta en el turno día' in a for a in servicios.avisos(noche, self.config)))
        self.assertTrue(any('también tiene ruta en el turno noche' in a for a in servicios.avisos(dia, self.config)))
        otro = self.ruta(MANANA, 22, conductor=self.nocturno)
        self.assertFalse(any('también tiene ruta' in a for a in servicios.avisos(otro, self.config)))


class SincronizacionDeTurnosTests(TurnoNocheBase):

    def test_dos_jornadas_por_placa_y_dia_con_su_conductor(self):
        self.preparar_dia_y_noche()
        dia, noche = self.turno(HOY, DIA), self.turno(HOY, NOCHE)
        self.assertEqual((dia.conductor, noche.conductor), (self.conductor, self.nocturno))
        self.assertEqual((noche.hora_inicio, noche.meta_pre_ruta_min, noche.hora_cierre, noche.tolerancia_cierre_min),
                         (time(20, 0), 60, time(6, 15), 15))
        self.assertEqual(jornadas.limite_cierre(noche), tiempo.combinar(MANANA, time(6, 30)))
        self.ocho.refresh_from_db()
        self.diez_noche.refresh_from_db()
        self.assertEqual((self.ocho.jornada, self.diez_noche.jornada), (dia, noche))
        madrugada = self.ruta(MANANA, 2)
        self.assertEqual(madrugada.jornada, noche)  # la madrugada siguiente es del mismo turno noche
        self.assertEqual(len(jornadas.jornadas_del_dia(HOY, tiempo.ahora())), 2)

    def test_cancelar_las_nocturnas_anula_solo_el_turno_noche(self):
        self.preparar_dia_y_noche()
        for ruta in (self.diez_noche, self.once_y_media):
            servicios.cancelar(ruta, self.despacho, 'Sin carga', tiempo.ahora())
        self.assertEqual(self.turno(HOY, NOCHE).estado, Jornada.Estado.ANULADA)
        self.assertEqual(self.turno(HOY, DIA).estado, Jornada.Estado.PLANIFICADA)

    def test_cambiar_la_hora_mueve_la_ruta_de_turno(self):
        self.mover_reloj(HOY, 5, 0)
        ruta = self.ruta(HOY, 19)
        self.assertEqual(ruta.jornada.turno, DIA)
        ruta.hora = time(21, 0)
        ruta.save()
        ruta.refresh_from_db()
        self.assertEqual(ruta.jornada, self.turno(HOY, NOCHE))
        self.assertEqual(self.turno(HOY, DIA).estado, Jornada.Estado.ANULADA)

    def test_comando_reclasifica_las_rutas_futuras_ya_cargadas(self):
        self.mover_reloj(HOY, 5, 0)
        ruta = self.ruta(HOY, 22)
        dia = self.turno(HOY, DIA) if Jornada.objects.filter(turno=DIA).exists() else None
        self.assertIsNone(dia)
        # Como quedó tras la migración 0016: ligada al turno día de su fecha.
        dia = Jornada.objects.create(transporte=self.f7j, fecha=HOY, cd=self.lurin, conductor=self.conductor,
                                     hora_inicio=time(6, 30), meta_pre_ruta_min=60, hora_cierre=time(16, 0),
                                     tolerancia_cierre_min=15)
        Programacion.objects.filter(pk=ruta.pk).update(jornada=dia)
        Jornada.objects.filter(turno=NOCHE).update(estado=Jornada.Estado.ANULADA)
        salida = StringIO()
        call_command('reclasificar_turnos', stdout=salida)
        self.assertIn('Rutas por reclasificar: 1', salida.getvalue())
        ruta.refresh_from_db()
        self.assertEqual(ruta.jornada, dia)  # sin --grabar no cambia nada
        call_command('reclasificar_turnos', '--grabar', stdout=StringIO())
        ruta.refresh_from_db()
        self.assertEqual((ruta.jornada.turno, ruta.jornada.estado), (NOCHE, Jornada.Estado.PLANIFICADA))
        dia.refresh_from_db()
        self.assertEqual(dia.estado, Jornada.Estado.ANULADA)
        call_command('reclasificar_turnos', stdout=salida)
        self.assertIn('Rutas por reclasificar: 0', salida.getvalue())


class EjemploDiaYNocheTests(TurnoNocheBase):
    """El caso de Despacho: el turno día termina a las 16:00 y la misma placa, con otro conductor, sale a las 22:00,
    vuelve a cargar y sale a las 23:30, y termina el turno noche a las 02:00 del día siguiente."""

    def test_dia_completo_con_turno_noche(self):
        self.preparar_dia_y_noche()
        self.mover_reloj(HOY, 7, 40)
        self.salida_sin_registro()

        # 16:00: vuelve. Las rutas nocturnas NO se ofrecen como entrada (T10) y la Post-Ruta no las toca (T9).
        self.mover_reloj(HOY, 16, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.pendientes), (servicios.POST_RUTA, []))
        self.assertEqual(resultado.siguiente, self.turno(HOY, NOCHE))
        post_dia, avisos = self.post_ruta()
        self.assertEqual(avisos, [])
        self.assertEqual(jornadas.calificacion_cierre(self.turno(HOY, DIA)), jornadas.EN_HORARIO)
        self.assertFalse(Programacion.objects.filter(no_atendida_por_cierre=True).exists())

        # 17:30: entre turnos, una salida no es la ruta nocturna (pide motivo).
        self.mover_reloj(HOY, 17, 30)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_TRAS_CIERRE)

        # 21:55: sale a la ruta de las 22:00. La pernocta termina a las 20:00 y la Pre-Ruta noche dura 115 min.
        self.mover_reloj(HOY, 21, 55)
        resultado = self.evaluar()
        self.assertEqual((resultado.tipo_salida, resultado.ruta_salida, resultado.jornada),
                         (servicios.SALIDA_PRE_RUTA_AUTOMATICA, self.diez_noche, self.turno(HOY, NOCHE)))
        pre = self.salida(post_dia)
        post_dia.refresh_from_db()
        self.assertEqual(tiempo.a_lima(post_dia.salida_en).time(), time(20, 0))
        self.assertEqual((pre.clase, pre.jornada.turno, pre.minutos_permanencia, pre.desviacion_salida_min,
                          pre.conductor), (Tramo.Clase.PRE_RUTA, NOCHE, 115, -5, self.nocturno))
        noche = self.turno(HOY, NOCHE)
        self.assertEqual((noche.estado, jornadas.pre_ruta_a_tiempo(noche)), (Jornada.Estado.EN_RUTA, False))

        # 23:10: vuelve a cargar: solo se ofrece la ruta de las 23:30, no la de mañana a las 08:00.
        self.mover_reloj(HOY, 23, 10)
        resultado = self.evaluar()
        self.assertEqual([c.programacion for c in resultado.candidatas], [self.once_y_media])
        visita = self.entrar_en(self.once_y_media)
        self.assertEqual(visita.jornada, noche)
        self.mover_reloj(HOY, 23, 35)
        visita = self.salida(visita)
        self.assertEqual(visita.desviacion_salida_min, 5)

        # 02:00 del día siguiente: Post-Ruta del turno noche (no «inicio de jornada desde afuera»).
        self.mover_reloj(MANANA, 2, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.jornada, resultado.pendientes),
                         (servicios.POST_RUTA, noche, []))
        post_noche, _ = self.post_ruta()
        noche.refresh_from_db()
        self.assertEqual((noche.estado, jornadas.calificacion_cierre(noche), jornadas.minutos_sobre_cierre(noche)),
                         (Jornada.Estado.CERRADA, jornadas.EN_HORARIO, 0))
        self.assertEqual(post_noche.jornada, noche)
        self.ocho_manana.refresh_from_db()
        self.assertFalse(self.ocho_manana.no_atendida_por_cierre)

        # 07:40: turno día de mañana. La pernocta se cierra a las 06:30 y la Pre-Ruta dura 70 min.
        self.mover_reloj(MANANA, 7, 40)
        resultado = self.evaluar()
        self.assertEqual((resultado.tipo_salida, resultado.ruta_salida),
                         (servicios.SALIDA_PRE_RUTA_AUTOMATICA, self.ocho_manana))
        pre = self.salida(post_noche)
        self.assertEqual((pre.jornada, pre.minutos_permanencia), (self.turno(MANANA, DIA), 70))
        post_noche.refresh_from_db()
        self.assertEqual(tiempo.a_lima(post_noche.salida_en).time(), time(6, 30))
        # Todas las rutas se cumplieron; ninguna quedó «No atendida».
        self.assertEqual(Programacion.objects.filter(estado=Programacion.Estado.CERRADA).count(), 4)

    def test_estado_de_una_ruta_nocturna_durante_la_madrugada(self):
        self.preparar_dia_y_noche()
        self.mover_reloj(MANANA, 1, 0)
        self.diez_noche.refresh_from_db()
        self.assertEqual(servicios.estado_visible(self.diez_noche, MANANA), servicios.PENDIENTE)
        filtradas = servicios.filtrar_por_estado_visible(Programacion.objects.all(), servicios.PENDIENTE, MANANA)
        self.assertIn(self.diez_noche, filtradas)
        self.assertNotIn(self.ocho, filtradas)
        no_atendidas = servicios.filtrar_por_estado_visible(Programacion.objects.all(), servicios.NO_ATENDIDA, MANANA)
        self.assertEqual(set(no_atendidas), {self.ocho})
        pasado = MANANA + timedelta(days=1)
        self.assertEqual(servicios.estado_visible(self.diez_noche, pasado), servicios.NO_ATENDIDA)
        self.assertIn(self.diez_noche,
                      servicios.filtrar_por_estado_visible(Programacion.objects.all(), servicios.NO_ATENDIDA, pasado))

    def test_estados_visibles_del_turno_noche(self):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta()
        noche = self.turno(HOY, NOCHE)

        def estado(dia, hora, minuto=0):
            self.mover_reloj(dia, hora, minuto)
            return next(j for j in jornadas.jornadas_en_vivo(tiempo.ahora()) if j.pk == noche.pk).estado_v.codigo

        self.assertEqual(estado(HOY, 19, 0), 'POR_INICIAR')
        self.assertEqual(estado(HOY, 20, 30), 'EN_PRE_RUTA')      # duerme en el CD desde la Post-Ruta del día
        self.assertEqual(estado(HOY, 21, 10), 'PRE_RUTA_EXCEDIDA')
        self.salida(self.abierto())
        self.assertEqual(estado(MANANA, 2, 0), 'EN_RUTA')
        self.assertEqual(estado(MANANA, 6, 31), 'SIN_RETORNO')
        self.mover_reloj(PASADO, 8, 0)
        noche.refresh_from_db()
        self.assertEqual(jornadas.estado_visible(noche, tiempo.ahora()).codigo, 'SIN_POST_RUTA')
        lista = indicadores_flota.jornadas_del_periodo(HOY, HOY, {})
        self.assertEqual(indicadores_flota.kpis(lista, tiempo.ahora())['sin_post_ruta'], 1)
        # A la 01:00 del día siguiente el turno noche sigue en curso: no es «Sin Post-Ruta» todavía.
        self.mover_reloj(MANANA, 1, 0)
        self.assertEqual(indicadores_flota.kpis(lista, tiempo.ahora())['sin_post_ruta'], 0)

    def test_turno_noche_que_no_sale(self):
        self.mover_reloj(HOY, 5, 0)
        noche = Jornada.objects.get(pk=self.ruta(HOY, 22).jornada_id)
        self.mover_reloj(MANANA, 6, 31)
        self.assertEqual(jornadas.estado_visible(noche, tiempo.ahora()).codigo, 'SIN_SALIDA')


class CambiosDeTurnoTests(TurnoNocheBase):

    def test_regreso_del_turno_noche_despues_de_las_0630_abre_la_pre_ruta_del_dia(self):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 22, 0)
        self.salida(self.abierto())
        self.mover_reloj(HOY, 23, 10)
        self.salida(self.entrar_en(self.once_y_media))
        self.mover_reloj(MANANA, 7, 0)  # vuelve tarde: ya empezó el turno día
        self.assertEqual(self.evaluar().accion, servicios.POST_RUTA)
        post, _ = self.post_ruta()
        noche = self.turno(HOY, NOCHE)
        noche.refresh_from_db()
        self.assertEqual((jornadas.calificacion_cierre(noche), jornadas.minutos_sobre_cierre(noche)),
                         (jornadas.FUERA_DE_HORARIO, 30))
        self.mover_reloj(MANANA, 7, 40)
        pre = self.salida(post)
        self.assertEqual((pre.jornada, pre.minutos_permanencia), (self.turno(MANANA, DIA), 40))
        self.assertEqual(tiempo.a_lima(pre.entrada_en).time(), time(7, 0))

    def test_regreso_del_turno_noche_en_los_limites(self):
        for regreso, esperado, minutos in (((6, 20), jornadas.EN_HORARIO, 0), ((6, 30), jornadas.EN_HORARIO, 0),
                                           ((6, 40), jornadas.FUERA_DE_HORARIO, 10)):
            with self.subTest(regreso=regreso):
                Tramo.objects.filter(transporte=self.f7j).update(abierto=None)
                fecha = HOY + timedelta(days=[(6, 20), (6, 30), (6, 40)].index(regreso))
                self.mover_reloj(fecha, 12, 0)
                self.ruta(fecha, 21)
                self.mover_reloj(fecha, 21, 0)
                self.salida_sin_registro()
                self.mover_reloj(fecha + timedelta(days=1), *regreso)
                self.post_ruta()
                noche = self.turno(fecha, NOCHE)
                self.assertEqual(jornadas.calificacion_cierre(noche), esperado)
                self.assertEqual(jornadas.minutos_sobre_cierre(noche), minutos)

    def test_el_turno_dia_vuelve_despues_de_las_20_y_abre_la_pre_ruta_noche(self):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta(retorno=(21, 0))
        dia = self.turno(HOY, DIA)
        self.assertEqual(jornadas.calificacion_cierre(dia), jornadas.FUERA_DE_HORARIO)
        self.mover_reloj(HOY, 21, 30)
        resultado = self.evaluar()
        self.assertEqual((resultado.tipo_salida, resultado.ruta_salida),
                         (servicios.SALIDA_PRE_RUTA_AUTOMATICA, self.diez_noche))
        pre = self.salida(self.abierto())
        self.assertEqual((pre.jornada.turno, pre.minutos_permanencia), (NOCHE, 30))

    def test_entre_turnos_la_salida_pide_motivo_y_vuelve_como_reingreso(self):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 17, 0)
        with self.assertRaises(ValidationError):
            self.salida(self.abierto())
        self.salida(self.abierto(), obs='Al grifo')
        self.mover_reloj(HOY, 18, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.reingreso, resultado.jornada),
                         (servicios.POST_RUTA, True, self.turno(HOY, DIA)))
        self.post_ruta()
        self.assertEqual(self.turno(HOY, NOCHE).estado, Jornada.Estado.PLANIFICADA)

    def test_placa_solo_de_noche_ingresa_desde_afuera(self):
        self.mover_reloj(HOY, 12, 0)
        ruta = self.ruta(HOY, 22, conductor=self.nocturno)
        self.mover_reloj(HOY, 18, 0)
        self.assertEqual(self.evaluar().accion, servicios.NO_PROGRAMADO)  # todavía no es su turno
        self.mover_reloj(HOY, 21, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.ruta_salida), (servicios.INICIO_JORNADA, ruta))
        pre, _ = servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=1000, obs='',
                                                    conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        self.mover_reloj(HOY, 21, 40)
        self.salida(pre)
        noche = self.turno(HOY, NOCHE)
        self.assertEqual((noche.ingreso_desde_afuera, jornadas.minutos_pre_ruta(noche)), (True, 40))

    def test_averia_en_el_turno_noche_cancela_sus_rutas_y_las_del_dia(self):
        self.preparar_dia_y_noche()
        tres = self.ruta(MANANA, 3, conductor=self.nocturno)
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 22, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 1, 0)
        _, avisos = self.post_ruta(averia_tipo_id=self.averia.pk, averia_descripcion='Falla de frenos')
        for ruta in (self.once_y_media, tres, self.ocho_manana):
            ruta.refresh_from_db()
            self.assertEqual(ruta.estado, Programacion.Estado.CANCELADA, ruta)
        self.assertEqual(self.turno(MANANA, DIA).estado, Jornada.Estado.ANULADA)
        self.assertEqual(self.turno(HOY, NOCHE).motivo_cierre, Jornada.MotivoCierre.AVERIA)
        self.assertTrue(any('cancelaron' in a for a in avisos))

    def test_anular_la_post_ruta_noche_reabre_solo_el_turno_noche(self):
        self.preparar_dia_y_noche()
        tres = self.ruta(MANANA, 3, conductor=self.nocturno)
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 22, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 1, 0)
        post, _ = self.post_ruta()
        for ruta in (self.once_y_media, tres):
            ruta.refresh_from_db()
            self.assertTrue(ruta.no_atendida_por_cierre)
        self.ocho_manana.refresh_from_db()
        self.assertFalse(self.ocho_manana.no_atendida_por_cierre)
        servicios.anular_tramo(post, self.admin_accesos, motivo='Registro por error')
        self.assertEqual(self.turno(HOY, NOCHE).estado, Jornada.Estado.EN_RUTA)
        tres.refresh_from_db()
        self.assertFalse(tres.no_atendida_por_cierre)

    def test_en_vivo_incluye_el_turno_noche_de_ayer(self):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 22, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 2, 0)
        en_vivo = jornadas.jornadas_en_vivo(tiempo.ahora())
        self.assertEqual([(j.fecha, j.turno) for j in en_vivo], [(HOY, NOCHE), (MANANA, DIA)])
        self.post_ruta()
        self.mover_reloj(MANANA, 10, 0)  # cerró hoy de madrugada: se sigue viendo en la mañana…
        self.assertIn((HOY, NOCHE), [(j.fecha, j.turno) for j in jornadas.jornadas_en_vivo(tiempo.ahora())])
        self.mover_reloj(MANANA, 15, 0)  # …y por la tarde ya no se mezcla con los turnos de hoy
        self.assertNotIn((HOY, NOCHE), [(j.fecha, j.turno) for j in jornadas.jornadas_en_vivo(tiempo.ahora())])
        self.mover_reloj(PASADO, 2, 0)
        self.assertNotIn((HOY, NOCHE), [(j.fecha, j.turno) for j in jornadas.jornadas_en_vivo(tiempo.ahora())])


class PantallasTurnoNocheTests(TurnoNocheBase):
    """Las pantallas con un día completo con turno noche: portería, agenda, «Hoy», detalle, dashboard, TV,
    Trazabilidad y exportaciones."""

    def jornada_completa(self, hasta_la_madrugada=True):
        self.preparar_dia_y_noche()
        self.dia_hasta_post_ruta()
        self.mover_reloj(HOY, 21, 55)
        self.salida(self.abierto())
        self.mover_reloj(HOY, 23, 10)
        visita = self.entrar_en(self.once_y_media)
        self.mover_reloj(HOY, 23, 35)
        self.salida(visita)
        if hasta_la_madrugada:
            self.mover_reloj(MANANA, 2, 0)
            self.post_ruta()
            self.mover_reloj(MANANA, 2, 30)

    def test_porteria_muestra_el_turno(self):
        self.preparar_dia_y_noche()
        self.mover_reloj(HOY, 7, 40)
        self.salida_sin_registro()
        self.mover_reloj(HOY, 16, 0)
        self.entrar(self.vigilante)
        respuesta = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J011'})
        self.assertContains(respuesta, 'Fin de jornada · Post-Ruta')
        self.assertContains(respuesta, 'Próximo turno de la placa: noche')
        self.assertNotContains(respuesta, self.diez_noche.codigo)  # no se ofrece ni se cierra la ruta nocturna
        self.post_ruta()
        self.mover_reloj(HOY, 21, 55)
        respuesta = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J011'})
        self.assertContains(respuesta, 'fin de la Pre-Ruta · turno noche')
        self.assertContains(respuesta, 'esperó en el CD')
        self.assertContains(respuesta, '20:00')
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 2, 0)
        # La ruta de las 23:30 sigue pendiente: se ofrece como entrada, con la opción «Fin de jornada».
        respuesta = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J011'})
        self.assertEqual([c.programacion for c in respuesta.context['resultado'].candidatas], [self.once_y_media])
        respuesta = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J011', 'modo': 'post_ruta'})
        self.assertContains(respuesta, 'Fin del turno noche · Post-Ruta')
        self.assertContains(respuesta, self.once_y_media.codigo)  # quedará «No atendida» si cierra el turno
        self.assertContains(respuesta, 'Cierre del turno noche')

    def test_agenda_hoy_y_detalle(self):
        self.jornada_completa()
        self.entrar(self.despacho)
        agenda = self.client.get(reverse('accesos:programacion_agenda'), {'fecha': HOY.isoformat()})
        self.assertEqual(len(agenda.context['jornadas_dia']), 2)
        self.assertContains(agenda, 'Turno noche del 07/10')
        hoy = self.client.get(reverse('accesos:tablero_hoy'))
        self.assertEqual((hoy.context['resumen_flota']['placas'], hoy.context['resumen_flota']['noche']), (1, 1))
        self.assertContains(hoy, 'turno noche')
        detalle = self.client.get(reverse('accesos:programacion_detalle', args=[self.diez_noche.pk]))
        self.assertContains(detalle, 'Turno noche del 07/10')

    def test_dashboard_flota_por_turno(self):
        self.jornada_completa()
        self.entrar(self.consulta)
        url = reverse('accesos:dashboard')
        respuesta = self.client.get(url, {'vista': 'flota', 'desde': HOY.isoformat(), 'hasta': HOY.isoformat()})
        flota = respuesta.context['flota']
        self.assertEqual([f['k']['jornadas'] for f in flota['por_turno']], [1, 1])
        noche = respuesta.context['datos']['flota']['gantt_noche']
        self.assertEqual(len(noche['filas']), 1)
        segmentos = noche['filas'][0]['segmentos']
        self.assertEqual(segmentos[0]['desde'], 20 * 60)                       # Pre-Ruta desde las 20:00
        self.assertEqual(segmentos[-1]['desde'], 24 * 60 + 2 * 60)             # Post-Ruta a las 02:00 del día siguiente
        self.assertEqual(len(respuesta.context['datos']['flota']['gantt']['filas']), 1)
        self.assertContains(respuesta, 'Línea de tiempo · turno noche')
        solo_noche = self.client.get(url, {'vista': 'flota', 'desde': HOY.isoformat(), 'hasta': HOY.isoformat(),
                                           'turno': 'NOCHE'})
        self.assertEqual(solo_noche.context['flota']['kpis']['jornadas'], 1)
        self.assertIsNone(solo_noche.context['datos']['flota']['gantt'])
        self.assertEqual(solo_noche.context['datos']['flota']['retorno']['turno'], NOCHE)
        rango = self.client.get(url, {'vista': 'flota', 'desde': HOY.isoformat(), 'hasta': MANANA.isoformat()})
        self.assertEqual([f['placa'] for f in rango.context['datos']['flota']['matriz']['filas']],
                         ['F7J-011', 'F7J-011 · noche'])

    def test_tv_muestra_el_turno_noche_en_curso(self):
        self.jornada_completa(hasta_la_madrugada=False)
        self.mover_reloj(MANANA, 1, 0)
        self.entrar(self.admin_accesos)
        datos = self.client.get(reverse('accesos:tv_datos', args=['LURIN'])).json()
        self.assertEqual((datos['cifras']['placas'], datos['cifras']['noche'], datos['cifras']['dia']), (1, 1, 1))
        self.assertEqual((datos['gantt']['turno'], len(datos['gantt']['filas'])), (NOCHE, 1))
        self.assertIn('noche', datos['gantt']['titulo'])
        self.mover_reloj(MANANA, 10, 0)
        datos = self.client.get(reverse('accesos:tv_datos', args=['LURIN'])).json()
        self.assertEqual(datos['gantt']['turno'], DIA)

    def test_trazabilidad_y_exportaciones(self):
        self.jornada_completa()
        self.entrar(self.despacho)
        respuesta = self.client.get(reverse('accesos:trazabilidad'), {'placa': 'F7J011', 'fecha': HOY.isoformat()})
        self.assertEqual(respuesta.context['noche'], self.turno(HOY, NOCHE))
        nocturnos = [e for e in respuesta.context['eventos'] if e.turno_noche]
        self.assertTrue(any(e.tipo == 'post_ruta' and not e.otro_dia for e in nocturnos))
        self.assertEqual(respuesta.context['resumen']['noche']['calificacion_cierre'], 'En horario')
        self.assertContains(respuesta, 'Turno noche')
        hoja = libro(self.client.get(reverse('accesos:trazabilidad_exportar'),
                                     {'placa': 'F7J011', 'fecha': HOY.isoformat()})).active
        self.assertIn('Noche', [fila['Turno'] for fila in filas(hoja)])
        hoja = libro(self.client.get(reverse('accesos:programacion_exportar'), {'fecha': HOY.isoformat()})).active
        turnos = {fila['Código']: fila['Turno'] for fila in filas(hoja)}
        self.assertEqual((turnos[self.ocho.codigo], turnos[self.diez_noche.codigo]), ('Día', 'Noche (del 07/10)'))

    def test_configuracion_valida_las_franjas(self):
        config = ConfiguracionAccesos.obtener()
        config.hora_cierre_noche = time(6, 45)       # después de la Pre-Ruta del día (06:30)
        config.hora_pre_ruta_noche = time(15, 0)     # antes del cierre operativo (16:00)
        with self.assertRaises(ValidationError) as error:
            config.full_clean()
        self.assertEqual(set(error.exception.message_dict), {'hora_cierre_noche', 'hora_pre_ruta_noche'})
