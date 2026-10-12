from django.contrib.auth.models import Group, User

from ..models import TamanoVehiculo, TipoDocumentoIdentidad
from ..permisos import GRUPO_ADMINISTRADOR, GRUPO_CONSULTA, GRUPO_DESPACHO, GRUPO_VIGILANCIA

CLAVE = 'clave-de-prueba-123'


def crear_usuario(username, *grupos, **extra):
    usuario = User.objects.create_user(username=username, password=CLAVE, **extra)
    for nombre in grupos:
        grupo, _ = Group.objects.get_or_create(name=nombre)
        usuario.groups.add(grupo)
    return usuario


class UsuariosMixin:
    """Un usuario por cada perfil relevante para permisos y regresión."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_accesos = crear_usuario('admin_accesos', GRUPO_ADMINISTRADOR, is_staff=True)
        cls.despacho = crear_usuario('despacho', 'Planeamiento', GRUPO_DESPACHO)
        cls.vigilante = crear_usuario('vigilante', GRUPO_VIGILANCIA)
        cls.consulta = crear_usuario('consulta', GRUPO_CONSULTA)
        cls.planeamiento = crear_usuario('planeamiento', 'Planeamiento')
        cls.comercial = crear_usuario('comercial', 'Comercial')
        cls.sin_grupo = crear_usuario('sin_grupo')
        cls.superusuario = User.objects.create_superuser('super', 'super@example.com', CLAVE)
        cls.tamano = TamanoVehiculo.objects.create(nombre='Camión grande', orden=3)
        cls.dni = TipoDocumentoIdentidad.objects.get(codigo='DNI')
        cls.ce = TipoDocumentoIdentidad.objects.get(codigo='CE')

    def entrar(self, usuario):
        self.client.force_login(usuario)
