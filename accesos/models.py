"""
Modelos del módulo de Control de Accesos de Transporte: catálogos, maestros, configuración,
programaciones (F2), tramos (F3), jornada de la flota propia e incidencias (F6) y auditoría.

Ningún modelo de esta app tiene relación con `planificacion`; la única relación externa
es hacia el usuario de Django (auth_user).
"""
import re
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q

PATRON_NO_ALFANUMERICO = re.compile(r'[^A-Z0-9]')
LONGITUD_MIN_PLACA = 5
LONGITUD_MAX_PLACA = 8


def normalizar_placa(texto):
    """Mayúsculas y solo letras y números: 'f7j-011', 'F7J 011' y 'F7J011' dan 'F7J011'.

    Es la clave de búsqueda del transporte y el contenido de su QR.
    """
    return PATRON_NO_ALFANUMERICO.sub('', (texto or '').upper())


# --- CATÁLOGOS ---

class CentroDistribucion(models.Model):
    codigo = models.CharField(max_length=20, unique=True, verbose_name="Código")
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    capacidad_simultanea = models.PositiveSmallIntegerField(
        verbose_name="Capacidad simultánea",
        help_text="Vehículos que el CD puede atender a la vez (referencial, solo genera avisos).",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Centro de Distribución"
        verbose_name_plural = "Centros de Distribución"
        ordering = ['nombre']
        constraints = [
            models.CheckConstraint(condition=Q(capacidad_simultanea__gt=0), name='accesos_cd_capacidad_positiva'),
        ]

    def __str__(self):
        return self.nombre


class TamanoVehiculo(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    orden = models.PositiveSmallIntegerField(default=0, help_text="Orden en listas y gráficos.")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Tamaño de Vehículo"
        verbose_name_plural = "Tamaños de Vehículo"
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


class TipoDocumentoIdentidad(models.Model):
    codigo = models.CharField(max_length=10, unique=True, verbose_name="Código")
    nombre = models.CharField(max_length=60)
    longitud_min = models.PositiveSmallIntegerField(verbose_name="Longitud mínima")
    longitud_max = models.PositiveSmallIntegerField(verbose_name="Longitud máxima")
    solo_digitos = models.BooleanField(default=True, verbose_name="Solo dígitos")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Tipo de Documento de Identidad"
        verbose_name_plural = "Tipos de Documento de Identidad"
        ordering = ['codigo']
        constraints = [
            models.CheckConstraint(
                condition=Q(longitud_min__gt=0) & Q(longitud_max__gte=models.F('longitud_min')),
                name='accesos_tipodoc_longitudes_validas',
            ),
        ]

    def __str__(self):
        return self.codigo

    def validar_numero(self, numero):
        """Lanza ValidationError si el número no cumple la longitud o el formato del tipo."""
        if not (self.longitud_min <= len(numero) <= self.longitud_max):
            if self.longitud_min == self.longitud_max:
                rango = f"{self.longitud_min} caracteres"
            else:
                rango = f"entre {self.longitud_min} y {self.longitud_max} caracteres"
            raise ValidationError(f"El {self.codigo} debe tener {rango}.")
        if self.solo_digitos and not numero.isdigit():
            raise ValidationError(f"El {self.codigo} solo admite dígitos.")
        if not self.solo_digitos and not numero.isalnum():
            raise ValidationError(f"El {self.codigo} solo admite letras y números.")


class ConfiguracionAccesos(models.Model):
    """Parámetros operativos del módulo. Registro único: usar ConfiguracionAccesos.obtener()."""
    ventana_sugerencia_min = models.PositiveSmallIntegerField(
        default=60, verbose_name="Ventana de sugerencia (min)",
        help_text="Programación sugerida en portería: la más cercana dentro de ± estos minutos.",
    )
    tolerancia_puntualidad_min = models.PositiveSmallIntegerField(
        default=15, verbose_name="Tolerancia de puntualidad (min)",
        help_text="Desde estos minutos de demora la llegada (o la salida, en las rutas INTERNO) es «Retrasado».",
    )
    duracion_estimada_atencion_min = models.PositiveSmallIntegerField(
        default=60, verbose_name="Duración estimada de atención (min)",
        help_text="Se usa para el aviso de capacidad al programar.",
    )
    umbral_prolongada_min = models.PositiveSmallIntegerField(
        default=240, verbose_name="Umbral de permanencia prolongada (min)",
    )
    meta_permanencia_min = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Meta de permanencia (min)",
        help_text="La define Gerencia. Un tramo por encima de esta meta cuenta como demora.",
    )
    sesion_vigilancia_horas = models.PositiveSmallIntegerField(
        default=12, verbose_name="Duración de sesión de Vigilancia (horas)",
    )
    horarios_sugeridos = models.CharField(
        max_length=200, default='08:00,09:00,10:00,11:00,12:00,14:00,15:00,16:00',
        help_text="Horas separadas por comas. Solo son sugerencias al programar.",
    )
    ruc_empresa = models.CharField(
        max_length=11, blank=True, verbose_name="RUC de la empresa",
        help_text="Valor por defecto del documento en programaciones de tipo INTERNO.",
    )
    razon_social_empresa = models.CharField(
        max_length=200, default='Daryza S.A.C.', verbose_name="Razón social de la empresa",
    )
    aviso_licencia_dias = models.PositiveSmallIntegerField(
        default=30, verbose_name="Aviso de vencimiento de licencias (días)",
        help_text="La campanita avisa las licencias que vencen dentro de estos días.",
    )
    gracia_licencia_dias = models.PositiveSmallIntegerField(
        default=7, verbose_name="Aviso después del vencimiento (días)",
        help_text="Días que se sigue avisando una licencia vencida. Pasado este plazo, si todas las "
                  "licencias del conductor vencieron, sus notificaciones se desactivan solas.",
    )
    licencias_revisadas_el = models.DateField(
        null=True, blank=True, editable=False, verbose_name="Última revisión automática de licencias",
    )
    # Jornada de la flota propia (Pre-Ruta y Post-Ruta). Cada jornada guarda una copia de estos valores
    # al crearse: un cambio aquí rige para las jornadas nuevas y no altera el histórico.
    hora_pre_ruta = models.TimeField(
        default=time(6, 30), verbose_name="Hora de inicio de la Pre-Ruta",
        help_text="Inicio automático de la jornada de cada unidad propia con programación INTERNO.",
    )
    meta_pre_ruta_min = models.PositiveSmallIntegerField(
        default=60, verbose_name="Meta de la Pre-Ruta (min)",
        help_text="Minutos para salir a ruta desde el inicio de la Pre-Ruta; después, alerta.",
    )
    hora_cierre_operativo = models.TimeField(
        default=time(16, 0), verbose_name="Hora de cierre operativo",
        help_text="Hora de retorno esperada de la flota propia (Post-Ruta).",
    )
    tolerancia_cierre_min = models.PositiveSmallIntegerField(
        default=15, verbose_name="Tolerancia del cierre operativo (min)",
        help_text="Un retorno después de la hora de cierre más esta tolerancia es «fuera de horario».",
    )
    # Turno noche (15/10/2026): rutas INTERNO con salida desde la hora de la Pre-Ruta noche (20:00) hasta la
    # hora de cierre noche (06:15) del día siguiente. Lunes a viernes; el sábado es excepcional (aviso).
    hora_pre_ruta_noche = models.TimeField(
        default=time(20, 0), verbose_name="Hora de inicio del turno noche (Pre-Ruta noche)",
        help_text="Desde esta hora una ruta INTERNO es del turno noche y empieza su Pre-Ruta.",
    )
    meta_pre_ruta_noche_min = models.PositiveSmallIntegerField(
        default=60, verbose_name="Meta de la Pre-Ruta noche (min)",
        help_text="Minutos para salir a ruta desde el inicio del turno noche; después, alerta.",
    )
    hora_cierre_noche = models.TimeField(
        default=time(6, 15), verbose_name="Hora de cierre del turno noche",
        help_text="Retorno esperado del turno noche (día siguiente). Las salidas hasta esta hora son del turno "
                  "noche del día anterior.",
    )
    tolerancia_cierre_noche_min = models.PositiveSmallIntegerField(
        default=15, verbose_name="Tolerancia del cierre del turno noche (min)",
        help_text="Un retorno del turno noche después del cierre más esta tolerancia es «fuera de horario».",
    )
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Accesos"
        verbose_name_plural = "Configuración de Accesos"
        constraints = [
            models.CheckConstraint(condition=Q(ventana_sugerencia_min__gt=0), name='accesos_config_ventana_positiva'),
            models.CheckConstraint(condition=Q(umbral_prolongada_min__gt=0), name='accesos_config_umbral_positivo'),
            models.CheckConstraint(condition=Q(sesion_vigilancia_horas__gt=0), name='accesos_config_sesion_positiva'),
            models.CheckConstraint(condition=Q(aviso_licencia_dias__gt=0), name='accesos_config_aviso_licencia_positivo'),
            models.CheckConstraint(condition=Q(meta_pre_ruta_min__gt=0), name='accesos_config_meta_pre_ruta_positiva'),
            models.CheckConstraint(condition=Q(meta_pre_ruta_noche_min__gt=0),
                                   name='accesos_config_meta_pre_ruta_noche_positiva'),
        ]

    def __str__(self):
        return "Configuración de Accesos"

    @classmethod
    def obtener(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config

    def clean(self):
        super().clean()
        if self.ruc_empresa and not (self.ruc_empresa.isdigit() and len(self.ruc_empresa) == 11):
            raise ValidationError({'ruc_empresa': "El RUC debe tener 11 dígitos."})
        # Las franjas de los turnos no se pisan: el cierre noche (madrugada) antes de la Pre-Ruta del día, y el
        # turno noche después del cierre operativo del día.
        errores = {}
        if self.hora_cierre_noche and self.hora_pre_ruta and self.hora_cierre_noche >= self.hora_pre_ruta:
            errores['hora_cierre_noche'] = "Debe ser anterior a la hora de inicio de la Pre-Ruta del día."
        if self.hora_pre_ruta_noche and self.hora_cierre_operativo and self.hora_pre_ruta_noche <= self.hora_cierre_operativo:
            errores['hora_pre_ruta_noche'] = "Debe ser posterior a la hora de cierre operativo del día."
        if errores:
            raise ValidationError(errores)


class Feriado(models.Model):
    """Día no laborable (además de los domingos): no se programa, no hay jornada y el dashboard
    lo muestra como «Sin actividad»."""
    fecha = models.DateField(unique=True)
    descripcion = models.CharField(max_length=100, verbose_name="Descripción")

    class Meta:
        verbose_name = "Feriado"
        verbose_name_plural = "Feriados"
        ordering = ['fecha']

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y} · {self.descripcion}"


class TipoIncidencia(models.Model):
    """Tipo de no disponibilidad de un vehículo (avería mecánica, siniestro, etc.)."""
    nombre = models.CharField(max_length=60, unique=True)
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Tipo de incidencia"
        verbose_name_plural = "Tipos de incidencia"
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


# --- MAESTROS ---

class Transporte(models.Model):
    """Vehículo físico. La placa normalizada es su identificador de negocio y el contenido del QR."""
    placa = models.CharField(max_length=10, verbose_name="Placa", help_text="Ej. F7J-011")
    placa_normalizada = models.CharField(max_length=10, unique=True, editable=False)
    tamano = models.ForeignKey(TamanoVehiculo, on_delete=models.PROTECT, verbose_name="Tamaño")
    es_interno = models.BooleanField(default=False, verbose_name="Transporte interno",
                                     help_text="Solo a los internos se les imprime el QR.")
    activo = models.BooleanField(default=True)
    # Último kilometraje registrado en portería. Lo mantiene kilometraje.actualizar() a partir de los tramos
    # válidos (que son la fuente de verdad), así que también sigue a las correcciones y anulaciones.
    km_actual = models.PositiveIntegerField(null=True, blank=True, editable=False, verbose_name="Último kilometraje")
    km_actualizado_en = models.DateTimeField(null=True, blank=True, editable=False,
                                             verbose_name="Fecha del último kilometraje")
    km_origen = models.CharField(max_length=60, blank=True, editable=False, verbose_name="Origen del último kilometraje")

    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+', verbose_name="Creado por")
    creado_en = models.DateTimeField(auto_now_add=True)
    modificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                       related_name='+', null=True, blank=True, verbose_name="Modificado por")
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Transporte"
        verbose_name_plural = "Transportes"
        ordering = ['placa_normalizada']

    def __str__(self):
        return self.placa

    def clean(self):
        super().clean()
        normalizada = normalizar_placa(self.placa)
        self.placa_normalizada = normalizada
        if not (LONGITUD_MIN_PLACA <= len(normalizada) <= LONGITUD_MAX_PLACA):
            raise ValidationError({'placa': f"La placa debe tener entre {LONGITUD_MIN_PLACA} y "
                                            f"{LONGITUD_MAX_PLACA} letras o números."})
        duplicada = Transporte.objects.filter(placa_normalizada=normalizada).exclude(pk=self.pk)
        if duplicada.exists():
            raise ValidationError({'placa': f"Ya existe un transporte con la placa {duplicada.first().placa}."})

    def save(self, *args, **kwargs):
        self.placa = (self.placa or '').strip().upper()
        self.placa_normalizada = normalizar_placa(self.placa)
        super().save(*args, **kwargs)

    @property
    def contenido_qr(self):
        return self.placa_normalizada


CATEGORIAS_LICENCIA = [(c, c) for c in ('A-IIb', 'A-IIIa', 'A-IIIb', 'A-IIIc')]
CATEGORIAS_LICENCIA_ESPECIAL = [('A-IV', 'A-IV')]  # materiales y residuos peligrosos (MAPTEL)
PATRON_LICENCIA = re.compile(r'^[A-Z0-9]{6,12}$')


def normalizar_licencia(texto):
    """Número de licencia en mayúsculas, sin espacios ni guiones: 'q-43028991' -> 'Q43028991'."""
    return PATRON_NO_ALFANUMERICO.sub('', (texto or '').upper())


class Conductor(models.Model):
    tipo_documento = models.ForeignKey(TipoDocumentoIdentidad, on_delete=models.PROTECT,
                                       verbose_name="Tipo de documento")
    numero_documento = models.CharField(max_length=15, verbose_name="Número de documento")
    apellido_paterno = models.CharField(max_length=60)
    apellido_materno = models.CharField(max_length=60, blank=True)
    nombres = models.CharField(max_length=60)
    activo = models.BooleanField(default=True)

    # Licencia de conducir (una sola licencia: la categoría especial A-IV va en el mismo número,
    # con su propio vencimiento). Todo opcional: los conductores ya registrados no la tienen.
    licencia_numero = models.CharField(max_length=12, blank=True, verbose_name="Número de licencia",
                                       help_text="Ej. Q43028991")
    licencia_categoria = models.CharField(max_length=7, blank=True, choices=CATEGORIAS_LICENCIA,
                                          verbose_name="Categoría")
    licencia_vencimiento = models.DateField(null=True, blank=True, db_index=True,
                                            verbose_name="Vencimiento")
    licencia_especial_categoria = models.CharField(max_length=5, blank=True, choices=CATEGORIAS_LICENCIA_ESPECIAL,
                                                   verbose_name="Licencia especial",
                                                   help_text="A-IV: materiales y residuos peligrosos (MAPTEL).")
    licencia_especial_vencimiento = models.DateField(null=True, blank=True, db_index=True,
                                                     verbose_name="Vencimiento licencia especial")
    notificar_vencimiento = models.BooleanField(
        default=True, verbose_name="Notificar vencimientos",
        help_text="Avisa en la campanita cuando una licencia está por vencer. Se desactiva sola cuando "
                  "todas sus licencias llevan vencidas más del plazo configurado.",
    )

    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+', verbose_name="Creado por")
    creado_en = models.DateTimeField(auto_now_add=True)
    modificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                       related_name='+', null=True, blank=True, verbose_name="Modificado por")
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conductor"
        verbose_name_plural = "Conductores"
        ordering = ['apellido_paterno', 'apellido_materno', 'nombres']
        constraints = [
            models.UniqueConstraint(fields=['tipo_documento', 'numero_documento'],
                                    name='accesos_conductor_documento_unico',
                                    violation_error_message="Ya existe un conductor con ese documento."),
        ]

    def __str__(self):
        return f"{self.nombre_completo} ({self.tipo_documento} {self.numero_documento})"

    @property
    def nombre_completo(self):
        apellidos = ' '.join(filter(None, [self.apellido_paterno, self.apellido_materno]))
        return f"{apellidos}, {self.nombres}"

    def clean(self):
        super().clean()
        self.numero_documento = (self.numero_documento or '').strip().upper()
        if self.tipo_documento_id:
            try:
                self.tipo_documento.validar_numero(self.numero_documento)
            except ValidationError as error:
                raise ValidationError({'numero_documento': error.messages})
        self._validar_licencias()

    def _validar_licencias(self):
        self.licencia_numero = normalizar_licencia(self.licencia_numero)
        errores = {}
        if self.licencia_numero and not PATRON_LICENCIA.match(self.licencia_numero):
            errores['licencia_numero'] = "La licencia debe tener entre 6 y 12 letras o números (ej. Q43028991)."
        licencia = [self.licencia_numero, self.licencia_categoria, self.licencia_vencimiento]
        if any(licencia) and not all(licencia):
            for campo, valor, texto in (('licencia_numero', self.licencia_numero, "el número de licencia"),
                                        ('licencia_categoria', self.licencia_categoria, "la categoría"),
                                        ('licencia_vencimiento', self.licencia_vencimiento, "el vencimiento")):
                if not valor:
                    errores.setdefault(campo, f"Complete {texto}: la licencia necesita número, categoría y vencimiento.")
        if self.licencia_especial_categoria and not self.licencia_especial_vencimiento:
            errores['licencia_especial_vencimiento'] = "Indique el vencimiento de la licencia especial."
        if self.licencia_especial_vencimiento and not self.licencia_especial_categoria:
            errores['licencia_especial_categoria'] = "Indique la categoría especial (A-IV) o borre su vencimiento."
        if self.licencia_especial_categoria and not self.licencia_numero:
            errores.setdefault('licencia_numero', "La licencia especial va en la misma licencia: registre su número.")
        if errores:
            raise ValidationError(errores)

    @property
    def tiene_licencia(self):
        return bool(self.licencia_vencimiento or self.licencia_especial_vencimiento)

    def licencias(self):
        """[(etiqueta, categoría, vencimiento)] de las licencias registradas."""
        lista = []
        if self.licencia_vencimiento:
            lista.append(("Licencia", self.licencia_categoria, self.licencia_vencimiento))
        if self.licencia_especial_vencimiento:
            lista.append(("Licencia especial", self.licencia_especial_categoria, self.licencia_especial_vencimiento))
        return lista


class UsuarioCD(models.Model):
    """CD en los que un usuario (principalmente Vigilancia) está autorizado a operar."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='cds_accesos')
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, verbose_name="CD")

    class Meta:
        verbose_name = "CD autorizado de usuario"
        verbose_name_plural = "CD autorizados de usuarios"
        constraints = [
            models.UniqueConstraint(fields=['usuario', 'cd'], name='accesos_usuariocd_unico'),
        ]

    def __str__(self):
        return f"{self.usuario} → {self.cd}"


# --- PROGRAMACIÓN ---

PESOS_RUC = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def ruc_valido(ruc):
    """RUC peruano: 11 dígitos con dígito verificador (módulo 11) correcto."""
    if not (len(ruc) == 11 and ruc.isdigit()):
        return False
    resto = 11 - sum(int(d) * p for d, p in zip(ruc[:10], PESOS_RUC)) % 11
    return int(ruc[10]) == {10: 0, 11: 1}.get(resto, resto)


class Programacion(models.Model):
    """Lo que Despacho espera que ocurra: una fecha, un horario y una placa en un CD.

    Genera un solo tramo (F3). Si la misma placa vuelve a ingresar el mismo día, se registra
    otra programación en otro horario. Las reglas que dependen de la hora actual (no
    programar en el pasado, bloqueo de edición, cancelación) viven en `servicios.py`.
    """

    class Tipo(models.TextChoices):
        CLIENTE = 'CLIENTE', 'Cliente'
        PROVEEDOR = 'PROVEEDOR', 'Proveedor'
        INTERNO = 'INTERNO', 'Interno'

    class Estado(models.TextChoices):
        PROGRAMADA = 'PROGRAMADA', 'Programada'
        EN_PLANTA = 'EN_PLANTA', 'En planta'
        CERRADA = 'CERRADA', 'Cerrada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, verbose_name="CD")
    fecha = models.DateField(verbose_name="Fecha")
    hora = models.TimeField(verbose_name="Hora", help_text="Hora de Lima. Puede ser exacta (08:17). En las rutas INTERNO es la "
                                                          "hora de SALIDA del CD; en clientes y proveedores, la de llegada.")
    tipo = models.CharField(max_length=10, choices=Tipo.choices, verbose_name="Tipo")
    transporte = models.ForeignKey(Transporte, on_delete=models.PROTECT, related_name='programaciones',
                                   verbose_name="Transporte (placa)")
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT, related_name='programaciones')
    documento = models.CharField(max_length=11, verbose_name="RUC / DNI",
                                 help_text="RUC (11 dígitos) o DNI (8 dígitos).")
    razon_social = models.CharField(max_length=200, verbose_name="Razón social")
    motivo = models.CharField(max_length=200, verbose_name="Motivo",
                              help_text="Texto libre. Ej.: Recojo de pedido, Entrega de insumos.")
    observacion_despacho = models.TextField(blank=True, verbose_name="Observación de Despacho")
    # Programada -> En planta (entrada registrada) -> Cerrada (salida registrada); o Cancelada.
    # Lo mantiene servicios.sincronizar_estado() según el tramo vigente.
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PROGRAMADA, db_index=True)

    cancelado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                      null=True, blank=True)
    cancelado_en = models.DateTimeField(null=True, blank=True)
    motivo_cancelacion = models.TextField(blank=True)
    # Cancelada por la avería (no disponibilidad) del vehículo: separa esta causa en los indicadores.
    incidencia = models.ForeignKey('Incidencia', on_delete=models.PROTECT, related_name='programaciones_canceladas',
                                   null=True, blank=True)
    # La unidad cerró su jornada (Post-Ruta) sin atenderla: se muestra «No atendida» desde ese momento.
    no_atendida_por_cierre = models.BooleanField(default=False, verbose_name="No atendida por cierre de jornada")
    # Rutas INTERNO: la jornada (turno día o noche) a la que pertenece. La asigna jornadas.sincronizar() al
    # guardar, por la franja horaria de la salida; portería, la Post-Ruta y las averías trabajan por jornada.
    jornada = models.ForeignKey('Jornada', on_delete=models.PROTECT, related_name='programaciones',
                                null=True, blank=True, editable=False)

    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    creado_en = models.DateTimeField(auto_now_add=True)
    modificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                       null=True, blank=True)
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Programación"
        verbose_name_plural = "Programaciones"
        ordering = ['fecha', 'hora', 'cd__nombre']
        indexes = [
            models.Index(fields=['cd', 'fecha', 'hora'], name='accesos_prog_cd_fecha_idx'),
            models.Index(fields=['transporte', 'fecha'], name='accesos_prog_transp_fecha_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(estado='CANCELADA') | (Q(cancelado_en__isnull=False) & ~Q(motivo_cancelacion='')),
                name='accesos_prog_cancelacion_completa',
            ),
        ]

    def __str__(self):
        return f"{self.codigo} {self.transporte} {self.fecha:%d/%m/%Y} {self.hora:%H:%M}"

    @property
    def codigo(self):
        return f"PRG-{self.pk:06d}" if self.pk else "PRG-nueva"

    @property
    def esta_cancelada(self):
        return self.estado == self.Estado.CANCELADA

    def clean(self):
        super().clean()
        self.documento = (self.documento or '').strip()
        self.razon_social = (self.razon_social or '').strip()
        errores = {}
        if self.documento and not self.documento.isdigit():
            errores['documento'] = "Solo se admiten dígitos."
        elif len(self.documento) == 11 and not ruc_valido(self.documento):
            errores['documento'] = "El RUC no es válido (revise los dígitos)."
        elif self.documento and len(self.documento) not in (8, 11):
            errores['documento'] = "Ingrese un RUC de 11 dígitos o un DNI de 8 dígitos."
        if errores:
            raise ValidationError(errores)

    # Campos que no cambian la jornada: el estado que sigue al tramo (En planta, Cerrada) no la afecta.
    CAMPOS_SIN_EFECTO_EN_JORNADA = {'estado', 'modificado_en', 'no_atendida_por_cierre', 'jornada'}

    def save(self, *args, **kwargs):
        """Al guardar, la ruta INTERNO queda ligada a su jornada (turno día o noche según la hora de salida) y se
        sincronizan esa jornada y la anterior si cambió: la primera ruta crea la Pre-Ruta; sin ninguna activa, se
        anula."""
        campos = kwargs.get('update_fields')
        if campos is not None and set(campos) <= self.CAMPOS_SIN_EFECTO_EN_JORNADA and not self.esta_cancelada:
            return super().save(*args, **kwargs)
        anterior = None
        if self.pk:
            anterior = Programacion.objects.filter(pk=self.pk).values_list('jornada_id', flat=True).first()
        super().save(*args, **kwargs)
        from . import jornadas  # import diferido: jornadas usa estos modelos
        jornadas.sincronizar(self, anterior)


# --- INCIDENCIAS (NO DISPONIBILIDAD) ---

class Incidencia(models.Model):
    """Avería o falla que deja al vehículo NO DISPONIBLE: mientras esté abierta, la placa no aparece para
    programar. `abierta` vale TRUE o NULL (como en los tramos) para que la base de datos asegure una sola
    incidencia abierta por transporte."""

    class Origen(models.TextChoices):
        PORTERIA = 'PORTERIA', 'Portería (ingreso por avería)'
        MANUAL = 'MANUAL', 'Registro de Despacho o Administrador'

    transporte = models.ForeignKey(Transporte, on_delete=models.PROTECT, related_name='incidencias')
    tipo = models.ForeignKey(TipoIncidencia, on_delete=models.PROTECT, verbose_name="Tipo")
    descripcion = models.TextField(verbose_name="Descripción")
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.MANUAL)
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, null=True, blank=True, verbose_name="CD")
    inicio = models.DateTimeField(verbose_name="Inicio")
    fin = models.DateTimeField(null=True, blank=True, verbose_name="Fin (habilitado)")
    abierta = models.BooleanField(null=True, default=True, editable=False)
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    cerrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                    null=True, blank=True)
    observacion_cierre = models.TextField(blank=True, verbose_name="Observación al habilitar")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Incidencia de no disponibilidad"
        verbose_name_plural = "Incidencias de no disponibilidad"
        ordering = ['-inicio', '-id']
        constraints = [
            models.UniqueConstraint(fields=['transporte', 'abierta'], name='accesos_incidencia_una_abierta'),
            models.CheckConstraint(condition=Q(abierta__isnull=True) | Q(abierta=True),
                                   name='accesos_incidencia_abierta_true_o_null'),
            models.CheckConstraint(condition=Q(fin__isnull=True) | Q(fin__gte=models.F('inicio')),
                                   name='accesos_incidencia_fin_posterior'),
        ]

    def __str__(self):
        return f"{self.codigo} {self.transporte} · {self.tipo}"

    @property
    def codigo(self):
        return f"INC-{self.pk:06d}" if self.pk else "INC-nueva"

    @property
    def esta_abierta(self):
        return bool(self.abierta)


# --- JORNADA DE LA FLOTA PROPIA ---

class Jornada(models.Model):
    """Un turno de una unidad propia: empieza con la Pre-Ruta (automática, a la hora configurada) y termina
    con la Post-Ruta (la entrada que registra Vigilancia cuando ya no le quedan rutas de ese turno).

    Turnos (15/10/2026): DÍA (Pre-Ruta 06:30, cierre 16:00 + 15) y NOCHE (Pre-Ruta 20:00, cierre 06:15 + 15 del
    día siguiente), cada uno con su conductor, sus rutas y su Post-Ruta. `fecha` es el día en que empieza el
    turno. Se crea sola con la primera ruta INTERNO del turno (una por placa, fecha y turno) y se anula si se
    cancelan todas. Guarda una copia de los horarios vigentes al crearse. Los tramos PRE_RUTA y POST_RUTA solo
    se graban cuando ocurren (salida y entrada reales)."""

    class Turno(models.TextChoices):
        DIA = 'DIA', 'Día'
        NOCHE = 'NOCHE', 'Noche'

    class Estado(models.TextChoices):
        PLANIFICADA = 'PLANIFICADA', 'Planificada'   # todavía no sale a ruta
        EN_RUTA = 'EN_RUTA', 'En ruta'               # registró su primera salida
        CERRADA = 'CERRADA', 'Cerrada'               # Post-Ruta registrada o cerrada por avería
        ANULADA = 'ANULADA', 'Anulada'               # sin programaciones INTERNO activas

    class MotivoCierre(models.TextChoices):
        NORMAL = 'NORMAL', 'Post-Ruta'
        AVERIA = 'AVERIA', 'Avería'

    transporte = models.ForeignKey(Transporte, on_delete=models.PROTECT, related_name='jornadas')
    fecha = models.DateField(db_index=True, help_text="Día en que empieza el turno.")
    turno = models.CharField(max_length=5, choices=Turno.choices, default=Turno.DIA, db_index=True)
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, verbose_name="CD")
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT, related_name='jornadas',
                                  help_text="Conductor previsto: el de la primera programación del turno.")
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PLANIFICADA, db_index=True)

    # Copia de la configuración al crearse.
    hora_inicio = models.TimeField(verbose_name="Inicio de la Pre-Ruta")
    meta_pre_ruta_min = models.PositiveSmallIntegerField()
    hora_cierre = models.TimeField(verbose_name="Cierre operativo")
    tolerancia_cierre_min = models.PositiveSmallIntegerField()

    programada_tarde = models.BooleanField(default=False,
                                           help_text="La primera ruta INTERNO se registró después del inicio.")
    inicio_real = models.DateTimeField(null=True, blank=True, verbose_name="Inicio real de la Pre-Ruta")
    primera_salida = models.DateTimeField(null=True, blank=True, verbose_name="Salida a ruta")
    post_ruta_en = models.DateTimeField(null=True, blank=True, verbose_name="Post-Ruta")
    motivo_cierre = models.CharField(max_length=10, choices=MotivoCierre.choices, blank=True)
    incidencia = models.ForeignKey(Incidencia, on_delete=models.PROTECT, related_name='jornadas',
                                   null=True, blank=True)
    ingreso_desde_afuera = models.BooleanField(default=False,
                                               help_text="Inició la jornada entrando al CD (no durmió en él).")
    salida_anticipada = models.BooleanField(default=False, help_text="Salió antes de la hora de la Pre-Ruta.")
    salida_sin_pre_ruta = models.BooleanField(
        default=False, help_text="Su primera salida cerró un tramo programado que quedó abierto desde otro día.")
    pernocto_fuera = models.BooleanField(default=False, help_text="Salió del CD después de su Post-Ruta.")

    creado_en = models.DateTimeField(auto_now_add=True)
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Jornada de flota propia"
        verbose_name_plural = "Jornadas de flota propia"
        ordering = ['-fecha', 'transporte__placa_normalizada', 'turno']
        constraints = [
            models.UniqueConstraint(fields=['transporte', 'fecha', 'turno'], name='accesos_jornada_una_por_turno'),
        ]

    def __str__(self):
        return f"Jornada {self.transporte} {self.fecha:%d/%m/%Y}{' (noche)' if self.es_noche else ''}"

    @property
    def codigo(self):
        return f"JOR-{self.pk:06d}" if self.pk else "JOR-nueva"

    @property
    def es_noche(self):
        return self.turno == self.Turno.NOCHE


# --- TRAMO ---

class Tramo(models.Model):
    """Lo que ocurrió en portería: una entrada y su salida, asociadas a una programación.

    Dos restricciones en la base de datos protegen la operación aunque dos tablets registren
    a la vez (MySQL no admite índices únicos parciales, por eso se usan campos TRUE/NULL):
    - `abierto` = TRUE mientras el vehículo está dentro; único por transporte.
    - `vigente` = TRUE si el tramo es válido (NULL si se anuló); único por programación.

    Clases: PROGRAMADO (una visita programada; lo de siempre) y los de la jornada de la flota propia,
    que no tienen programación: PRE_RUTA (desde la hora de la Pre-Ruta hasta la primera salida) y
    POST_RUTA (desde el retorno de fin de jornada; queda abierto mientras la unidad duerme en el CD).
    """

    class Clase(models.TextChoices):
        PROGRAMADO = 'PROGRAMADO', 'Programado'
        PRE_RUTA = 'PRE_RUTA', 'Pre-Ruta'
        POST_RUTA = 'POST_RUTA', 'Post-Ruta'

    clase = models.CharField(max_length=10, choices=Clase.choices, default=Clase.PROGRAMADO, db_index=True)
    programacion = models.ForeignKey(Programacion, on_delete=models.PROTECT, related_name='tramos',
                                     null=True, blank=True)
    jornada = models.ForeignKey(Jornada, on_delete=models.PROTECT, related_name='tramos', null=True, blank=True)
    transporte = models.ForeignKey(Transporte, on_delete=models.PROTECT, related_name='tramos')
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, verbose_name="CD")
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT, related_name='tramos',
                                  help_text="Conductor que realmente ingresó (por defecto, el programado).")

    entrada_en = models.DateTimeField(verbose_name="Entrada")
    fecha_local_entrada = models.DateField(db_index=True)
    hora_local_entrada = models.TimeField()
    # En la Pre-Ruta es la lectura al salir a ruta (o la copia de la Post-Ruta si durmió en el CD).
    km_entrada = models.PositiveIntegerField(null=True, blank=True, verbose_name="Kilometraje de entrada")
    obs_entrada = models.TextField(default='Ninguna', verbose_name="Observación de entrada")
    # Vacío = entrada registrada por el sistema (inicio automático de la Pre-Ruta).
    vigilante_entrada = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                          null=True, blank=True)

    salida_en = models.DateTimeField(null=True, blank=True, verbose_name="Salida")
    fecha_local_salida = models.DateField(null=True, blank=True)
    obs_salida = models.TextField(blank=True, verbose_name="Observación de salida")
    vigilante_salida = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                         null=True, blank=True)

    abierto = models.BooleanField(null=True, default=True, editable=False)
    vigente = models.BooleanField(null=True, default=True, editable=False)

    minutos_permanencia = models.PositiveIntegerField(null=True, blank=True)
    # Llegada menos hora programada: solo en las visitas programadas (Pre-Ruta y Post-Ruta no llegan a una cita).
    desviacion_llegada_min = models.IntegerField(null=True, blank=True,
                                                 help_text="Llegada menos hora programada (negativo = adelantado).")
    # En las rutas INTERNO la hora programada es la de SALIDA del CD (decisión del usuario del 14/10/2026):
    # salida menos hora programada. La primera ruta del día la atiende la salida de la Pre-Ruta.
    desviacion_salida_min = models.IntegerField(null=True, blank=True,
                                                help_text="Rutas INTERNO: salida menos hora programada (negativo = antes).")
    tamano_snapshot = models.ForeignKey(TamanoVehiculo, on_delete=models.PROTECT, related_name='+')
    tipo_snapshot = models.CharField(max_length=10, choices=Programacion.Tipo.choices)

    fuera_de_ventana = models.BooleanField(default=False)
    km_inconsistente = models.BooleanField(default=False)
    corregido = models.BooleanField(default=False)
    cierre_administrativo = models.BooleanField(default=False)
    cierre_automatico = models.BooleanField(
        default=False, help_text="Salida puesta por el sistema al iniciar la Pre-Ruta (fin de la pernocta).")
    salida_tras_cierre = models.BooleanField(default=False, help_text="Salió del CD después de su Post-Ruta.")
    dia_no_laborable = models.BooleanField(default=False, help_text="Movimiento en domingo o feriado.")

    class Meta:
        verbose_name = "Tramo"
        verbose_name_plural = "Tramos"
        ordering = ['-entrada_en']
        indexes = [
            models.Index(fields=['cd', 'fecha_local_entrada'], name='accesos_tramo_cd_fecha_idx'),
            models.Index(fields=['transporte', 'entrada_en'], name='accesos_tramo_transp_ent_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['transporte', 'abierto'], name='accesos_tramo_un_abierto_por_placa'),
            models.UniqueConstraint(fields=['programacion', 'vigente'], name='accesos_tramo_un_vigente_por_prog'),
            models.CheckConstraint(condition=Q(salida_en__isnull=True) | Q(salida_en__gte=models.F('entrada_en')),
                                   name='accesos_tramo_salida_posterior'),
            models.CheckConstraint(condition=Q(abierto__isnull=True) | Q(abierto=True),
                                   name='accesos_tramo_abierto_true_o_null'),
            models.CheckConstraint(condition=Q(vigente__isnull=True) | Q(vigente=True),
                                   name='accesos_tramo_vigente_true_o_null'),
        ]

    def __str__(self):
        return f"Tramo {self.transporte} {self.fecha_local_entrada:%d/%m/%Y} {self.hora_local_entrada:%H:%M}"

    @property
    def esta_abierto(self):
        return bool(self.abierto)

    @property
    def esta_anulado(self):
        return not self.vigente

    @property
    def entre_dias(self):
        return bool(self.fecha_local_salida and self.fecha_local_salida != self.fecha_local_entrada)

    @property
    def es_programado(self):
        return self.clase == self.Clase.PROGRAMADO

    @property
    def es_de_jornada(self):
        return self.clase != self.Clase.PROGRAMADO

    @property
    def descripcion(self):
        """Texto corto para listas: la razón social y el motivo, o la clase si es de la jornada."""
        if self.es_de_jornada:
            return f"{self.get_clase_display()} · flota propia"
        return self.programacion.razon_social if self.programacion_id else ''

    @property
    def califica_salida(self):
        """En las rutas INTERNO se califica la salida (su hora programada es la de salida); en las demás, la
        llegada."""
        return self.tipo_snapshot == Programacion.Tipo.INTERNO

    @property
    def desviacion_min(self):
        """La desviación que se califica (puntualidad): de salida en las rutas INTERNO, de llegada en las demás.
        None si el tramo no atiende una programación o si la ruta INTERNO todavía no sale."""
        return self.desviacion_salida_min if self.califica_salida else self.desviacion_llegada_min

    def save(self, *args, **kwargs):
        """Después de guardar, el último kilometraje del transporte se recalcula desde sus tramos válidos (vale
        para la portería, las correcciones, las anulaciones y los datos de demostración)."""
        super().save(*args, **kwargs)
        from . import kilometraje  # import diferido: kilometraje usa estos modelos
        kilometraje.actualizar(self.transporte_id)


# --- AUDITORÍA ---

class RegistroAuditoria(models.Model):
    """Bitácora inmutable: quién hizo qué, cuándo, sobre qué registro, y los valores antes/después."""

    class Accion(models.TextChoices):
        CREAR = 'CREAR', 'Crear'
        MODIFICAR = 'MODIFICAR', 'Modificar'
        CANCELAR = 'CANCELAR', 'Cancelar'
        ENTRADA = 'ENTRADA', 'Entrada'
        SALIDA = 'SALIDA', 'Salida'
        CORREGIR = 'CORREGIR', 'Corregir'
        ANULAR = 'ANULAR', 'Anular'
        CIERRE_ADMIN = 'CIERRE_ADMIN', 'Cierre administrativo'
        CONFIG = 'CONFIG', 'Configuración'
        INCIDENCIA = 'INCIDENCIA', 'Incidencia (no disponible)'
        HABILITAR = 'HABILITAR', 'Habilitar vehículo'

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    # Vacío = acción automática del sistema (p. ej. la desactivación de avisos de licencias vencidas).
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                null=True, blank=True)
    accion = models.CharField(max_length=20, choices=Accion.choices)
    entidad = models.CharField(max_length=60)
    objeto_id = models.PositiveBigIntegerField()
    objeto_repr = models.CharField(max_length=200, verbose_name="Registro")
    datos_antes = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    datos_despues = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    motivo = models.TextField(blank=True)
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"
        ordering = ['-fecha', '-id']
        indexes = [
            models.Index(fields=['entidad', 'objeto_id'], name='accesos_audit_objeto_idx'),
        ]

    def __str__(self):
        return f"{self.get_accion_display()} {self.entidad} #{self.objeto_id} por {self.autor}"

    @property
    def autor(self):
        return self.usuario or "Sistema"
