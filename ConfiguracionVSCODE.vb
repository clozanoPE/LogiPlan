<?xml version="1.0" encoding="UTF-16"?><Install/>
PRJWEB/                 <-- Carpeta Raíz (Abierta en VS Code)
├── venv/               <-- ENTORNO VIRTUAL (Aquí NO se toca nada)
├── core/               <-- CONFIGURACIÓN (settings.py, urls.py)
├── planificacion/      <-- TU APP (models.py, views.py)
└── manage.py           <-- ARCHIVO DE CONTROL (El que ejecuta todo)

1.Crear el Entorno
python.exe -m venv venv
2. Activar el ENTORNO
.\venv\Scripts\activate
2.1
# Instala el framework Django
pip install django

# Instala el conector para que Django pueda escribir en tu MySQL
pip install mysqlclient

3.django-admin startproject core .
--Esto instalará el corazón del proyecto directamente en PRJWEB, dejando el archivo manage.py a la vista.
4.python manage.py startapp planificacion
--Crear la aplicación de logística
5.Configuras el archivo settings.py
6.Comandos en el Entorno venv activo
Ejecuta estos comandos en la terminal (con venv activo):
	Crear instrucciones de tabla:
	python manage.py makemigrations planificacion

	Crear tablas en MySQL:
	python manage.py migrate

	Crear acceso administrativo:
	python manage.py createsuperuser
	(Sigue las instrucciones: pon un usuario y clave que recuerdes, super usuario= clozano pwd=1234 ).


Propuesta de KPIs Estratégicos para LOGIPLAN
Tendencia de Utilización Semanal (Tu propuesta):
Un gráfico lineal compacto que muestra los 6 días de la semana actual. Permite ver de un vistazo
si la carga se está concentrando en los primeros días o si hay picos peligrosos el fin de semana.

Eficiencia de Flota (Promedio): 
El porcentaje promedio de uso de camiones en la semana actual. Un número alto indica rentabilidad; un número bajo, subutilización.

Día Crítico de la Semana: 
Identifica automáticamente qué día tiene el mayor volumen de unidades, permitiendo a Planeamiento anticipar la búsqueda de transportistas externos si es necesario.

Balance de Carga (Saturación): Un medidor (gauge) que indica cuántos días de la semana están por encima del 80% de capacidad.