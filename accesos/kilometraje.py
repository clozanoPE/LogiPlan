"""
Kilometraje de cada transporte. Los tramos válidos son la fuente de verdad: Vigilancia registra el
kilometraje en cada entrada, en el inicio de jornada, en la Post-Ruta y en la salida a ruta de una unidad que
durmió en el CD sin registro. El maestro de Transporte guarda una copia (`km_actual`, `km_actualizado_en`,
`km_origen`) para mostrarla sin consultas extra; se recalcula cada vez que se guarda un tramo (Tramo.save),
así que también sigue a las correcciones y anulaciones del Administrador.

Calidad del kilometraje (30/10/2026, decisión del usuario tras revisar los datos reales):
- Solo cuentan las lecturas «registradas» (`Tramo.km_valido`): las «No leído» (el vigilante no pudo leer el
  odómetro) y las «Excluidas» por el Administrador no sirven de referencia ni suman en el reporte.
- Portería no acepta un km menor al último válido, ni valores de relleno (0, 99999…).
- Candados del 30/10/2026 (decisión del usuario: «si a las 8 registró 200 km, a las 10 no puede registrar 190 ni
  200»): en toda entrada después de una salida el km debe ser MAYOR al último válido, y el recorrido debe caber en
  el tiempo que la unidad estuvo fuera del CD (horas × `ConfiguracionAccesos.velocidad_maxima_kmh`, 80 km/h). El
  vigilante no puede forzarlo: corrige el número o, si el tablero marca ese valor, lo registra como «km
  observado» (OBSERVADO): el camión pasa, la lectura se guarda pero no cuenta en ningún indicador ni sirve de
  referencia hasta que el Administrador la valide, la corrija o la excluya. Cada intento rechazado queda en la
  auditoría (`KM_RECHAZADO`).
- La Pre-Ruta automática copia el km de la pernocta (o de la espera en el CD): es una COPIA y sigue a su origen
  cuando el Administrador corrige o excluye la lectura (`copias_de`, `propagar_a_copias`).
- Km de una jornada = última lectura (Post-Ruta) − primera lectura (Pre-Ruta) del turno (`km_de_jornadas`).
"""
import math
from dataclasses import dataclass, field
from datetime import time, timedelta

from django.core.exceptions import ValidationError

from . import tiempo
from .models import ConfiguracionAccesos, Jornada, Tramo, Transporte

Clase = Tramo.Clase
KmEstado = Tramo.KmEstado

# En el reporte, un turno con más km no suma (queda por revisar).
TOPE_TURNO = 800
TEXTO_NO_LEIDO = "Si no se puede leer el odómetro, marque «No se pudo leer el odómetro»."
TEXTO_OBSERVADO = ("Corrija el número; si el tablero marca ese valor, marque «El tablero marca este valor (km "
                   "observado)» y explíquelo en la observación.")
# Rechazos que el vigilante puede registrar como «km observado» (el relleno no: se marca «No se pudo leer»).
CODIGOS_OBSERVABLES = ('km_menor', 'km_igual', 'km_excesivo')
CODIGOS_KM = ('km_relleno',) + CODIGOS_OBSERVABLES


def miles(numero):
    return f"{numero:,}".replace(',', '.')


def es_relleno(km):
    """Valores que se escriben cuando no se lee el odómetro: 0 o cinco o más dígitos iguales (99999, 999999…)."""
    texto = str(km)
    return km == 0 or (len(texto) >= 5 and len(set(texto)) == 1)


def lecturas_validas(transporte_id):
    return Tramo.objects.filter(transporte_id=transporte_id, vigente=True, km_entrada__isnull=False,
                                km_estado=KmEstado.REGISTRADO)


def ultima_lectura(transporte_id):
    """Tramo válido más reciente de la placa con una lectura válida, o None."""
    return lecturas_validas(transporte_id).select_related('cd').order_by('-entrada_en', '-id').first()


def momento_lectura(tramo):
    """Cuándo se leyó el kilometraje: en la Pre-Ruta, al salir a ruta; en los demás tramos, al entrar."""
    if tramo.clase == Clase.PRE_RUTA and tramo.salida_en:
        return tramo.salida_en
    return tramo.entrada_en


def _orden(tramo):
    return (momento_lectura(tramo), tramo.entrada_en, tramo.pk or 0)


def origen(tramo):
    if tramo.clase == Clase.POST_RUTA:
        return "Post-Ruta (fin de jornada)"
    if tramo.clase == Clase.PRE_RUTA:
        return "Inicio de jornada (ingreso)" if tramo.vigilante_entrada_id else "Salida a ruta (Pre-Ruta)"
    return "Entrada al CD"


def actualizar(transporte_id):
    """Recalcula la copia del último kilometraje en el maestro. Usa update() para no tocar los datos de
    modificación del transporte (no es un cambio del usuario sobre el maestro)."""
    tramo = ultima_lectura(transporte_id)
    if tramo is None:
        datos = {'km_actual': None, 'km_actualizado_en': None, 'km_origen': ''}
    else:
        datos = {'km_actual': tramo.km_entrada, 'km_actualizado_en': momento_lectura(tramo),
                 'km_origen': f"{origen(tramo)} · CD {tramo.cd}"[:60]}
    Transporte.objects.filter(pk=transporte_id).update(**datos)
    return datos


def _texto_lectura(tramo):
    return f"{miles(tramo.km_entrada)} km el {tiempo.a_lima(momento_lectura(tramo)):%d/%m %H:%M}"


def duracion(minutos):
    horas, resto = divmod(max(int(minutos), 0), 60)
    return f"{horas} h {resto:02d} min" if horas else f"{resto} min"


# --- VALIDACIÓN ---

def desde_cuando(ultima):
    """Desde cuándo la unidad pudo recorrer kilómetros después de la última lectura: su salida del CD (o el
    momento de la lectura, si ese tramo no tiene salida)."""
    return max(ultima.salida_en or momento_lectura(ultima), momento_lectura(ultima))


def tope_por_tiempo(desde, hasta, velocidad):
    """Máximo de km razonable entre dos momentos a `velocidad` km/h (al menos 1 km)."""
    horas = max((hasta - desde).total_seconds(), 0) / 3600
    return max(math.ceil(horas * velocidad), 1)


class KmRechazado(ValidationError):
    """Rechazo de una lectura de portería. `base` es el porqué sin la indicación al vigilante: se usa como motivo
    de un «km observado» y en la auditoría del intento rechazado."""

    def __init__(self, base, indicacion, code):
        super().__init__(f"{base} {indicacion}", code=code)
        self.base = base


def validar_nueva(transporte_id, km, *, ahora, velocidad, exigir_variacion=True):
    """Lectura nueva de Portería frente a la última válida de la placa. Lanza KmRechazado con código
    `km_relleno`, `km_menor`, `km_igual` (en una entrada después de salir, el km debe subir) o `km_excesivo`
    (el recorrido no cabe en el tiempo fuera del CD a `velocidad` km/h). `exigir_variacion=False` solo en
    «Está saliendo» (durmió en el CD: el km puede ser igual)."""
    if es_relleno(km):
        raise KmRechazado(f"El kilometraje {miles(km)} no es una lectura válida.", TEXTO_NO_LEIDO, 'km_relleno')
    ultima = ultima_lectura(transporte_id)
    if ultima is None:
        return
    if km < ultima.km_entrada:
        raise KmRechazado(f"El kilometraje ({miles(km)}) es menor al último registrado ({_texto_lectura(ultima)}).",
                          TEXTO_OBSERVADO, 'km_menor')
    if exigir_variacion and km == ultima.km_entrada:
        raise KmRechazado(f"El kilometraje ({miles(km)}) es igual al último registrado ({_texto_lectura(ultima)}): "
                          "la unidad salió del CD, así que debe marcar más kilómetros.", TEXTO_OBSERVADO, 'km_igual')
    desde = desde_cuando(ultima)
    tope = tope_por_tiempo(desde, ahora, velocidad)
    if km - ultima.km_entrada > tope:
        raise KmRechazado(f"Son {miles(km - ultima.km_entrada)} km desde la última lectura ({_texto_lectura(ultima)}) "
                          f"en {duracion((ahora - desde).total_seconds() / 60)} fuera del CD: el máximo razonable es "
                          f"{miles(tope)} km (a {velocidad} km/h).", TEXTO_OBSERVADO, 'km_excesivo')


def copias_de(tramo):
    """Tramos automáticos que copiaron el km de este al empezar justo cuando este terminó: la Pre-Ruta que cierra
    una pernocta (o una espera en el CD) y la Post-Ruta de «Convertir en Post-Ruta»."""
    if not (tramo.pk and tramo.salida_en):
        return Tramo.objects.none()
    return (Tramo.objects.filter(transporte_id=tramo.transporte_id, vigente=True, vigilante_entrada__isnull=True,
                                 clase__in=[Clase.PRE_RUTA, Clase.POST_RUTA], entrada_en=tramo.salida_en)
            .exclude(pk=tramo.pk))


def vecinas(tramo):
    """Lecturas válidas de la placa justo antes y justo después de la del tramo (sin contar sus copias)."""
    excluidos = {tramo.pk, *copias_de(tramo).values_list('pk', flat=True)}
    lecturas = sorted((t for t in lecturas_validas(tramo.transporte_id) if t.pk not in excluidos), key=_orden)
    clave = _orden(tramo)
    anteriores = [t for t in lecturas if _orden(t) < clave]
    siguientes = [t for t in lecturas if _orden(t) > clave]
    return (anteriores[-1] if anteriores else None), (siguientes[0] if siguientes else None)


def validar_correccion(tramo, km, *, reinicio=False, velocidad=None):
    """Corrección del Administrador: la lectura debe quedar entre la válida anterior y la siguiente, y el recorrido
    desde la anterior debe caber en el tiempo fuera del CD (`velocidad` km/h, por defecto la de Configuración). Un
    km menor al anterior solo se acepta como cambio de odómetro."""
    if es_relleno(km):
        raise ValidationError(f"El kilometraje {miles(km)} no es una lectura válida. Si no se conoce el valor real, "
                              "excluya la lectura.")
    anterior, siguiente = vecinas(tramo)
    if anterior and km < anterior.km_entrada and not reinicio:
        raise ValidationError(f"El kilometraje ({miles(km)}) es menor a la lectura anterior ({_texto_lectura(anterior)}). "
                              "Si se cambió el odómetro, marque «Cambio de odómetro»; si la anterior es la errada, "
                              "corríjala o exclúyala primero.")
    if anterior and not reinicio and km > anterior.km_entrada:
        if velocidad is None:
            velocidad = ConfiguracionAccesos.obtener().velocidad_maxima_kmh
        desde = desde_cuando(anterior)
        tope = tope_por_tiempo(desde, max(momento_lectura(tramo), desde), velocidad)
        if km - anterior.km_entrada > tope:
            raise ValidationError(f"Son {miles(km - anterior.km_entrada)} km desde la lectura anterior "
                                  f"({_texto_lectura(anterior)}): el máximo razonable para ese tiempo es {miles(tope)} "
                                  f"km (a {velocidad} km/h). Si la anterior es la errada, corríjala o exclúyala primero.")
    if siguiente and km > siguiente.km_entrada and not siguiente.km_reinicio:
        raise ValidationError(f"El kilometraje ({miles(km)}) es mayor a la lectura siguiente ({_texto_lectura(siguiente)}). "
                              "Si la siguiente es la errada, corríjala o exclúyala primero.")


def propagar_a_copias(tramo, usuario, motivo):
    """Las copias automáticas siguen a su lectura de origen (valor, estado y motivo)."""
    from . import auditoria  # import diferido: auditoria importa los modelos
    for copia in copias_de(tramo):
        if (copia.km_entrada, copia.km_estado, copia.km_motivo) == (tramo.km_entrada, tramo.km_estado, tramo.km_motivo):
            continue
        foto = auditoria.foto(copia)
        copia.km_entrada, copia.km_estado, copia.km_motivo = tramo.km_entrada, tramo.km_estado, tramo.km_motivo
        copia.save()
        auditoria.registrar_modificacion(usuario, copia, foto, accion=auditoria.Accion.CORREGIR,
                                         motivo=f"Copia automática del km del tramo {tramo.pk}: {motivo}")
        propagar_a_copias(copia, usuario, motivo)


def excluir(tramo, usuario, *, motivo):
    """El Administrador excluye una lectura errada sin valor real conocido: no suma ni sirve de referencia."""
    from . import auditoria
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indique el motivo.")
    tramo.refresh_from_db()  # sus copias se buscan por la salida guardada
    if not (tramo.vigente and tramo.km_entrada is not None
            and tramo.km_estado in (KmEstado.REGISTRADO, KmEstado.OBSERVADO)):
        raise ValidationError("Solo se excluye una lectura registrada u observada de un tramo válido.")
    foto = auditoria.foto(tramo)
    tramo.km_estado = KmEstado.EXCLUIDO
    tramo.km_motivo = motivo[:300]
    tramo.save()
    auditoria.registrar_modificacion(usuario, tramo, foto, accion=auditoria.Accion.CORREGIR,
                                     motivo=f"Lectura de km excluida: {motivo}")
    propagar_a_copias(tramo, usuario, f"lectura excluida: {motivo}")
    return tramo


def validar_observada(tramo, usuario, *, motivo):
    """El Administrador confirma una lectura «observada» (el tablero sí marcaba ese valor): pasa a contar si queda
    entre la lectura anterior y la siguiente y cabe en el tiempo fuera del CD."""
    from . import auditoria
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indique el motivo.")
    tramo.refresh_from_db()
    if not (tramo.vigente and tramo.km_estado == KmEstado.OBSERVADO and tramo.km_entrada is not None):
        raise ValidationError("La lectura no está observada.")
    validar_correccion(tramo, tramo.km_entrada, reinicio=tramo.km_reinicio)
    foto = auditoria.foto(tramo)
    tramo.km_estado = KmEstado.REGISTRADO
    tramo.km_motivo = ''
    tramo.save()
    auditoria.registrar_modificacion(usuario, tramo, foto, accion=auditoria.Accion.CORREGIR,
                                     motivo=f"Lectura de km observada validada: {motivo}")
    propagar_a_copias(tramo, usuario, f"lectura validada: {motivo}")
    return tramo


def restituir(tramo, usuario, *, motivo):
    """Vuelve a contar una lectura excluida, si queda entre la anterior y la siguiente."""
    from . import auditoria
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indique el motivo.")
    tramo.refresh_from_db()
    if not (tramo.vigente and tramo.km_estado == KmEstado.EXCLUIDO):
        raise ValidationError("La lectura no está excluida.")
    validar_correccion(tramo, tramo.km_entrada, reinicio=tramo.km_reinicio)
    foto = auditoria.foto(tramo)
    tramo.km_estado = KmEstado.REGISTRADO
    tramo.km_motivo = ''
    tramo.save()
    auditoria.registrar_modificacion(usuario, tramo, foto, accion=auditoria.Accion.CORREGIR,
                                     motivo=f"Lectura de km restituida: {motivo}")
    propagar_a_copias(tramo, usuario, f"lectura restituida: {motivo}")
    return tramo


# --- KM DE CADA JORNADA (reporte, dashboard y TV) ---

@dataclass
class KmJornada:
    inicial: int = None
    final: int = None
    recorrido: int = None        # solo si es válido
    valido: bool = False
    motivo: str = ''             # por qué no suma (vacío si es válido)
    fuera: int = None            # km desde la lectura anterior de la placa hasta el inicio del turno
    por_revisar: bool = False    # dato que el Administrador puede corregir (no «en curso»)
    lecturas: list = field(default_factory=list)  # (tramo_id, clase, km, estado) en orden, para revisarlas
    minutos_en_ruta: int = None   # fuera del CD entre la salida a ruta y la Post-Ruta (sin visitas ni imprevistos)
    imprevistos: int = 0          # retornos imprevistos del turno
    minutos_imprevistos: int = 0  # minutos en el CD de esos retornos


EN_CURSO = "Turno en curso"


def _motivo_lectura(tramo, cual):
    if tramo.km_estado == KmEstado.NO_LEIDO:
        return f"Lectura {cual}: odómetro no leído"
    if tramo.km_estado == KmEstado.EXCLUIDO:
        return f"Lectura {cual} excluida"
    if tramo.km_estado == KmEstado.OBSERVADO:
        return f"Lectura {cual} observada (por validar)"
    if tramo.km_entrada is None:
        return f"Sin lectura {cual}"
    if es_relleno(tramo.km_entrada):
        return f"Lectura {cual} de relleno ({miles(tramo.km_entrada)})"
    return ''


def _km_jornada(jornada, tramos, anterior, ahora):
    resultado = KmJornada()
    if not tramos:
        resultado.motivo = "Sin registros"
        resultado.por_revisar = jornada.estado != Jornada.Estado.PLANIFICADA
        return resultado
    resultado.lecturas = [(t.pk, t.clase, t.km_entrada, t.km_estado) for t in tramos]
    imprevistos = [t for t in tramos if t.clase == Clase.IMPREVISTO]
    resultado.imprevistos = len(imprevistos)
    resultado.minutos_imprevistos = sum(t.minutos_permanencia or 0 for t in imprevistos)
    primero = tramos[0]
    post = [t for t in tramos if t.clase == Clase.POST_RUTA]
    if post:
        resultado.minutos_en_ruta = _minutos_fuera(tramos, post[0])
    resultado.inicial = primero.km_entrada if primero.km_valido else None
    if resultado.inicial is not None and anterior is not None and 0 <= resultado.inicial - anterior <= TOPE_TURNO:
        resultado.fuera = resultado.inicial - anterior
    if jornada.cierre_sin_retorno:
        resultado.motivo, resultado.por_revisar = "Cerrada sin retorno registrado", False
        return resultado
    if not post:
        terminado = jornada.estado != Jornada.Estado.PLANIFICADA and (
            ahora.date() > (jornada.fecha + timedelta(days=1 if jornada.es_noche else 0)))
        resultado.motivo = "Sin Post-Ruta" if terminado else EN_CURSO
        resultado.por_revisar = terminado
        return resultado
    final = post[0]
    resultado.final = final.km_entrada if final.km_valido else None
    resultado.por_revisar = True
    motivo = _motivo_lectura(primero, 'inicial') or _motivo_lectura(final, 'final')
    if motivo:
        resultado.motivo = motivo
        return resultado
    validas = [t for t in tramos if t.km_valido and _orden(t) <= _orden(final)]
    if any(t.km_reinicio for t in validas[1:]):
        resultado.motivo = "Cambio de odómetro en el turno"
        return resultado
    if any(b.km_entrada < a.km_entrada for a, b in zip(validas, validas[1:])):
        resultado.motivo = "Km menor al anterior dentro del turno"
        return resultado
    recorrido = final.km_entrada - primero.km_entrada
    if recorrido > TOPE_TURNO:
        resultado.motivo = f"Más de {miles(TOPE_TURNO)} km en el turno ({miles(recorrido)})"
        return resultado
    if recorrido == 0 and jornada.primera_salida:
        resultado.motivo = "Sin variación (0 km) habiendo salido a ruta"
        return resultado
    resultado.recorrido, resultado.valido, resultado.por_revisar = recorrido, True, False
    return resultado


def _minutos_fuera(tramos, post):
    """Minutos fuera del CD del turno: los huecos entre la salida de un tramo y la entrada del siguiente, hasta la
    Post-Ruta (la Pre-Ruta, las visitas y los imprevistos son tiempo en el CD)."""
    ordenados = sorted((t for t in tramos if t.entrada_en <= post.entrada_en), key=lambda t: (t.entrada_en, t.pk))
    total = 0
    for actual, siguiente in zip(ordenados, ordenados[1:]):
        if actual.salida_en and siguiente.entrada_en > actual.salida_en:
            total += (siguiente.entrada_en - actual.salida_en).total_seconds()
    return round(total / 60)


def km_de_jornadas(lista, ahora):
    """{jornada_id: KmJornada} con dos consultas: los tramos válidos de las jornadas y las lecturas válidas de
    sus placas en el periodo (para el km fuera de jornada: desde la lectura anterior hasta el inicio del turno)."""
    if not lista:
        return {}
    ids = [j.pk for j in lista]
    por_jornada = {pk: [] for pk in ids}
    campos = ('id', 'jornada_id', 'transporte_id', 'clase', 'entrada_en', 'salida_en', 'km_entrada', 'km_estado',
              'km_reinicio', 'vigilante_entrada_id', 'minutos_permanencia')
    for tramo in Tramo.objects.filter(jornada_id__in=ids, vigente=True).only(*campos):
        por_jornada[tramo.jornada_id].append(tramo)
    desde = tiempo.combinar(min(j.fecha for j in lista) - timedelta(days=15), time.min)
    hasta = tiempo.combinar(max(j.fecha for j in lista) + timedelta(days=2), time.min)
    lecturas = {}
    for tramo in (Tramo.objects.filter(transporte_id__in={j.transporte_id for j in lista}, vigente=True,
                                       km_entrada__isnull=False, km_estado=KmEstado.REGISTRADO,
                                       entrada_en__gte=desde, entrada_en__lt=hasta).only(*campos)):
        lecturas.setdefault(tramo.transporte_id, []).append(tramo)
    for serie in lecturas.values():
        serie.sort(key=_orden)
    resultado = {}
    for jornada in lista:
        tramos = sorted(por_jornada[jornada.pk], key=_orden)
        anterior = None
        if tramos:
            clave, propios = _orden(tramos[0]), {t.pk for t in tramos}
            previas = [t for t in lecturas.get(jornada.transporte_id, []) if _orden(t) < clave and t.pk not in propios]
            anterior = previas[-1].km_entrada if previas else None
        resultado[jornada.pk] = _km_jornada(jornada, tramos, anterior, ahora)
    return resultado
