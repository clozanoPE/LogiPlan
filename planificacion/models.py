from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from datetime import date
import calendar

class ConfiguracionCapacidad(models.Model):
    """
    Capa de Configuración: Almacena parámetros globales del sistema.
    Permite ajustar el límite de camiones sin tocar el código.
    """
    limite_max = models.IntegerField(
        default=7, 
        verbose_name="Límite Máximo de Unidades (Placas)"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Capacidad"
        verbose_name_plural = "Configuraciones de Capacidad"

    def __str__(self):
        return f"Capacidad Máxima: {self.limite_max} unidades"


class CapacidadDiaria(models.Model):
    """
    Capa de Configuración Dinámica: Permite establecer límites de placas 
    específicos para fechas determinadas (ej. camiones en taller, feriados).
    """
    fecha = models.DateField(
        unique=True, 
        verbose_name="Fecha Específica",
        db_index=True
    )
    limite_personalizado = models.IntegerField(
        verbose_name="Límite para este día"
    )
    motivo = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        help_text="Ej: Mantenimiento de unidades / Pico de demanda"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Capacidad Diaria Específica"
        verbose_name_plural = "Capacidades Diarias Específicas"
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.fecha} | Límite: {self.limite_personalizado}"


class PlanificacionDespacho(models.Model):
    """
    Capa de Planificación Core: Estructura principal de datos.
    Incluye campos de auditoría, lógica de semanas y validaciones de integridad.
    """
    # Datos del Despacho
    fecha_despacho = models.DateField(db_index=True, verbose_name="Fecha de Despacho")
    cliente = models.CharField(max_length=150, verbose_name="Nombre del Cliente")
    sede = models.CharField(max_length=200, verbose_name="Sede de Destino")
    
    # Datos de la Unidad
    unidad_placa = models.CharField(max_length=20, verbose_name="Placa de la Unidad")
    conductor = models.CharField(max_length=255, verbose_name="Nombre del Conductor")
    
    # Referencias Documentarias
    orden_compra = models.CharField(max_length=100, verbose_name="Orden de Compra (OC)")
    carga_origen = models.CharField(max_length=150, verbose_name="Punto de Carga / Origen")
    pallets = models.PositiveIntegerField(verbose_name="Cantidad de Pallets")
    
    # Capa de Auditoría y Seguridad
    creado_por = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        verbose_name="Usuario Registrador"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    ultima_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Planificación de Despacho"
        verbose_name_plural = "Planificaciones de Despachos"
        ordering = ['-fecha_despacho', 'cliente']

    @property
    def numero_semana(self):
        """Retorna el número de semana del año (ISO)"""
        return self.fecha_despacho.isocalendar()[1]

    @property
    def nombre_dia(self):
        """Retorna el nombre del día en español"""
        dias = {
            0: 'Lunes', 1: 'Martes', 2: 'Miércoles', 
            3: 'Jueves', 4: 'Viernes', 5: 'Sábado', 6: 'Domingo'
        }
        return dias[self.fecha_despacho.weekday()]

    @property
    def mes_nombre(self):
        """Retorna el nombre del mes"""
        meses = {
            1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
            5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
            9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
        }
        return meses[self.fecha_despacho.month]

    def __str__(self):
        return f"Sem {self.numero_semana} | {self.nombre_dia} | {self.unidad_placa}"

    def clean(self):
        """
        Capa de Reglas de Negocio (Business Logic):
        Validaciones de capacidad dinámica y restricciones de tiempo.
        """
        super().clean()
        
        # 1. Validación de restricción temporal (Bloqueo de pasado)
        if not self.pk:
            if self.fecha_despacho < date.today():
                raise ValidationError({
                    'fecha_despacho': "No es posible registrar despachos en fechas pasadas."
                })
        else:
            original = PlanificacionDespacho.objects.get(pk=self.pk)
            # Solo validar si se intenta cambiar la fecha a una pasada o si ya era pasada
            if original.fecha_despacho < date.today() and self.fecha_despacho < date.today():
                raise ValidationError("Restricción de Seguridad: No se pueden editar registros históricos.")

        # 2. Validación de Capacidad Máxima (Placas Únicas)
        if self.fecha_despacho:
            # Determinar el límite: ¿Hay excepción diaria o usamos el global?
            excepcion = CapacidadDiaria.objects.filter(fecha=self.fecha_despacho).first()
            if excepcion:
                limite_permitido = excepcion.limite_personalizado
            else:
                config = ConfiguracionCapacidad.objects.first()
                limite_permitido = config.limite_max if config else 7
            
            # Obtener placas ya registradas para ese día (excluyendo este registro si es edición)
            despachos_dia = PlanificacionDespacho.objects.filter(
                fecha_despacho=self.fecha_despacho
            ).exclude(pk=self.pk)
            
            placas_existentes = set(despachos_dia.values_list('unidad_placa', flat=True))

            # Si la placa que intento registrar NO está en la lista de hoy, verificamos si hay cupo
            if self.unidad_placa not in placas_existentes:
                if len(placas_existentes) >= limite_permitido:
                    pass
                    #raise ValidationError(
                    #    f"Capacidad Crítica: Se ha alcanzado el límite de {limite_permitido} unidades únicas para el día {self.fecha_despacho} ({self.nombre_dia})."
                    #)

    def save(self, *args, **kwargs):
        # Forzamos la ejecución de clean() antes de guardar en la DB
        self.full_clean()
        super().save(*args, **kwargs)
