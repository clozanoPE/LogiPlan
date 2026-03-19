from django import forms
from .models import PlanificacionDespacho, ConfiguracionCapacidad, CapacidadDiaria
from django.utils import timezone
from django.core.exceptions import ValidationError

class PlanificacionForm(forms.ModelForm):
    class Meta:
        model = PlanificacionDespacho
        fields = [
            'fecha_despacho', 'cliente', 'sede', 'unidad_placa', 
            'conductor', 'orden_compra', 'carga_origen', 'pallets'
        ]
        # Widgets para añadir clases de CSS (Tailwind) y placeholders
        widgets = {
            'fecha_despacho': forms.DateInput(attrs={
                'type': 'date', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'  # Asegura espacio para el icono
            }),
            'cliente': forms.TextInput(attrs={
                'placeholder': 'Nombre del Cliente', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'sede': forms.TextInput(attrs={
                'placeholder': 'Ej. Sede Lurin', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'unidad_placa': forms.TextInput(attrs={
                'placeholder': 'ABC-123', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'conductor': forms.TextInput(attrs={
                'placeholder': 'Nombre Completo', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'orden_compra': forms.TextInput(attrs={
                'placeholder': 'N° de OC', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'carga_origen': forms.TextInput(attrs={
                'placeholder': 'Punto de Carga', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
            'pallets': forms.NumberInput(attrs={
                'min': '1', 
                'class': 'form-input-styled',
                'style': 'padding-left: 3.5rem;'
            }),
        }

    def clean_fecha_despacho(self):
        """Regla PRO: No permitir fechas pasadas"""
        fecha = self.cleaned_data.get('fecha_despacho')
        if fecha and fecha < timezone.now().date():
            raise ValidationError("⚠️ No se puede programar en fechas pasadas. Seleccione hoy o una fecha futura.")
        return fecha

    def clean(self):
        """Reglas de Negocio combinadas: Capacidad Dinámica y Duplicados"""
        cleaned_data = super().clean()
        fecha = cleaned_data.get('fecha_despacho')
        placa = cleaned_data.get('unidad_placa')

        if fecha and placa:
            # 1. Evitar que la misma placa se registre dos veces el mismo día
            existe = PlanificacionDespacho.objects.filter(
                fecha_despacho=fecha, 
                unidad_placa=placa
            ).exclude(pk=self.instance.pk).exists()
            
            if existe:
                raise ValidationError(f"La unidad {placa} ya está programada para el {fecha}.")

            # 2. Validar contra el límite de capacidad (Dinámico: Diaria o Global)
            
            # Buscamos primero si hay una excepción para ese día
            excepcion = CapacidadDiaria.objects.filter(fecha=fecha).first()
            
            if excepcion:
                limite = excepcion.limite_personalizado
            else:
                # Si no hay excepción, usamos la configuración global
                config = ConfiguracionCapacidad.objects.first()
                limite = config.limite_max if config else 7
            
            # Contamos cuántas unidades únicas (placas) ya existen para ese día
            # Excluimos la instancia actual por si estamos editando
            total_dia = PlanificacionDespacho.objects.filter(
                fecha_despacho=fecha
            ).exclude(pk=self.instance.pk).values('unidad_placa').distinct().count()
            
            if total_dia >= limite:
                # Ya no lanzamos ValidationError para permitir el registro.
                # El usuario podrá guardar, pero el Dashboard mostrará "Capacidad Excedida".
                pass

            # Si la placa actual es nueva para ese día, verificamos el límite
            # Nota: Si la placa ya existe para ese día, la validación (1) ya habrá saltado.
            #if total_dia >= limite:
            #    raise ValidationError(
            #        f"Capacidad máxima alcanzada para el {fecha}. "
            #        f"El límite es de {limite} unidades y ya hay {total_dia} programadas."
            #    )
        
        return cleaned_data