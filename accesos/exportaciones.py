"""
Contenido de las exportaciones a Excel de las listas del módulo (programaciones, transportes, conductores,
disponibilidad de flota y auditoría). Cada función recibe exactamente las filas que ya filtró la pantalla
(misma función de filtrado y mismos permisos) y devuelve (encabezados, filas, formatos[, notas]) para
`excel.respuesta_excel`. Fechas y horas en hora de Lima, sin zona (Excel no las admite con zona).
"""
import json

from django.db.models import Count

from . import licencias, servicios, tiempo
from .excel import si_no
from .models import Programacion

FECHA, HORA, ENTERO, DECIMAL, TEXTO = 'DD/MM/YYYY', 'HH:MM', '#,##0', '#,##0.0', '@'

ETIQUETAS_PUNTUALIDAD = {servicios.ADELANTADO: 'Adelantado', servicios.PUNTUAL: 'Puntual',
                         servicios.RETRASADO: 'Retrasado'}
ETIQUETAS_LICENCIA = {licencias.VIGENTE: 'Vigente', licencias.POR_VENCER: 'Por vencer',
                      licencias.VENCIDA: 'Vencida'}


def _lima(momento):
    """(fecha, hora) de Lima sin zona, o (None, None)."""
    if momento is None:
        return None, None
    local = tiempo.a_lima(momento).replace(tzinfo=None)
    return local.date(), local.time().replace(second=0, microsecond=0)


def _persona(usuario):
    if usuario is None:
        return ''
    return usuario.get_full_name() or usuario.username


# --- PROGRAMACIONES ---

def _turno(programacion):
    """Turno de una ruta INTERNO: «Día» o «Noche (del dd/mm)» (las de madrugada son del turno noche anterior)."""
    if not programacion.jornada_id:
        return ''
    jornada = programacion.jornada
    return f"Noche (del {jornada.fecha:%d/%m})" if jornada.es_noche else 'Día'


def programaciones(filas, ahora):
    """`filas`: las de `consultas.programaciones_filtradas` (con `estado_codigo`, `tramo` y `puntualidad`)."""
    encabezados = ['Código', 'Fecha', 'Hora programada', 'La hora es de', 'Turno', 'CD', 'Tipo', 'Placa', 'Tamaño',
                   'Conductor programado', 'Documento conductor', 'RUC / DNI', 'Razón social', 'Motivo',
                   'Observación de Despacho', 'Estado', 'Detalle del estado', 'Conductor que ingresó',
                   'Fecha entrada', 'Hora entrada', 'Fecha salida', 'Hora salida', 'Permanencia (min)',
                   'Desviación (min)', 'Puntualidad', 'Kilometraje', 'Clase del tramo', 'Creado por', 'Creado el']
    datos = []
    for p in filas:
        tramo = p.tramo
        if p.esta_cancelada:
            detalle = p.motivo_cancelacion + (f" ({p.incidencia.codigo})" if p.incidencia_id else '')
        elif p.no_atendida_por_cierre and p.estado == Programacion.Estado.PROGRAMADA:
            detalle = 'No atendida por cierre de jornada'
        else:
            detalle = ''
        entrada = _lima(tramo.entrada_en) if tramo else (None, None)
        salida = _lima(tramo.salida_en) if tramo else (None, None)
        datos.append([
            p.codigo, p.fecha, p.hora, 'Salida' if p.tipo == Programacion.Tipo.INTERNO else 'Llegada', _turno(p), str(p.cd),
            p.get_tipo_display(), p.transporte.placa, str(p.transporte.tamano), p.conductor.nombre_completo,
            p.conductor.numero_documento, p.documento, p.razon_social, p.motivo, p.observacion_despacho,
            servicios.ETIQUETAS_ESTADO.get(p.estado_codigo, p.estado_codigo), detalle,
            tramo.conductor.nombre_completo if tramo else '', *entrada, *salida,
            servicios.minutos_dentro(tramo, ahora) if tramo else None,
            tramo.desviacion_min if tramo else None,
            ETIQUETAS_PUNTUALIDAD.get(getattr(p, 'puntualidad', None), '') if tramo else '',
            tramo.km_entrada if tramo else None,
            tramo.get_clase_display() if tramo else '',
            _persona(p.creado_por), _lima(p.creado_en)[0],
        ])
    formatos = {'Fecha': FECHA, 'Hora programada': HORA, 'Documento conductor': TEXTO, 'RUC / DNI': TEXTO,
                'Fecha entrada': FECHA, 'Hora entrada': HORA, 'Fecha salida': FECHA, 'Hora salida': HORA,
                'Permanencia (min)': ENTERO, 'Kilometraje': ENTERO, 'Creado el': FECHA}
    notas = ["En las rutas INTERNO la hora programada es la de SALIDA del CD y la desviación se mide en la salida; "
             "en clientes y proveedores es la hora de LLEGADA y se mide en la entrada.",
             "Desviación: minutos frente a la hora programada (negativo = antes, positivo = tarde). Desde la "
             "tolerancia configurada cuenta como retraso.",
             "Permanencia: la registrada si ya salió, o la transcurrida hasta el momento de la descarga si sigue "
             "dentro. Clase del tramo «Pre-Ruta»: la primera ruta del día la atiende la salida de la Pre-Ruta.",
             "Turno (rutas INTERNO): salidas desde las 20:00 hasta las 06:15 del día siguiente son del turno noche; "
             "el turno noche se identifica por el día en que empieza.",
             f"Descargado el {tiempo.a_lima(ahora):%d/%m/%Y %H:%M} (hora de Lima)."]
    return encabezados, datos, formatos, notas


# --- TRANSPORTES ---

def transportes(filas, no_disponibles):
    encabezados = ['Placa', 'Tamaño', 'Interno', 'Activo', 'Disponibilidad', 'Último km', 'Fecha último km',
                   'Hora último km', 'Origen del último km', 'Creado por', 'Creado el', 'Modificado por',
                   'Modificado el']
    datos = []
    for t in filas:
        datos.append([
            t.placa, str(t.tamano), si_no(t.es_interno), si_no(t.activo),
            'No disponible' if t.pk in no_disponibles else 'Disponible',
            t.km_actual, *_lima(t.km_actualizado_en), t.km_origen,
            _persona(t.creado_por), _lima(t.creado_en)[0], _persona(t.modificado_por),
            _lima(t.modificado_en)[0] if t.modificado_por_id else None,
        ])
    formatos = {'Último km': ENTERO, 'Fecha último km': FECHA, 'Hora último km': HORA, 'Creado el': FECHA,
                'Modificado el': FECHA}
    return encabezados, datos, formatos


# --- CONDUCTORES ---

# Los encabezados de documento y licencia coinciden con los de la carga «Actualizar licencias» (que reconoce
# las columnas por nombre, sin importar mayúsculas, tildes ni espacios) y la hoja se llama «Datos»: el archivo
# descargado se puede corregir y volver a subir. Las demás columnas las ignora la carga.
HOJA_CONDUCTORES = 'Datos'


def conductores(filas, hoy, config):
    encabezados = ['Tipo documento', 'Número documento', 'Apellido paterno', 'Apellido materno', 'Nombres',
                   'Activo', 'Licencia número', 'Licencia categoría', 'Licencia vencimiento',
                   'Licencia especial categoría', 'Licencia especial vencimiento', 'Notificar',
                   'Estado de la licencia', 'Estado de la licencia especial', 'Creado por', 'Creado el']
    datos = []
    for c in filas:
        estado = licencias.estado(c.licencia_vencimiento, hoy, config)
        estado_especial = licencias.estado(c.licencia_especial_vencimiento, hoy, config)
        datos.append([
            c.tipo_documento.codigo, c.numero_documento, c.apellido_paterno, c.apellido_materno, c.nombres,
            si_no(c.activo), c.licencia_numero, c.licencia_categoria, c.licencia_vencimiento,
            c.licencia_especial_categoria, c.licencia_especial_vencimiento, si_no(c.notificar_vencimiento),
            ETIQUETAS_LICENCIA.get(estado, 'Sin licencia registrada'),
            ETIQUETAS_LICENCIA.get(estado_especial, '') if c.licencia_especial_categoria else '',
            _persona(c.creado_por), _lima(c.creado_en)[0],
        ])
    formatos = {'Número documento': TEXTO, 'Licencia número': TEXTO, 'Licencia vencimiento': FECHA,
                'Licencia especial vencimiento': FECHA, 'Creado el': FECHA}
    notas = ["Para actualizar licencias: corrija en esta hoja las columnas de licencia (número, categoría, "
             "vencimientos y Notificar SI/NO) y suba el archivo en Conductores → «Actualizar licencias».",
             "No cambie los encabezados ni el nombre de la hoja «Datos». Las columnas de nombres, Activo, estados "
             "y auditoría se ignoran al subirlo: los datos personales no se modifican por archivo.",
             "Las filas sin cambios se omiten. Quite las filas de conductores sin licencia registrada (darían "
             "error por faltar número, categoría y vencimiento) o descargue con el filtro de licencias.",
             f"Estados calculados al {hoy:%d/%m/%Y} con un aviso de {config.aviso_licencia_dias} días."]
    return encabezados, datos, formatos, notas


# --- DISPONIBILIDAD DE FLOTA (INCIDENCIAS) ---

def incidencias(queryset, afectadas_por_transporte, ahora):
    encabezados = ['Código', 'Placa', 'Tamaño', 'Tipo', 'Descripción', 'Origen', 'CD', 'Fecha inicio',
                   'Hora inicio', 'Fecha habilitado', 'Hora habilitado', 'Fuera de servicio (h)', 'Estado',
                   'Registrada por', 'Habilitada por', 'Observación al habilitar', 'Rutas canceladas',
                   'Programaciones por reasignar']
    datos = []
    for inc in queryset.annotate(rutas_canceladas=Count('programaciones_canceladas')):
        fin = inc.fin or ahora
        datos.append([
            inc.codigo, inc.transporte.placa, str(inc.transporte.tamano), str(inc.tipo), inc.descripcion,
            inc.get_origen_display(), str(inc.cd or ''), *_lima(inc.inicio), *_lima(inc.fin),
            round((fin - inc.inicio).total_seconds() / 3600, 1),
            'No disponible' if inc.esta_abierta else 'Habilitado', _persona(inc.registrada_por),
            _persona(inc.cerrada_por), inc.observacion_cierre, inc.rutas_canceladas,
            afectadas_por_transporte.get(inc.transporte_id, 0) if inc.esta_abierta else None,
        ])
    formatos = {'Fecha inicio': FECHA, 'Hora inicio': HORA, 'Fecha habilitado': FECHA, 'Hora habilitado': HORA,
                'Fuera de servicio (h)': DECIMAL}
    notas = ["Fuera de servicio: horas desde el inicio de la avería hasta que se habilitó o, si sigue no "
             f"disponible, hasta la descarga ({tiempo.a_lima(ahora):%d/%m/%Y %H:%M}, hora de Lima).",
             "Programaciones por reasignar: pendientes desde hoy de un vehículo que sigue no disponible "
             "(Despacho les cambia la placa o las cancela)."]
    return encabezados, datos, formatos, notas


# --- AUDITORÍA ---

def _json(valor):
    if valor in (None, {}, []):
        return ''
    return json.dumps(valor, ensure_ascii=False, default=str)


def auditoria(registros):
    encabezados = ['Fecha', 'Hora', 'Usuario', 'Acción', 'Registro', 'Id', 'Descripción', 'CD', 'Motivo',
                   'Campos cambiados', 'Antes', 'Después']
    datos = []
    for r in registros:
        antes, despues = r.datos_antes or {}, r.datos_despues or {}
        cambiados = sorted(k for k in set(antes) | set(despues) if antes.get(k) != despues.get(k)) \
            if isinstance(antes, dict) and isinstance(despues, dict) else []
        datos.append([*_lima(r.fecha), _persona(r.usuario) or 'Sistema', r.get_accion_display(), r.entidad, r.objeto_id,
                      r.objeto_repr, str(r.cd or ''), r.motivo, ', '.join(cambiados), _json(r.datos_antes),
                      _json(r.datos_despues)])
    return encabezados, datos, {'Fecha': FECHA, 'Hora': HORA}
