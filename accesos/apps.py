from django.apps import AppConfig


class AccesosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accesos'
    verbose_name = 'Control de Accesos de Transporte'

    def ready(self):
        # Registra los receptores de señales (duración de sesión de Vigilancia).
        from . import signals  # noqa: F401
