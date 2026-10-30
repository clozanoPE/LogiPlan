"""
Calidad del kilometraje, Reporte de jornadas, motor único de indicadores y modo TV gerencial (30/10/2026).

Pedido de las áreas: Tiempo de Pre-Ruta = salida a ruta − ingreso (06:30 / 20:00); Tiempo de Post-Ruta = fin del
turno (16:00 / 06:15) − retorno real; km por turno y por día (ejemplo del usuario: 90 → 95 → 110 = 20 km). Decisión
del usuario: Portería no acepta un km menor al último registrado. Lo que se descarga debe cuadrar con el Dashboard y
con la TV.
"""
import importlib
from io import BytesIO

from django.apps import apps
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook

from .. import indicadores_flota, jornadas, kilometraje, pantalla, reporte_jornadas, servicios, tiempo
from ..models import Jornada, Programacion, RegistroAuditoria, Tramo
from .test_jornada import JornadaBase
from .test_programacion import HOY, MANANA

REPORTE = reverse('accesos:reporte_jornadas')
EXPORTAR = reverse('accesos:reporte_exportar')
KmEstado = Tramo.KmEstado


class EjemploDelUsuarioBase(JornadaBase):
    """Rutas de 08:00 y 11:15; sale a las 07:50 con 90 km (durmió en el CD sin registro), vuelve a las 11:00 con
    95 km, sale 11:30 y retorna a las 16:50 con 110 km: 20 km en el turno."""

    def armar_dia(self, retorno=(16, 50), km=(90, 95, 110)):
        self.mover_reloj(HOY, 5, 0)
        self.ocho, self.once = self.interna(8, 0), self.interna(11, 15)
        self.mover_reloj(HOY, 7, 50)
        self.pre = self.salida_sin_registro(km=km[0])
        self.mover_reloj(HOY, 11, 0)
        self.visita = self.entrar_en(self.once, km=km[1])
        self.mover_reloj(HOY, 11, 30)
        self.salida(self.visita)
        self.mover_reloj(HOY, *retorno)
        self.post, _ = self.post_ruta(km=km[2])
        return self.jornada()


# --- 1. Portería: validación del kilometraje ---

class ValidacionKmPorteriaTests(JornadaBase):

    def preparar(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.interna(11, 15)
        self.mover_reloj(HOY, 7, 50)
        self.salida_sin_registro(km=1000)
        self.mover_reloj(HOY, 16, 30)

    def registrar(self, km, **extra):
        return servicios.registrar_post_ruta(transporte_id=self.f7j.pk, cd=self.lurin, km=km, obs=extra.pop('obs', ''),
                                             conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora(), **extra)

    def test_no_acepta_un_km_menor_al_ultimo(self):
        self.preparar()
        with self.assertRaises(ValidationError) as error:
            self.registrar(999)
        self.assertEqual(error.exception.code, 'km_menor')
        self.assertIn('1.000 km', error.exception.messages[0])
        self.assertEqual(self.jornada().estado, Jornada.Estado.EN_RUTA)  # no se registró nada

    def test_valores_de_relleno_se_bloquean(self):
        self.preparar()
        for km in (0, 99999, 999999):
            with self.assertRaises(ValidationError) as error:
                self.registrar(km, km_observado=True, obs='x')
            self.assertEqual(error.exception.code, 'km_relleno')

    def test_km_igual_o_que_no_cabe_en_el_tiempo_se_rechaza(self):
        """Salió a las 07:50 con 1.000 y vuelve a las 16:30 (8 h 40 min): como máximo 694 km a 80 km/h. Igual al
        anterior o más del tope se rechaza; el vigilante no lo puede confirmar (desde el 30/10/2026)."""
        self.preparar()
        for km, codigo in ((1000, 'km_igual'), (1695, 'km_excesivo')):
            with self.assertRaises(ValidationError) as error:
                self.registrar(km)
            self.assertEqual(error.exception.code, codigo)
        tramo, _ = self.registrar(1694)
        self.assertEqual((tramo.km_entrada, tramo.km_estado), (1694, KmEstado.REGISTRADO))

    def test_esta_saliendo_acepta_el_mismo_km(self):
        """Durmió en el CD: salir con el mismo km que tenía es lo normal (no pide confirmar)."""
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.f7j.km_actual = None
        Tramo.objects.create  # noqa: B018 (solo para dejar claro que no hay tramos previos)
        self.mover_reloj(HOY, 7, 50)
        self.salida_sin_registro(km=500)
        self.assertEqual(kilometraje.ultima_lectura(self.f7j.pk).km_entrada, 500)

    def test_odometro_no_leido_exige_el_porque_y_queda_sin_km(self):
        self.preparar()
        with self.assertRaises(ValidationError):
            self.registrar(None, km_no_leido=True)
        tramo, _ = self.registrar(None, km_no_leido=True, obs='Tablero apagado')
        self.assertEqual((tramo.km_entrada, tramo.km_estado, tramo.km_motivo), (None, KmEstado.NO_LEIDO, 'Tablero apagado'))
        # La lectura no leída no es referencia: el último válido sigue siendo 1.000.
        self.assertEqual(kilometraje.ultima_lectura(self.f7j.pk).km_entrada, 1000)
        # La Pre-Ruta automática del día siguiente copia el estado (sin km).
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 45)
        pre = self.salida(self.abierto())
        self.assertEqual((pre.clase, pre.km_entrada, pre.km_estado), (Tramo.Clase.PRE_RUTA, None, KmEstado.NO_LEIDO))

    def test_pantalla_y_formulario(self):
        self.preparar()
        self.entrar(self.vigilante)
        respuesta = self.client.get(reverse('accesos:porteria'), {'placa': 'F7J-011', 'modo': 'post_ruta'})
        self.assertContains(respuesta, 'No se pudo leer el odómetro')
        self.assertContains(respuesta, 'data-ultimo="1000"')
        url = reverse('accesos:porteria_post_ruta')
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'km': 900, 'placa': 'F7J-011'}, follow=True)
        self.assertContains(respuesta, 'es menor al último registrado')
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'km_no_leido': 'on', 'placa': 'F7J-011'}, follow=True)
        self.assertContains(respuesta, 'por qué no se pudo leer el odómetro')
        respuesta = self.client.post(url, {'transporte': self.f7j.pk, 'km_no_leido': 'on', 'obs': 'Sin luz en el tablero',
                                           'placa': 'F7J-011'})
        self.assertIn('registrado=POST_RUTA', respuesta.url)
        self.assertEqual(Tramo.objects.get(clase=Tramo.Clase.POST_RUTA).km_estado, KmEstado.NO_LEIDO)


# --- 2. Administrador: excluir, restituir, corregir y cerrar sin retorno ---

class CorreccionesKmTests(EjemploDelUsuarioBase):

    def test_excluir_y_restituir_siguen_las_copias(self):
        jornada = self.armar_dia()
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 45)
        copia = self.salida(self.abierto())  # Pre-Ruta automática: copia los 110 km de la pernocta
        self.assertEqual(copia.km_entrada, 110)
        kilometraje.excluir(self.post, self.admin_accesos, motivo='Lectura dudosa')
        copia.refresh_from_db()
        self.f7j.refresh_from_db()
        self.assertEqual(copia.km_estado, KmEstado.EXCLUIDO)
        self.assertEqual(self.f7j.km_actual, 95)  # el maestro solo mira lecturas válidas
        jornada.refresh_from_db()
        self.assertEqual(kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk].motivo, 'Lectura final excluida')
        self.assertTrue(RegistroAuditoria.objects.filter(entidad='Tramo', objeto_id=copia.pk,
                                                         motivo__contains='Copia automática').exists())
        kilometraje.restituir(self.post, self.admin_accesos, motivo='Era correcta')
        copia.refresh_from_db()
        self.assertEqual(copia.km_estado, KmEstado.REGISTRADO)

    def test_corregir_no_admite_menor_que_la_anterior_salvo_cambio_de_odometro(self):
        self.armar_dia()
        datos = dict(motivo='Error de tipeo', ahora=tiempo.ahora(), entrada_en=self.post.entrada_en, salida_en=None,
                     obs_entrada='', obs_salida='', programacion=None, conductor=self.conductor)
        with self.assertRaises(ValidationError):
            servicios.corregir_tramo(self.post, self.admin_accesos, km_entrada=80, **datos)
        servicios.corregir_tramo(self.post, self.admin_accesos, km_entrada=80, km_reinicio=True, **datos)
        self.post.refresh_from_db()
        self.assertEqual((self.post.km_entrada, self.post.km_reinicio), (80, True))
        jornada = self.jornada()
        self.assertEqual(kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk].motivo,
                         'Cambio de odómetro en el turno')

    def test_corregir_no_admite_mayor_que_la_siguiente(self):
        self.armar_dia()
        visita = Tramo.objects.get(pk=self.visita.pk)
        with self.assertRaises(ValidationError):
            servicios.corregir_tramo(visita, self.admin_accesos, motivo='x', ahora=tiempo.ahora(),
                                     entrada_en=visita.entrada_en, salida_en=visita.salida_en, km_entrada=200,
                                     obs_entrada='', obs_salida='', programacion=self.once, conductor=self.conductor)

    def test_pantallas_del_administrador(self):
        self.armar_dia()
        self.entrar(self.admin_accesos)
        detalle = reverse('accesos:tramo_detalle', args=[self.post.pk])
        self.assertContains(self.client.get(detalle), 'Excluir lectura de km')
        self.client.post(reverse('accesos:tramo_km', args=[self.post.pk]), {'km-motivo': 'Odómetro dudoso',
                                                                             'accion': 'excluir'})
        self.post.refresh_from_db()
        self.assertEqual(self.post.km_estado, KmEstado.EXCLUIDO)
        self.assertContains(self.client.get(detalle), 'Restituir lectura de km')
        self.assertContains(self.client.get(reverse('accesos:tramo_corregir', args=[self.post.pk])), 'Cambio de odómetro')
        for usuario in (self.despacho, self.consulta, self.vigilante):
            self.entrar(usuario)
            self.assertEqual(self.client.post(reverse('accesos:tramo_km', args=[self.post.pk]),
                                              {'km-motivo': 'x'}).status_code, 403)


class CerrarSinRetornoTests(JornadaBase):

    def preparar(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.quince = self.interna(15, 0)
        self.mover_reloj(HOY, 7, 50)
        self.salida_sin_registro(km=1000)
        return self.jornada()

    def test_cierra_la_jornada_y_las_pendientes_quedan_no_atendidas(self):
        jornada = self.preparar()
        with self.assertRaises(ValidationError):  # el turno todavía no termina
            servicios.cerrar_jornada_sin_retorno(jornada, self.admin_accesos, motivo='x', ahora=tiempo.ahora())
        self.mover_reloj(MANANA, 9, 0)
        servicios.cerrar_jornada_sin_retorno(jornada, self.admin_accesos, motivo='No se registró el retorno',
                                             ahora=tiempo.ahora())
        jornada.refresh_from_db()
        self.quince.refresh_from_db()
        self.assertEqual((jornada.estado, jornada.cierre_sin_retorno), (Jornada.Estado.CERRADA, True))
        self.assertTrue(self.quince.no_atendida_por_cierre)
        self.assertEqual(jornadas.estado_visible(jornada, tiempo.ahora()).codigo, 'CERRADA_SIN_RETORNO')
        self.assertIsNone(jornadas.minutos_post_ruta(jornada))
        km = kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk]
        self.assertEqual((km.valido, km.por_revisar, km.motivo), (False, False, 'Cerrada sin retorno registrado'))

    def test_pantalla_solo_administrador(self):
        jornada = self.preparar()
        self.mover_reloj(MANANA, 9, 0)
        url = reverse('accesos:jornada_cerrar', args=[jornada.pk])
        self.entrar(self.despacho)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.entrar(self.admin_accesos)
        traza = self.client.get(reverse('accesos:trazabilidad'), {'placa': 'F7J011', 'fecha': HOY.isoformat()})
        self.assertContains(traza, url)
        respuesta = self.client.post(url, {'cerrar-motivo': 'Retorno no registrado'})
        self.assertEqual(respuesta.status_code, 302)
        jornada.refresh_from_db()
        self.assertTrue(jornada.cierre_sin_retorno)


class PreRutaDeOtroTurnoTests(JornadaBase):
    """Hallazgo del 29/10/2026: la unidad entró desde afuera un día, no salió, y su salida días después dejaba la
    jornada del nuevo día «Planificada» aunque saliera a ruta."""

    def test_la_salida_cierra_la_espera_y_graba_la_pre_ruta_del_turno(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 11, 0)
        servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=5000, obs='',
                                           conductor_id=None, usuario=self.vigilante, ahora=tiempo.ahora())
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 7, 40)
        resultado = servicios.evaluar_porteria('F7J011', self.lurin, tiempo.ahora(), self.config)
        self.assertEqual(resultado.tipo_salida, servicios.SALIDA_PRE_RUTA_AUTOMATICA)
        pre = self.salida(self.abierto())
        manana = self.jornada(MANANA)
        self.assertEqual((pre.clase, pre.jornada, pre.km_entrada), (Tramo.Clase.PRE_RUTA, manana, 5000))
        self.assertEqual(manana.estado, Jornada.Estado.EN_RUTA)
        self.assertEqual(jornadas.minutos_pre_ruta(manana), 70)  # 06:30 → 07:40
        espera = Tramo.objects.get(jornada=self.jornada(HOY))
        self.assertIn('espera en el CD', espera.obs_salida)
        self.assertEqual(self.jornada(HOY).estado, Jornada.Estado.PLANIFICADA)  # ese día no salió


# --- 3. Fórmulas: Pre-Ruta, Post-Ruta y km por jornada ---

class FormulasTests(EjemploDelUsuarioBase):

    def test_ejemplo_del_usuario_veinte_km(self):
        jornada = self.armar_dia()
        km = kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk]
        self.assertEqual((km.inicial, km.final, km.recorrido, km.valido), (90, 110, 20, True))
        self.assertEqual(jornadas.minutos_pre_ruta(jornada), 80)    # 06:30 → 07:50
        self.assertEqual(jornadas.minutos_post_ruta(jornada), -50)  # fin 16:00, volvió 16:50
        self.assertEqual(jornadas.texto_post_ruta(-50), '50 min después')

    def test_post_ruta_antes_del_fin_del_turno_es_positiva(self):
        jornada = self.armar_dia(retorno=(15, 30))
        self.assertEqual(jornadas.minutos_post_ruta(jornada), 30)
        self.assertEqual(jornadas.texto_post_ruta(30), '30 min antes')

    def test_motivos_por_los_que_el_km_no_suma(self):
        jornada = self.armar_dia()
        # Dato histórico (antes de los candados de portería no se podía registrar): +1.405 km en el turno.
        Tramo.objects.filter(pk=self.post.pk).update(km_entrada=1500)
        km = kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk]
        self.assertEqual((km.valido, km.por_revisar), (False, True))
        self.assertIn('Más de 800 km', km.motivo)

    def test_turno_en_curso_y_km_fuera_de_jornada(self):
        self.mover_reloj(HOY, 5, 0)
        self.interna(8, 0)
        self.mover_reloj(HOY, 7, 50)
        self.salida_sin_registro(km=100)
        jornada = self.jornada()
        km = kilometraje.km_de_jornadas([jornada], tiempo.ahora())[jornada.pk]
        self.assertEqual((km.motivo, km.por_revisar), (kilometraje.EN_CURSO, False))
        self.mover_reloj(HOY, 16, 0)
        self.post_ruta(km=150)
        # Duerme fuera: sale después de su Post-Ruta y al día siguiente entra desde afuera con 180 km.
        self.mover_reloj(HOY, 18, 0)
        self.salida(self.abierto(), obs='Pernocta en otra sede')
        self.mover_reloj(MANANA, 5, 0)
        self.interna(8, 0, fecha=MANANA)
        self.mover_reloj(MANANA, 6, 50)
        servicios.registrar_inicio_jornada(transporte_id=self.f7j.pk, cd=self.lurin, km=180, obs='', conductor_id=None,
                                           usuario=self.vigilante, ahora=tiempo.ahora())
        manana = self.jornada(MANANA)
        self.assertEqual(kilometraje.km_de_jornadas([manana], tiempo.ahora())[manana.pk].fuera, 30)


# --- 4. Cuadre: Dashboard = Reporte (pantalla y Excel) = TV ---

class CuadreTests(EjemploDelUsuarioBase):

    def test_las_mismas_cifras_en_dashboard_reporte_excel_y_tv(self):
        self.armar_dia()
        self.mover_reloj(HOY, 17, 0)
        filtros = {'desde': HOY.isoformat(), 'hasta': HOY.isoformat(), 'cd': self.lurin.pk, 'turno': 'DIA'}
        self.entrar(self.consulta)
        k_dashboard = self.client.get(reverse('accesos:dashboard'), {**filtros, 'vista': 'flota'}).context['flota']['kpis']
        respuesta = self.client.get(REPORTE, filtros)
        k_reporte = respuesta.context['kpis']
        claves = ('jornadas', 'pre_ruta_promedio', 'pct_pre_ruta_a_tiempo', 'post_ruta_promedio_dia', 'pct_cierre_en_horario',
                  'km_total', 'km_turnos', 'km_por_unidad_dia', 'pct_salidas_a_tiempo')
        self.assertEqual({c: k_reporte[c] for c in claves}, {c: k_dashboard[c] for c in claves})
        self.assertEqual((k_reporte['km_total'], k_reporte['pre_ruta_promedio'], k_reporte['post_ruta_promedio_dia']),
                         (20, 80, -50))
        self.assertContains(respuesta, '20 km')

        libro = load_workbook(BytesIO(self.client.get(EXPORTAR, filtros).content))
        self.assertEqual(libro.sheetnames, ['Resumen', 'Por placa y día', 'Por turno', 'Movimientos', 'Resumen por placa',
                                            'Calidad del km', 'Por tipo de unidad', 'Notas'])
        resumen = {fila[0]: fila[1] for fila in libro['Resumen'].iter_rows(min_row=2, values_only=True)}
        self.assertEqual((resumen['Km totales'], resumen['Pre-Ruta promedio (min)'],
                          resumen['Post-Ruta promedio turno día (min)']), (20, 80, -50))
        por_turno = list(libro['Por turno'].iter_rows(values_only=True))
        encabezados = por_turno[0]
        fila = dict(zip(encabezados, por_turno[1]))
        self.assertEqual(len(por_turno) - 1, k_reporte['jornadas'])
        self.assertEqual((fila['Placa'], fila['Pre-Ruta (min)'], fila['Post-Ruta (min)'], fila['Km inicial'],
                          fila['Km final'], fila['Km recorridos'], fila['Km válido']), ('F7J-011', 80, -50, 90, 110, 20, 'SI'))
        placa_dia = dict(zip(*list(libro['Por placa y día'].iter_rows(values_only=True))[:2]))
        self.assertEqual((placa_dia['Km del día'], placa_dia['Km total (combustible)'], placa_dia['Km inicial'],
                          placa_dia['Km final']), (20, 20, 90, 110))
        mes = dict(zip(*list(libro['Resumen por placa'].iter_rows(values_only=True))[:2]))
        self.assertEqual((mes['Km total (combustible)'], mes['Km por odómetro'], mes['Diferencia (huecos)'],
                          mes['Cobertura del km (%)']), (20, 20, 0, 100))
        movimientos = list(libro['Movimientos'].iter_rows(values_only=True))
        self.assertEqual([dict(zip(movimientos[0], f))['Km desde la lectura anterior'] for f in movimientos[1:]],
                         [None, 5, 15])  # 90 → 95 → 110

        datos = pantalla.datos(self.lurin, tiempo.ahora())
        tarjetas = {t['clave']: t for t in datos['indicadores']}
        self.assertEqual(tarjetas['km']['valor'], '20 km')
        self.assertEqual(tarjetas['pre_ruta']['valor'], '80 min')
        self.assertIn('50 min después', tarjetas['retornos']['detalle'])
        self.assertEqual(datos['foco']['turno'], 'DIA')
        self.assertEqual(datos['pagina_turno']['km'][0], {'placa': 'F7J-011', 'km': 20, 'motivo': '', 'por_revisar': False})
        self.assertNotIn(self.conductor.nombre_completo, str(datos['pagina_turno']))  # minutos y km por placa

    def test_filtros_propios_del_reporte_recalculan_el_resumen(self):
        self.armar_dia()
        self.mover_reloj(HOY, 17, 0)
        self.entrar(self.despacho)
        base = {'desde': HOY.isoformat(), 'hasta': HOY.isoformat()}
        self.assertEqual(self.client.get(REPORTE, {**base, 'km': 'valido'}).context['kpis']['jornadas'], 1)
        self.assertEqual(self.client.get(REPORTE, {**base, 'km': 'por_revisar'}).context['kpis']['jornadas'], 0)
        self.assertEqual(self.client.get(REPORTE, {**base, 'retorno': 'FUERA_DE_HORARIO'}).context['kpis']['jornadas'], 1)
        self.assertEqual(self.client.get(REPORTE, {**base, 'pre': 'a_tiempo'}).context['kpis']['jornadas'], 0)
        for vista in ('placa_dia', 'tipo', 'calidad'):
            self.assertEqual(self.client.get(REPORTE, {**base, 'vista': vista}).status_code, 200)
        self.assertEqual(self.client.get(REPORTE, {**base, 'estado': 'nada', 'km': 'x', 'orden': 'y'}).status_code, 200)


class PermisosYEnlacesTests(EjemploDelUsuarioBase):

    def test_quien_ve_el_reporte(self):
        for usuario in (self.admin_accesos, self.despacho, self.consulta, self.superusuario):
            self.entrar(usuario)
            self.assertEqual(self.client.get(REPORTE).status_code, 200, usuario)
            self.assertEqual(self.client.get(EXPORTAR).status_code, 200, usuario)
        for usuario in (self.vigilante, self.comercial, self.sin_grupo):
            self.entrar(usuario)
            self.assertIn(self.client.get(REPORTE).status_code, (302, 403), usuario)

    def test_enlaces_desde_el_menu_el_dashboard_y_la_campanita(self):
        self.armar_dia()
        self.entrar(self.admin_accesos)
        self.assertContains(self.client.get(reverse('accesos:inicio')), REPORTE)
        respuesta = self.client.get(reverse('accesos:dashboard'), {'vista': 'flota'})
        self.assertContains(respuesta, 'Ver reporte detallado')
        self.assertContains(respuesta, 'Km recorridos')
        # Lectura no leída: aviso en la campanita con enlace a «Calidad del km».
        Tramo.objects.filter(pk=self.post.pk).update(km_entrada=None, km_estado=KmEstado.NO_LEIDO, km_motivo='x')
        avisos = jornadas.avisos_flota(tiempo.ahora())
        aviso = next(a for a in avisos if 'sin leer' in a.titulo)
        self.assertEqual(aviso.url_nombre, 'accesos:reporte_jornadas')
        self.assertIn('vista=calidad', aviso.url_query)


# --- 5. Migración 0018 ---

class MigracionCalidadKmTests(EjemploDelUsuarioBase):

    def test_excluye_los_valores_de_relleno_y_su_reversa(self):
        self.armar_dia()
        Tramo.objects.filter(pk=self.post.pk).update(km_entrada=99999)
        migracion = importlib.import_module('accesos.migrations.0018_datos_calidad_km')
        migracion.adelante(apps, None)
        migracion.adelante(apps, None)  # idempotente
        self.post.refresh_from_db()
        self.f7j.refresh_from_db()
        self.assertEqual(self.post.km_estado, KmEstado.EXCLUIDO)
        self.assertEqual(self.f7j.km_actual, 95)
        self.assertEqual(Tramo.objects.filter(km_estado=KmEstado.EXCLUIDO).count(), 1)
        migracion.atras(apps, None)
        self.post.refresh_from_db()
        self.assertEqual(self.post.km_estado, KmEstado.REGISTRADO)
