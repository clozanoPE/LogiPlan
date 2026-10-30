"""
Retorno imprevisto, candados del kilometraje y control del recorrido por placa (30/10/2026).

Caso del usuario: la placa sale a las 08:00 con 200 km, se olvidó algo, vuelve a las 10:00 y sale a las 11:00; retorna
a las 16:05. A las 10:00 no se acepta 190 ni 200 km. Todo debe verse en la Trazabilidad y en el Reporte (tiempo y km,
cómo inició y con qué km terminó) y permitir estadísticas por placa para medir combustible.
"""
from datetime import time
from io import BytesIO

from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook

from .. import indicadores, indicadores_flota, jornadas, kilometraje, pantalla, reporte_jornadas, servicios, tiempo, trazabilidad
from ..models import ConfiguracionAccesos, Jornada, Programacion, RegistroAuditoria, Tramo
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA

KmEstado = Tramo.KmEstado
REPORTE = reverse('accesos:reporte_jornadas')
EXPORTAR = reverse('accesos:reporte_exportar')
PORTERIA = reverse('accesos:porteria')
IMPREVISTO = reverse('accesos:porteria_imprevisto')
TRAZABILIDAD = reverse('accesos:trazabilidad')


class ImprevistoBase(JornadaBase):

    def en_ruta(self, rutas=((8, 0),), km=200):
        """Sale a las 08:00 con `km` (durmió en el CD sin registro)."""
        self.mover_reloj(HOY, 5, 0)
        self.rutas = [self.interna(h, m) for h, m in rutas]
        self.mover_reloj(HOY, 8, 0)
        return self.salida_sin_registro(km=km)

    def imprevisto(self, km=245, motivo='OLVIDO', obs='Olvidó las guías', **extra):
        tramo, _ = servicios.registrar_imprevisto(transporte_id=self.f7j.pk, cd=self.lurin, km=km, obs=obs, motivo=motivo,
                                                  conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora(), **extra)
        return tramo

    def dia_del_usuario(self):
        """08:00 sale con 200; 10:00 vuelve con 245 (imprevisto); 11:00 sale; 16:05 Post-Ruta con 312."""
        self.en_ruta()
        self.mover_reloj(HOY, 10, 0)
        self.retorno = self.imprevisto()
        self.mover_reloj(HOY, 11, 0)
        self.salida(self.retorno)
        self.mover_reloj(HOY, 16, 5)
        self.post, _ = self.post_ruta(km=312)
        return self.jornada()


class RetornoImprevistoTests(ImprevistoBase):

    def test_el_dia_completo_no_altera_la_jornada(self):
        self.en_ruta()
        self.mover_reloj(HOY, 10, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.permite_imprevisto), (servicios.POST_RUTA, True))
        retorno = self.imprevisto()
        self.assertEqual((retorno.clase, retorno.motivo_imprevisto, retorno.programacion_id, retorno.km_entrada),
                         (Tramo.Clase.IMPREVISTO, 'OLVIDO', None, 245))
        self.assertTrue(retorno.esta_abierto)
        jornada = self.jornada()
        self.assertEqual((jornada.estado, jornada.post_ruta_en), (Jornada.Estado.EN_RUTA, None))  # no cierra la jornada
        self.assertEqual(self.evaluar().accion, servicios.SALIDA)
        self.assertEqual(self.evaluar().tipo_salida, servicios.SALIDA_NORMAL)  # salida común, sin motivo
        self.mover_reloj(HOY, 11, 0)
        self.salida(retorno)
        retorno.refresh_from_db()
        self.assertEqual(retorno.minutos_permanencia, 60)
        self.mover_reloj(HOY, 16, 5)
        self.assertEqual(self.evaluar().accion, servicios.POST_RUTA)  # el retorno final sigue siendo la Post-Ruta
        self.post_ruta(km=312)
        jornada.refresh_from_db()
        self.assertEqual((jornada.estado, jornadas.calificacion_cierre(jornada)),
                         (Jornada.Estado.CERRADA, jornadas.EN_HORARIO))

    def test_no_toca_las_rutas_pendientes(self):
        self.en_ruta(rutas=((8, 0), (14, 0)))
        self.mover_reloj(HOY, 10, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.permite_imprevisto), (servicios.ENTRADA, True))
        retorno = self.imprevisto()
        self.salida(retorno)
        catorce = Programacion.objects.get(pk=self.rutas[1].pk)
        self.assertEqual((catorce.estado, catorce.no_atendida_por_cierre), (Programacion.Estado.PROGRAMADA, False))
        self.mover_reloj(HOY, 13, 30)
        self.assertEqual([c.programacion for c in self.evaluar().candidatas], [catorce])  # se sigue ofreciendo

    def test_solo_para_una_unidad_en_ruta(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 0)
        self.assertFalse(self.evaluar().permite_imprevisto)
        with self.assertRaises(ValidationError):  # todavía no salió a ruta
            self.imprevisto(km=100)

    def test_vehiculo_externo_sin_programacion_sigue_bloqueado(self):
        self.mover_reloj(HOY, 10, 0)
        resultado = self.evaluar()
        self.assertEqual((resultado.accion, resultado.permite_imprevisto), (servicios.NO_PROGRAMADO, False))

    def test_exige_motivo_de_la_lista_y_observacion(self):
        self.en_ruta()
        self.mover_reloj(HOY, 10, 0)
        with self.assertRaises(ValidationError):
            self.imprevisto(motivo='CUALQUIERA')
        with self.assertRaises(ValidationError):
            self.imprevisto(obs='  ')
        self.assertFalse(Tramo.objects.filter(clase=Tramo.Clase.IMPREVISTO).exists())

    def test_varios_imprevistos_sin_limite(self):
        self.en_ruta()
        for hora, km in ((9, 210), (10, 220), (11, 230)):
            self.mover_reloj(HOY, hora, 0)
            retorno = self.imprevisto(km=km)
            self.mover_reloj(HOY, hora, 30)
            self.salida(retorno)
        self.assertEqual(Tramo.objects.filter(clase=Tramo.Clase.IMPREVISTO, vigente=True).count(), 3)

    def test_anular_y_convertir_en_post_ruta(self):
        self.en_ruta()
        self.mover_reloj(HOY, 10, 0)
        retorno = self.imprevisto()
        servicios.anular_tramo(retorno, self.admin_accesos, motivo='Error del vigilante')
        self.assertEqual(self.jornada().estado, Jornada.Estado.EN_RUTA)
        self.assertEqual(kilometraje.ultima_lectura(self.f7j.pk).km_entrada, 200)
        # Entró como imprevisto y ya no volvió a salir: el Administrador lo convierte en Post-Ruta.
        self.mover_reloj(HOY, 15, 0)
        retorno = self.imprevisto(km=300)
        self.mover_reloj(HOY, 21, 0)
        post = servicios.convertir_en_post_ruta(retorno, self.admin_accesos, fin_atencion=tiempo.combinar(HOY, time(15, 30)),
                                                motivo='Se quedó a dormir', ahora=tiempo.ahora())
        self.assertEqual((post.clase, post.km_entrada), (Tramo.Clase.POST_RUTA, 300))
        self.assertEqual(self.jornada().estado, Jornada.Estado.CERRADA)

    def test_no_entra_en_los_indicadores_de_planta(self):
        self.dia_del_usuario()
        self.assertEqual(len(indicadores.tramos_del_periodo(HOY, HOY, {})), 0)

    def test_linea_de_tiempo_y_tv_no_se_rompen(self):
        self.dia_del_usuario()
        gantt = indicadores_flota.gantt(HOY, tiempo.ahora(), {})
        etiquetas = [s['etiqueta'] for fila in gantt['filas'] for s in fila['segmentos']]
        self.assertIn('Retorno imprevisto', etiquetas)
        self.assertEqual(pantalla.datos(self.lurin, tiempo.ahora())['foco']['turno'], 'DIA')


class PantallaPorteriaTests(ImprevistoBase):

    def test_pantalla_y_registro(self):
        self.en_ruta()
        self.mover_reloj(HOY, 10, 0)
        self.entrar(self.vigilante)
        respuesta = self.client.get(PORTERIA, {'placa': 'F7J-011'})
        self.assertContains(respuesta, 'modo=imprevisto')
        respuesta = self.client.get(PORTERIA, {'placa': 'F7J-011', 'modo': 'imprevisto'})
        self.assertContains(respuesta, 'Registrar retorno imprevisto')
        self.assertContains(respuesta, 'Olvidó carga o documentos')
        self.assertContains(respuesta, 'data-ultimo="200"')
        self.assertContains(respuesta, 'data-tope="160"')  # 2 h fuera a 80 km/h
        datos = {'transporte': self.f7j.pk, 'placa': 'F7J-011', 'km': 245, 'motivo': 'OLVIDO', 'obs': 'Olvidó las guías'}
        sin_motivo = self.client.post(IMPREVISTO, {**datos, 'motivo': ''}, follow=True)
        self.assertContains(sin_motivo, 'Elija el motivo')
        respuesta = self.client.post(IMPREVISTO, datos)
        self.assertIn('registrado=IMPREVISTO', respuesta.url)
        self.assertEqual(Tramo.objects.get(clase=Tramo.Clase.IMPREVISTO).km_entrada, 245)
        self.assertContains(self.client.get(respuesta.url), 'RETORNO IMPREVISTO registrado')

    def test_modo_imprevisto_ignorado_si_no_corresponde(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 0)
        self.entrar(self.vigilante)
        respuesta = self.client.get(PORTERIA, {'placa': 'F7J-011', 'modo': 'imprevisto'})
        self.assertNotContains(respuesta, 'Registrar retorno imprevisto')

    def test_solo_vigilancia(self):
        self.en_ruta()
        for usuario in (self.admin_accesos, self.despacho, self.consulta):
            self.entrar(usuario)
            self.assertEqual(self.client.post(IMPREVISTO, {'transporte': self.f7j.pk}).status_code, 403)


class CandadosKmTests(ImprevistoBase):

    def test_ejemplo_del_usuario_190_y_200_no_pasan(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        for km, codigo in ((190, 'km_menor'), (200, 'km_igual')):
            with self.assertRaises(kilometraje.KmRechazado) as error:
                self.imprevisto(km=km)
            self.assertEqual(error.exception.code, codigo)
        self.assertFalse(Tramo.objects.filter(clase=Tramo.Clase.IMPREVISTO).exists())
        self.assertEqual(self.imprevisto(km=201).km_entrada, 201)

    def test_rige_en_todas_las_entradas(self):
        self.en_ruta(rutas=((8, 0), (14, 0)), km=200)
        self.mover_reloj(HOY, 13, 0)
        with self.assertRaises(kilometraje.KmRechazado):
            self.entrar_en(self.rutas[1], km=200)
        with self.assertRaises(kilometraje.KmRechazado):
            self.post_ruta(km=200)

    def test_tope_por_tiempo_fuera_del_cd(self):
        """2 h fuera a 80 km/h: como máximo 160 km."""
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        with self.assertRaises(kilometraje.KmRechazado) as error:
            self.imprevisto(km=361)
        self.assertEqual(error.exception.code, 'km_excesivo')
        self.assertIn('160 km', error.exception.messages[0])
        self.assertEqual(self.imprevisto(km=360).km_entrada, 360)

    def test_el_tiempo_dentro_del_cd_no_cuenta(self):
        """Tras el retorno de las 10:00 sale a las 15:00: a las 16:00 solo hubo 1 h fuera (80 km)."""
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        retorno = self.imprevisto(km=245)
        self.mover_reloj(HOY, 15, 0)
        self.salida(retorno)
        self.mover_reloj(HOY, 16, 0)
        with self.assertRaises(kilometraje.KmRechazado):
            self.post_ruta(km=326)
        self.assertEqual(self.post_ruta(km=325)[0].km_entrada, 325)

    def test_velocidad_configurable(self):
        ConfiguracionAccesos.objects.filter(pk=self.config.pk).update(velocidad_maxima_kmh=50)
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        with self.assertRaises(kilometraje.KmRechazado):
            self.imprevisto(km=301)
        self.assertEqual(self.imprevisto(km=300).km_entrada, 300)

    def test_el_digito_de_mas_o_de_menos_no_pasa(self):
        self.en_ruta(km=125000)
        self.mover_reloj(HOY, 10, 0)
        for km in (12505, 1250050):  # le faltó o le sobró un dígito
            with self.assertRaises(kilometraje.KmRechazado):
                self.imprevisto(km=km)

    def test_esta_saliendo_acepta_el_mismo_km_pero_no_uno_menor(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 16, 0)
        self.post_ruta(km=300)
        Tramo.objects.filter(clase=Tramo.Clase.POST_RUTA).update(abierto=None, salida_en=tiempo.combinar(HOY, time(16, 30)))
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 50)
        with self.assertRaises(kilometraje.KmRechazado):
            self.salida_sin_registro(km=299)
        self.assertEqual(self.salida_sin_registro(km=300).km_entrada, 300)

    def test_intento_rechazado_queda_en_la_auditoria(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.entrar(self.vigilante)
        datos = {'transporte': self.f7j.pk, 'placa': 'F7J-011', 'motivo': 'OLVIDO', 'obs': 'Guías'}
        respuesta = self.client.post(IMPREVISTO, {**datos, 'km': 190}, follow=True)
        self.assertContains(respuesta, 'es menor al último registrado')
        self.client.post(reverse('accesos:porteria_post_ruta'), {'transporte': self.f7j.pk, 'placa': 'F7J-011', 'km': 200})
        intentos = RegistroAuditoria.objects.filter(accion=RegistroAuditoria.Accion.KM_RECHAZADO).order_by('id')
        self.assertEqual([i.datos_despues['codigo'] for i in intentos], ['km_menor', 'km_igual'])
        self.assertEqual((intentos[0].usuario, intentos[0].cd, intentos[0].objeto_id), (self.vigilante, self.lurin, self.f7j.pk))
        self.assertIn('Km 190', intentos[0].motivo)
        self.assertFalse(Tramo.objects.filter(clase=Tramo.Clase.IMPREVISTO).exists())

    def test_correccion_del_administrador_con_el_tope(self):
        self.dia_del_usuario()
        retorno = Tramo.objects.get(pk=self.retorno.pk)
        with self.assertRaises(ValidationError):  # 2 h fuera: no puede tener 200 km más que la lectura anterior
            servicios.corregir_tramo(retorno, self.admin_accesos, motivo='x', ahora=tiempo.ahora(), entrada_en=retorno.entrada_en,
                                     salida_en=retorno.salida_en, km_entrada=400, obs_entrada='', obs_salida='',
                                     programacion=None, conductor=self.conductor)
        servicios.corregir_tramo(retorno, self.admin_accesos, motivo='Era 250', ahora=tiempo.ahora(), entrada_en=retorno.entrada_en,
                                 salida_en=retorno.salida_en, km_entrada=250, obs_entrada='', obs_salida='',
                                 programacion=None, conductor=self.conductor)
        retorno.refresh_from_db()
        self.assertEqual((retorno.km_entrada, retorno.clase), (250, Tramo.Clase.IMPREVISTO))


class KmObservadoTests(ImprevistoBase):

    def observado(self, km=190):
        return self.imprevisto(km=km, km_observado=True, obs='El tablero marca 190')

    def test_el_camion_pasa_pero_no_cuenta(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        tramo = self.observado()
        self.assertEqual((tramo.km_entrada, tramo.km_estado), (190, KmEstado.OBSERVADO))
        self.assertIn('menor al último', tramo.km_motivo)
        self.assertIn('El tablero marca 190', tramo.km_motivo)
        self.assertEqual(kilometraje.ultima_lectura(self.f7j.pk).km_entrada, 200)  # no sirve de referencia
        self.f7j.refresh_from_db()
        self.assertEqual(self.f7j.km_actual, 200)

    def test_exige_observacion(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        with self.assertRaises(ValidationError):
            self.imprevisto(km=190, km_observado=True, obs='')

    def test_una_lectura_correcta_con_la_casilla_queda_registrada(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.assertEqual(self.imprevisto(km=245, km_observado=True).km_estado, KmEstado.REGISTRADO)

    def test_el_relleno_no_se_puede_observar(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        with self.assertRaises(kilometraje.KmRechazado) as error:
            self.imprevisto(km=99999, km_observado=True)
        self.assertEqual(error.exception.code, 'km_relleno')

    def test_final_observado_no_suma_y_va_a_calidad(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 16, 0)
        self.post_ruta(km=199, km_observado=True, obs='Tablero raro')
        jornada = self.jornada()
        km = kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk]
        self.assertEqual((km.valido, km.por_revisar, km.motivo), (False, True, 'Lectura final observada (por validar)'))
        self.entrar(self.admin_accesos)
        filtros = {'desde': HOY.isoformat(), 'hasta': HOY.isoformat(), 'vista': 'calidad', 'km': 'observado'}
        respuesta = self.client.get(REPORTE, filtros)
        self.assertContains(respuesta, 'observada (por validar)')
        self.assertEqual(respuesta.context['total'], 1)

    def test_administrador_valida_corrige_o_excluye(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        tramo = self.imprevisto(km=200, km_observado=True, obs='Odómetro trabado')
        kilometraje.validar_observada(tramo, self.admin_accesos, motivo='Revisado en taller')
        tramo.refresh_from_db()
        self.assertEqual((tramo.km_estado, tramo.km_motivo), (KmEstado.REGISTRADO, ''))
        self.mover_reloj(HOY, 11, 0)
        self.salida(tramo)
        self.mover_reloj(HOY, 12, 0)
        otro = self.imprevisto(km=150, km_observado=True, obs='Marca 150')
        with self.assertRaises(ValidationError):  # menor a la anterior: no se puede validar tal cual
            kilometraje.validar_observada(otro, self.admin_accesos, motivo='x')
        kilometraje.excluir(otro, self.admin_accesos, motivo='Valor no confiable')
        otro.refresh_from_db()
        self.assertEqual(otro.km_estado, KmEstado.EXCLUIDO)

    def test_corregir_una_observada_la_deja_registrada(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        tramo = self.observado()
        servicios.corregir_tramo(tramo, self.admin_accesos, motivo='Era 250', ahora=tiempo.ahora(), entrada_en=tramo.entrada_en,
                                 salida_en=None, km_entrada=250, obs_entrada='', obs_salida='', programacion=None,
                                 conductor=self.conductor)
        tramo.refresh_from_db()
        self.assertEqual((tramo.km_entrada, tramo.km_estado), (250, KmEstado.REGISTRADO))

    def test_pantalla_del_administrador(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        tramo = self.imprevisto(km=200, km_observado=True, obs='Odómetro trabado')
        self.entrar(self.admin_accesos)
        detalle = reverse('accesos:tramo_detalle', args=[tramo.pk])
        self.assertContains(self.client.get(detalle), 'Km observado: revisar')
        self.client.post(reverse('accesos:tramo_km', args=[tramo.pk]), {'km-motivo': 'Revisado', 'accion': 'validar'})
        tramo.refresh_from_db()
        self.assertEqual(tramo.km_estado, KmEstado.REGISTRADO)

    def test_portería_formulario_exige_observacion(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.entrar(self.vigilante)
        datos = {'transporte': self.f7j.pk, 'placa': 'F7J-011', 'km': 190, 'km_observado': 'on'}
        respuesta = self.client.post(reverse('accesos:porteria_post_ruta'), datos, follow=True)
        self.assertContains(respuesta, 'por qué registra un km observado')
        respuesta = self.client.post(reverse('accesos:porteria_post_ruta'), {**datos, 'obs': 'Marca 190'})
        self.assertIn('registrado=POST_RUTA', respuesta.url)
        self.assertEqual(Tramo.objects.get(clase=Tramo.Clase.POST_RUTA).km_estado, KmEstado.OBSERVADO)


class AvisosTests(ImprevistoBase):

    def test_campanita_avisa_imprevisto_y_observado(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.imprevisto(km=190, km_observado=True, obs='Marca 190')
        avisos = jornadas.avisos_flota(tiempo.ahora())
        titulos = [a.titulo for a in avisos]
        self.assertIn('F7J-011 · retorno imprevisto', titulos)
        self.assertIn('1 lectura de km observada', titulos)
        aviso = next(a for a in avisos if 'retorno imprevisto' in a.titulo)
        self.assertIn('10:00 · Olvidó carga o documentos', aviso.texto)
        observada = next(a for a in avisos if 'observada' in a.titulo)
        self.assertIn('km=observado', observada.url_query)
        # El aviso del imprevisto es del día; luego queda en el reporte.
        self.mover_reloj(MANANA, 9, 0)
        self.assertFalse(any('retorno imprevisto' in a.titulo for a in jornadas.avisos_flota(tiempo.ahora())))

    def test_la_campanita_se_ve_con_despacho_y_administrador(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.imprevisto()
        for usuario in (self.despacho, self.admin_accesos):
            self.entrar(usuario)
            self.assertContains(self.client.get(reverse('accesos:inicio')), 'retorno imprevisto')


class TrazabilidadTests(ImprevistoBase):

    def test_muestra_lo_programado_los_imprevistos_y_el_recorrido(self):
        self.dia_del_usuario()
        datos = trazabilidad.del_dia(self.f7j, HOY, tiempo.ahora(), self.config)
        eventos = datos['eventos']
        retorno = next(e for e in eventos if e.tipo == 'imprevisto')
        self.assertEqual(retorno.titulo, 'Retorno imprevisto · Olvidó carga o documentos')
        self.assertEqual((retorno.km, retorno.km_desde_anterior, retorno.fuera), (245, 45, '2 h 00 min'))
        post = next(e for e in eventos if e.tipo == 'post_ruta')
        self.assertEqual((post.km, post.km_desde_anterior, post.fuera), (312, 67, '5 h 05 min'))
        self.assertTrue(any(e.tipo == 'salida_ruta' and e.programada == time(8, 0) for e in eventos))  # lo programado
        self.assertEqual(datos['resumen']['imprevistos'], 1)
        km = datos['resumen']['km_turno']
        self.assertEqual((km.inicial, km.final, km.recorrido, km.minutos_en_ruta), (200, 312, 112, 425))

    def test_pantalla_y_excel(self):
        self.dia_del_usuario()
        self.entrar(self.consulta)
        filtros = {'placa': 'F7J-011', 'fecha': HOY.isoformat()}
        respuesta = self.client.get(TRAZABILIDAD, filtros)
        self.assertContains(respuesta, 'Retorno imprevisto')
        self.assertContains(respuesta, '(+45 km)')
        self.assertContains(respuesta, 'fuera del CD 2 h 00 min')
        self.assertContains(respuesta, 'inició con 200 y terminó con 312')
        libro = load_workbook(BytesIO(self.client.get(reverse('accesos:trazabilidad_exportar'), filtros).content))
        filas = list(libro.active.iter_rows(values_only=True))
        fila = next(dict(zip(filas[0], f)) for f in filas[1:] if 'Retorno imprevisto' in f[2])
        self.assertEqual((fila['Kilometraje'], fila['Km desde la lectura anterior']), (245, 45))

    def test_observado_se_marca_y_no_se_muestra_como_km(self):
        self.en_ruta(km=200)
        self.mover_reloj(HOY, 10, 0)
        self.imprevisto(km=190, km_observado=True, obs='Marca 190')
        evento = next(e for e in trazabilidad.del_dia(self.f7j, HOY, tiempo.ahora(), self.config)['eventos']
                      if e.tipo == 'imprevisto')
        self.assertIsNone(evento.km)
        self.assertIn('Km observado (190): por validar', evento.marcas)


class ReporteTests(ImprevistoBase):

    def filtros(self, **extra):
        return {'desde': HOY.isoformat(), 'hasta': HOY.isoformat(), **extra}

    def test_por_placa_y_dia_es_la_vista_principal(self):
        self.dia_del_usuario()
        self.entrar(self.consulta)
        respuesta = self.client.get(REPORTE, self.filtros())
        self.assertEqual(respuesta.context['vista'], 'placa_dia')
        fila = list(respuesta.context['page_obj'])[0]
        self.assertEqual((fila['km_inicial'], fila['km_final'], fila['km_turnos'], fila['en_ruta'], fila['imprevistos']),
                         (200, 312, 112, 425, 1))
        self.assertEqual(tiempo.a_lima(fila['fin']).time(), time(16, 5))
        self.assertEqual((fila['rutas_atendidas'], fila['rutas_programadas']), (1, 1))
        self.assertContains(respuesta, '7 h 05 min')
        self.assertContains(respuesta, '1 imprevisto')
        self.assertNotContains(respuesta, 'Por tipo de unidad')  # quedó solo en el Excel

    def test_turno_noche_suma_al_dia_de_su_fecha(self):
        lista_dia = self.dia_del_usuario()
        # Dos jornadas de la misma placa y fecha: día + noche → una sola fila.
        self.mover_reloj(HOY, 17, 0)
        self.interna(21, 0)
        self.mover_reloj(HOY, 21, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 5, 30)
        self.post_ruta(km=400)
        ahora = tiempo.ahora()
        lista, rutas, _ = reporte_jornadas.datos(HOY, HOY, {}, reporte_jornadas.leer_extra({}), ahora)
        self.assertEqual(len(lista), 2)
        filas = reporte_jornadas.filas_placa_dia(lista, rutas)
        self.assertEqual(len(filas), 1)
        self.assertEqual((filas[0]['km_dia'], filas[0]['km_noche'], filas[0]['km_turnos'], filas[0]['km_inicial'],
                          filas[0]['km_final'], filas[0]['turno_noche']), (112, 88, 200, 200, 400, True))
        self.assertEqual(lista_dia.pk, lista[0].pk)

    def test_resumen_por_placa_con_control_de_odometro(self):
        self.dia_del_usuario()
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 8, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 16, 0)
        self.post_ruta(km=400)
        self.entrar(self.consulta)
        filtros = {'desde': HOY.isoformat(), 'hasta': MANANA.isoformat(), 'vista': 'mes'}
        respuesta = self.client.get(REPORTE, filtros)
        fila = list(respuesta.context['page_obj'])[0]
        self.assertEqual((fila['dias'], fila['turnos'], fila['km_total'], fila['km_por_dia'], fila['cobertura']),
                         (2, 2, 200, 100.0, 100))
        self.assertEqual((fila['km_primera'], fila['km_ultima'], fila['km_odometro'], fila['diferencia']), (200, 400, 200, 0))
        self.assertEqual(fila['imprevistos'], 1)
        self.assertContains(respuesta, 'cuadra')

    def test_el_odometro_delata_los_huecos(self):
        self.dia_del_usuario()
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 8, 0)
        self.salida(self.abierto())
        self.mover_reloj(MANANA, 16, 0)
        self.post_ruta(km=None, km_no_leido=True, obs='Sin luz')  # el segundo turno no suma
        self.mover_reloj(MANANA, 18, 0)
        ahora = tiempo.ahora()
        lista, _, _ = reporte_jornadas.datos(HOY, MANANA, {}, reporte_jornadas.leer_extra({}), ahora)
        fila = reporte_jornadas.filas_mes(lista)[0]
        self.assertEqual((fila['km_total'], fila['cobertura'], fila['por_revisar']), (112, 50, 1))

    def test_excel_con_movimientos_y_resumen_por_placa(self):
        self.dia_del_usuario()
        self.entrar(self.consulta)
        libro = load_workbook(BytesIO(self.client.get(EXPORTAR, self.filtros()).content))
        self.assertEqual(libro.sheetnames, ['Resumen', 'Por placa y día', 'Por turno', 'Movimientos', 'Resumen por placa',
                                            'Calidad del km', 'Por tipo de unidad', 'Notas'])
        filas = list(libro['Movimientos'].iter_rows(values_only=True))
        movimientos = [dict(zip(filas[0], f)) for f in filas[1:]]
        self.assertEqual([m['Movimiento'] for m in movimientos], ['Pre-Ruta', 'Retorno imprevisto', 'Post-Ruta'])
        self.assertEqual([m['Km'] for m in movimientos], [200, 245, 312])
        self.assertEqual([m['Km desde la lectura anterior'] for m in movimientos], [None, 45, 67])
        self.assertEqual([m['Minutos fuera antes de entrar'] for m in movimientos], [None, 120, 305])
        self.assertEqual(movimientos[1]['Detalle'], 'Olvidó carga o documentos')
        self.assertEqual(movimientos[1]['Minutos en el CD'], 60)
        placa_dia = dict(zip(*list(libro['Por placa y día'].iter_rows(values_only=True))[:2]))
        self.assertEqual((placa_dia['Km inicial'], placa_dia['Km final'], placa_dia['Horas en ruta (min)'],
                          placa_dia['Retornos imprevistos']), (200, 312, 425, 1))
        notas = ' '.join(str(f[0]) for f in libro['Notas'].iter_rows(values_only=True))
        self.assertIn('Retorno imprevisto', notas)
        self.assertIn('km observado', notas)

    def test_dashboard_cuadra_con_el_reporte(self):
        self.dia_del_usuario()
        self.entrar(self.consulta)
        filtros = self.filtros(cd=self.lurin.pk)
        flota = self.client.get(reverse('accesos:dashboard'), {**filtros, 'vista': 'flota'})
        k_dashboard = flota.context['flota']['kpis']
        k_reporte = self.client.get(REPORTE, filtros).context['kpis']
        for clave in ('imprevistos', 'minutos_imprevistos', 'minutos_en_ruta', 'km_total'):
            self.assertEqual(k_dashboard[clave], k_reporte[clave], clave)
        self.assertEqual((k_reporte['imprevistos'], k_reporte['minutos_imprevistos'], k_reporte['minutos_en_ruta']), (1, 60, 425))
        self.assertContains(flota, '1 retorno imprevisto')

    def test_ranking_por_conductor_no_trae_km_ni_imprevistos(self):
        lista = self.dia_del_usuario()
        ahora = tiempo.ahora()
        todas = indicadores_flota.jornadas_del_periodo(HOY, HOY, {}, ahora)
        self.assertEqual(todas[0].pk, lista.pk)
        self.assertEqual(indicadores_flota.ranking(todas, 'placa')[0]['imprevistos'], 1)
        self.assertNotIn('imprevistos', indicadores_flota.ranking(todas, 'conductor')[0])
