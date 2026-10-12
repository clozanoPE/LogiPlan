"""
Modelos del módulo de Control de Accesos de Transporte (Fase F1: catálogos, maestros,
configuración y auditoría). Programación y Tramo llegan en F2 y F3.

Ningún modelo de esta app tiene relación con `planificacion`; la única relación externa
es hacia el usuario de Django (auth_user).
"""
import re

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
    modificado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Accesos"
        verbose_name_plural = "Configuración de Accesos"
        constraints = [
            models.CheckConstraint(condition=Q(ventana_sugerencia_min__gt=0), name='accesos_config_ventana_positiva'),
            models.CheckConstraint(condition=Q(umbral_prolongada_min__gt=0), name='accesos_config_umbral_positivo'),
            models.CheckConstraint(condition=Q(sesion_vigilancia_horas__gt=0), name='accesos_config_sesion_positiva'),
            models.CheckConstraint(condition=Q(aviso_licencia_dias__gt=0), name='accesos_config_aviso_licencia_positivo'),
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


# --- MAESTROS ---

class Transporte(models.Model):
    """Vehículo físico. La placa normalizada es su identificador de negocio y el contenido del QR."""
    placa = models.CharField(max_length=10, verbose_name="Placa", help_text="Ej. F7J-011")
    placa_normalizada = models.CharField(max_length=10, unique=True, editable=False)
    tamano = models.ForeignKey(TamanoVehiculo, on_delete=models.PROTECT, verbose_name="Tamaño")
    es_interno = models.BooleanField(default=False, verbose_name="Transporte interno",
                                     help_text="Solo a los internos se les imprime el QR.")
    activo = models.BooleanField(default=True)

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
    hora = models.TimeField(verbose_name="Hora", help_text="Hora de Lima. Puede ser exacta (08:17).")
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


# --- TRAMO ---

class Tramo(models.Model):
    """Lo que ocurrió en portería: una entrada y su salida, asociadas a una programación.

    Dos restricciones en la base de datos protegen la operación aunque dos tablets registren
    a la vez (MySQL no admite índices únicos parciales, por eso se usan campos TRUE/NULL):
    - `abierto` = TRUE mientras el vehículo está dentro; único por transporte.
    - `vigente` = TRUE si el tramo es válido (NULL si se anuló); único por programación.
    """
    programacion = models.ForeignKey(Programacion, on_delete=models.PROTECT, related_name='tramos')
    transporte = models.ForeignKey(Transporte, on_delete=models.PROTECT, related_name='tramos')
    cd = models.ForeignKey(CentroDistribucion, on_delete=models.PROTECT, verbose_name="CD")
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT, related_name='tramos',
                                  help_text="Conductor que realmente ingresó (por defecto, el programado).")

    entrada_en = models.DateTimeField(verbose_name="Entrada")
    fecha_local_entrada = models.DateField(db_index=True)
    hora_local_entrada = models.TimeField()
    km_entrada = models.PositiveIntegerField(verbose_name="Kilometraje de entrada")
    obs_entrada = models.TextField(default='Ninguna', verbose_name="Observación de entrada")
    vigilante_entrada = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    salida_en = models.DateTimeField(null=True, blank=True, verbose_name="Salida")
    fecha_local_salida = models.DateField(null=True, blank=True)
    obs_salida = models.TextField(blank=True, verbose_name="Observación de salida")
    vigilante_salida = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                         null=True, blank=True)

    abierto = models.BooleanField(null=True, default=True, editable=False)
    vigente = models.BooleanField(null=True, default=True, editable=False)

    minutos_permanencia = models.PositiveIntegerField(null=True, blank=True)
    desviacion_llegada_min = models.IntegerField(help_text="Llegada menos hora programada (negativo = adelantado).")
    tamano_snapshot = models.ForeignKey(TamanoVehiculo, on_delete=models.PROTECT, related_name='+')
    tipo_snapshot = models.CharField(max_length=10, choices=Programacion.Tipo.choices)

    fuera_de_ventana = models.BooleanField(default=False)
    km_inconsistente = models.BooleanField(default=False)
    corregido = models.BooleanField(default=False)
    cierre_administrativo = models.BooleanField(default=False)

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
