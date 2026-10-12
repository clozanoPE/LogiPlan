from django import forms
from django.core.exceptions import ValidationError

from . import licencias, servicios, tiempo
from .models import (CATEGORIAS_LICENCIA, CATEGORIAS_LICENCIA_ESPECIAL, CentroDistribucion, Conductor,
                     ConfiguracionAccesos, Programacion, TamanoVehiculo, TipoDocumentoIdentidad, Transporte,
                     normalizar_licencia)

CLASE_CAMPO = 'campo-accesos'


def etiqueta_transporte(transporte):
    """Texto de la opción de transporte en la programación (también lo usa el registro rápido)."""
    return f"{transporte.placa} · {transporte.tamano}{' · interno' if transporte.es_interno else ''}"


def etiqueta_conductor(conductor):
    return f"{conductor.nombre_completo} · {conductor.tipo_documento} {conductor.numero_documento}"


def _activos_mas_actual(modelo, pk_actual):
    """Registros activos, más el ya asignado aunque se haya desactivado (para no perderlo al editar)."""
    activos = modelo.objects.filter(activo=True)
    if pk_actual:
        activos = activos | modelo.objects.filter(pk=pk_actual)
    return activos.distinct()


class TransporteForm(forms.ModelForm):
    class Meta:
        model = Transporte
        fields = ['placa', 'tamano', 'es_interno', 'activo']
        widgets = {
            'placa': forms.TextInput(attrs={
                'class': CLASE_CAMPO, 'placeholder': 'F7J-011', 'autocomplete': 'off',
                'style': 'text-transform: uppercase;',
            }),
            'tamano': forms.Select(attrs={'class': CLASE_CAMPO}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tamano'].queryset = _activos_mas_actual(TamanoVehiculo, self.instance.tamano_id)
        self.fields['tamano'].empty_label = 'Seleccione un tamaño'


CAMPOS_IDENTIDAD_CONDUCTOR = ['tipo_documento', 'numero_documento', 'apellido_paterno', 'apellido_materno',
                               'nombres', 'activo']
CAMPOS_LICENCIA = ['licencia_numero', 'licencia_categoria', 'licencia_vencimiento',
                   'licencia_especial_categoria', 'licencia_especial_vencimiento', 'notificar_vencimiento']


class ConductorForm(forms.ModelForm):
    """Alta y edición del conductor con su licencia. Con `solo_licencia=True` (Despacho al editar)
    los datos de identidad quedan bloqueados y solo se cambian la licencia y las notificaciones."""

    class Meta:
        model = Conductor
        fields = CAMPOS_IDENTIDAD_CONDUCTOR + CAMPOS_LICENCIA
        widgets = {
            'tipo_documento': forms.Select(attrs={'class': CLASE_CAMPO}),
            'numero_documento': forms.TextInput(attrs={'class': CLASE_CAMPO, 'autocomplete': 'off'}),
            'apellido_paterno': forms.TextInput(attrs={'class': CLASE_CAMPO}),
            'apellido_materno': forms.TextInput(attrs={'class': CLASE_CAMPO}),
            'nombres': forms.TextInput(attrs={'class': CLASE_CAMPO}),
            'licencia_numero': forms.TextInput(attrs={'class': CLASE_CAMPO, 'autocomplete': 'off',
                                                      'placeholder': 'Q43028991',
                                                      'style': 'text-transform: uppercase;'}),
            'licencia_categoria': forms.Select(attrs={'class': CLASE_CAMPO}),
            'licencia_vencimiento': forms.DateInput(format='%Y-%m-%d', attrs={'class': CLASE_CAMPO, 'type': 'date'}),
            'licencia_especial_categoria': forms.Select(attrs={'class': CLASE_CAMPO}),
            'licencia_especial_vencimiento': forms.DateInput(format='%Y-%m-%d',
                                                             attrs={'class': CLASE_CAMPO, 'type': 'date'}),
        }

    def __init__(self, *args, solo_licencia=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.solo_licencia = solo_licencia
        if 'tipo_documento' in self.fields:
            self.fields['tipo_documento'].queryset = _activos_mas_actual(TipoDocumentoIdentidad,
                                                                         self.instance.tipo_documento_id)
            self.fields['tipo_documento'].empty_label = None
        if 'licencia_categoria' in self.fields:
            self.fields['licencia_categoria'].choices = [('', 'Sin licencia')] + CATEGORIAS_LICENCIA
            self.fields['licencia_especial_categoria'].choices = [('', 'No tiene')] + CATEGORIAS_LICENCIA_ESPECIAL
        if solo_licencia:
            for nombre in CAMPOS_IDENTIDAD_CONDUCTOR:
                self.fields[nombre].disabled = True
        # Vencimientos antes del cambio, para reactivar las notificaciones al renovar.
        self._vencimientos_antes = (self.instance.licencia_vencimiento, self.instance.licencia_especial_vencimiento)

    def clean_licencia_numero(self):
        return normalizar_licencia(self.cleaned_data.get('licencia_numero'))

    def clean(self):
        datos = super().clean()
        if 'notificar_vencimiento' in self.fields and 'notificar_vencimiento' not in self.changed_data:
            despues = (datos.get('licencia_vencimiento'), datos.get('licencia_especial_vencimiento'))
            if licencias.renovada(self._vencimientos_antes, despues, tiempo.hoy()):
                datos['notificar_vencimiento'] = True  # renovó: vuelve a avisar
        return datos

    def clean_apellido_paterno(self):
        return self.cleaned_data['apellido_paterno'].strip().upper()

    def clean_apellido_materno(self):
        return self.cleaned_data['apellido_materno'].strip().upper()

    def clean_nombres(self):
        return self.cleaned_data['nombres'].strip().upper()


class ConductorRapidoForm(ConductorForm):
    """Registro rápido desde la programación: solo identidad (la licencia se completa después).
    Las notificaciones quedan activas por defecto."""

    class Meta(ConductorForm.Meta):
        fields = CAMPOS_IDENTIDAD_CONDUCTOR


class ProgramacionForm(forms.ModelForm):
    """Alta y edición de programaciones (R1, R7, R8, R9). Las advertencias (R5, R6) se
    calculan después de guardar con servicios.avisos() y no impiden guardar."""

    class Meta:
        model = Programacion
        fields = ['cd', 'fecha', 'hora', 'tipo', 'transporte', 'conductor',
                  'documento', 'razon_social', 'motivo', 'observacion_despacho']
        widgets = {
            'cd': forms.Select(attrs={'class': CLASE_CAMPO}),
            'fecha': forms.DateInput(format='%Y-%m-%d', attrs={'class': CLASE_CAMPO, 'type': 'date'}),
            'hora': forms.TimeInput(format='%H:%M', attrs={'class': CLASE_CAMPO, 'type': 'time',
                                                           'list': 'horarios-sugeridos'}),
            'tipo': forms.Select(attrs={'class': CLASE_CAMPO}),
            'transporte': forms.Select(attrs={'class': CLASE_CAMPO}),
            'conductor': forms.Select(attrs={'class': CLASE_CAMPO}),
            'documento': forms.TextInput(attrs={'class': CLASE_CAMPO, 'inputmode': 'numeric',
                                                'autocomplete': 'off', 'maxlength': 11}),
            'razon_social': forms.TextInput(attrs={'class': CLASE_CAMPO}),
            'motivo': forms.TextInput(attrs={'class': CLASE_CAMPO, 'list': 'motivos-frecuentes'}),
            'observacion_despacho': forms.Textarea(attrs={'class': CLASE_CAMPO, 'rows': 2}),
        }

    def __init__(self, *args, ahora=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ahora = ahora or tiempo.ahora()
        self.config = ConfiguracionAccesos.obtener()
        instancia = self.instance
        self.fields['cd'].queryset = _activos_mas_actual(CentroDistribucion, instancia.cd_id)
        self.fields['transporte'].queryset = _activos_mas_actual(Transporte, instancia.transporte_id).select_related('tamano')
        self.fields['conductor'].queryset = _activos_mas_actual(Conductor, instancia.conductor_id)
        self.fields['transporte'].label_from_instance = etiqueta_transporte
        self.fields['conductor'].label_from_instance = etiqueta_conductor
        # En INTERNO se completan solos con los datos de la empresa (ver clean()).
        self.fields['documento'].required = False
        self.fields['razon_social'].required = False
        if not instancia.pk:
            self.fields['fecha'].widget.attrs['min'] = tiempo.hoy().isoformat()

    def clean_documento(self):
        return self.cleaned_data['documento'].strip()

    def clean(self):
        datos = super().clean()
        tipo = datos.get('tipo')
        if tipo == Programacion.Tipo.INTERNO:
            datos['documento'] = datos.get('documento') or self.config.ruc_empresa
            datos['razon_social'] = datos.get('razon_social') or self.config.razon_social_empresa
        for campo, etiqueta in (('documento', 'el RUC o DNI'), ('razon_social', 'la razón social')):
            if tipo and not datos.get(campo) and campo not in self.errors:
                self.add_error(campo, f"Ingrese {etiqueta}.")

        fecha, hora = datos.get('fecha'), datos.get('hora')
        if fecha and hora:
            try:
                servicios.validar_horario_futuro(fecha, hora, self.ahora)
            except ValidationError as error:
                self.add_error('hora', error)
        return datos

    def _post_clean(self):
        super()._post_clean()
        # Placa duplicada en la misma fecha y hora (se valida sobre la instancia ya armada).
        if not self.errors:
            try:
                servicios.validar_placa_sin_duplicar(self.instance)
            except ValidationError as error:
                self.add_error('transporte', error)


class CancelacionForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo de la cancelación", max_length=500,
        widget=forms.Textarea(attrs={'class': CLASE_CAMPO, 'rows': 2,
                                     'placeholder': 'Ej.: El cliente reprogramó su recojo.'}),
    )


class EntradaForm(forms.Form):
    """Confirmación de ENTRADA en portería. Las reglas se validan en servicios.registrar_entrada."""
    transporte = forms.IntegerField(widget=forms.HiddenInput)
    programacion = forms.IntegerField(error_messages={'required': 'Elija la programación.'})
    km = forms.IntegerField(min_value=0, max_value=9_999_999, label="Kilometraje",
                            error_messages={'required': 'Ingrese el kilometraje.'})
    obs = forms.CharField(required=False, max_length=500, label="Observación de entrada")
    conductor = forms.IntegerField(required=False)
    confirmar_fuera_de_ventana = forms.BooleanField(required=False)


class SalidaForm(forms.Form):
    transporte = forms.IntegerField(widget=forms.HiddenInput)
    tramo = forms.IntegerField(widget=forms.HiddenInput)
    obs = forms.CharField(required=False, max_length=500, label="Observación de salida")


class CampoFechaHora(forms.DateTimeField):
    widget = forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': CLASE_CAMPO})

    def __init__(self, **kwargs):
        kwargs.setdefault('input_formats', ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'])
        super().__init__(**kwargs)


class MotivoForm(forms.Form):
    motivo = forms.CharField(label="Motivo", max_length=500,
                             widget=forms.Textarea(attrs={'class': CLASE_CAMPO, 'rows': 2}))


class CierreAdministrativoForm(MotivoForm):
    salida_en = CampoFechaHora(label="Hora de salida estimada")


class CorreccionTramoForm(MotivoForm):
    """Corrección del Administrador. Debe usarse dentro de tiempo.con_hora_lima."""
    entrada_en = CampoFechaHora(label="Entrada")
    salida_en = CampoFechaHora(label="Salida", required=False)
    km_entrada = forms.IntegerField(label="Kilometraje de entrada", min_value=0, max_value=9_999_999,
                                    widget=forms.NumberInput(attrs={'class': CLASE_CAMPO}))
    obs_entrada = forms.CharField(label="Observación de entrada", required=False, max_length=500,
                                  widget=forms.TextInput(attrs={'class': CLASE_CAMPO}))
    obs_salida = forms.CharField(label="Observación de salida", required=False, max_length=500,
                                 widget=forms.TextInput(attrs={'class': CLASE_CAMPO}))
    programacion = forms.ModelChoiceField(queryset=Programacion.objects.none(), label="Programación",
                                          widget=forms.Select(attrs={'class': CLASE_CAMPO}))
    conductor = forms.ModelChoiceField(queryset=Conductor.objects.none(), label="Conductor que ingresó",
                                       widget=forms.Select(attrs={'class': CLASE_CAMPO}))
    field_order = ['programacion', 'entrada_en', 'salida_en', 'km_entrada', 'conductor', 'obs_entrada',
                   'obs_salida', 'motivo']

    def __init__(self, *args, tramo, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['programacion'].queryset = servicios.programaciones_para_corregir(tramo)
        self.fields['programacion'].label_from_instance = (
            lambda p: f"{p.codigo} · {p.fecha:%d/%m/%Y} {p.hora:%H:%M} · CD {p.cd}")
        self.fields['conductor'].queryset = _activos_mas_actual(Conductor, tramo.conductor_id)
        self.fields['conductor'].label_from_instance = etiqueta_conductor
        if not tramo.salida_en:
            # Un tramo abierto se cierra con el cierre administrativo, no aquí.
            del self.fields['salida_en']
            del self.fields['obs_salida']
