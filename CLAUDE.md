# LogiPlan Web — Daryza S.A.C.

Sistema interno de gestión logística para la planificación diaria/semanal de despachos de carga (Sede Lurín). Controla cuántas unidades (placas) pueden operar por día, alerta sobre saturación de flota y permite a Planeamiento registrar, editar y eliminar programaciones de despacho.

## 1. Resumen del proyecto

- **Framework**: Django 6.0.3, servido en producción con **Gunicorn** (`core.wsgi:application`).
- **Base de datos**: MySQL (`mysqlclient`), configurada vía variables de entorno (`MYSQLDATABASE`, `MYSQLUSER`, `MYSQLPASSWORD`, `MYSQLHOST`, `MYSQLPORT`) con defaults locales (`root`/`127.0.0.1`) — patrón típico de Railway (plugin MySQL inyecta estas variables automáticamente).
- **Estáticos**: **WhiteNoise** (`CompressedManifestStaticFilesStorage`) sirve los estáticos directamente desde Gunicorn, sin necesidad de un servidor web adicional (Nginx/CDN) delante.
- **Frontend**: sin build system ni SPA. HTML renderizado por Django Templates, estilizado con **Tailwind CSS vía CDN** (`cdn.tailwindcss.com`, no está compilado/purgado) + **Font Awesome 6** (CDN) + fuente **Inter** (Google Fonts). Interactividad ligera con JavaScript vanilla embebido en cada plantilla (toggles de paneles, modal de confirmación, auto-refresh del dashboard).
- **Autenticación**: Django `django.contrib.auth` estándar. Autorización por pertenencia al grupo `Planeamiento` o `is_superuser` (ver sección 4). No hay app de terceros para permisos granulares (no DRF, no roles custom).
- **Dominio de despliegue**: `logiplan.daryza.pe` y `logiplan-production.up.railway.app` (Railway), configurados en `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` de `core/settings.py`.

## 2. Arquitectura y apps

Proyecto Django de dos piezas, sin apps adicionales:

```
core/                    → proyecto raíz Django
  settings.py             → configuración (DB, apps, middleware, static)
  urls.py                 → enrutador raíz (admin/ + include planificacion.urls)
  wsgi.py / asgi.py        → entrypoints de servidor
  templates/base.html      → layout maestro (nav, toasts, footer) que heredan todas las páginas
  crear_admin.py           → script de bootstrap: crea grupos (Planeamiento, Comercial) y un superusuario si no existe

planificacion/            → única app de negocio instalada
  models.py                → ConfiguracionCapacidad, CapacidadDiaria, PlanificacionDespacho
  views.py                 → toda la lógica de negocio y cálculo de KPIs (function-based views)
  forms.py                 → PlanificacionForm (ModelForm con validaciones de negocio)
  admin.py                 → registro de los 3 modelos en Django Admin
  urls.py                  → rutas de la app (dashboard, CRUD, login/logout, ajustes de capacidad)
  templates/planificacion/ → dashboard.html, detalle_dia.html, lista_busqueda.html, form_planificacion.html, login.html, estadisticas.html, carga_masiva.html

static/img/                → logos Daryza (logo.png, logo0/1/7/26.png)
```

**Flujo de request**: `core/urls.py` monta `admin/` y delega todo lo demás a `planificacion.urls`, que expone el dashboard en la raíz (`/`).

⚠️ **Nota técnica detectada durante el análisis**: `core/urls.py` referencia `redirect(...)` dentro de un `lambda` en la ruta `accounts/login/` sin importar `redirect` desde `django.shortcuts`. Esa ruta específica fallaría (`NameError`) si llegara a invocarse; no afecta el flujo normal porque `LOGIN_URL` apunta a `'login'` (la vista de `planificacion.urls`), pero conviene revisarlo si Django redirige ahí automáticamente en algún flujo de admin.

⚠️ **Nota de seguridad**: `crear_admin.py` crea un superusuario `admin` con contraseña hardcodeada en texto plano dentro del script, y `core/settings.py` tiene `DEBUG = True` y una `SECRET_KEY` hardcodeada en el repositorio. Recomendable mover a variables de entorno antes de un uso productivo formal.

## 3. Estructura de Base de Datos (app `planificacion`)

Tres modelos, sin relaciones ForeignKey entre sí (el vínculo entre `PlanificacionDespacho` y la capacidad del día es **lógico**, resuelto en runtime por fecha, no por FK):

### `ConfiguracionCapacidad`
Configuración global, singleton en la práctica (se usa `.first()` / `get_or_create(id=1)`).
- `limite_max` (int, default 7) — límite estándar de unidades/placas por día.
- `updated_at` (auto).

### `CapacidadDiaria`
Excepciones puntuales al límite global, por fecha específica (feriados, mantenimiento, picos de demanda).
- `fecha` (date, **unique**, indexada).
- `limite_personalizado` (int).
- `motivo` (texto libre, opcional).
- `updated_at` (auto).
- Ordenado por `-fecha`.

### `PlanificacionDespacho`
Entidad central: un registro = una unidad (placa) asignada a un cliente/sede en una fecha.
- Despacho: `fecha_despacho` (indexada), `cliente`, `sede`.
- Unidad: `unidad_placa`, `conductor`.
- Documentación: `orden_compra`, `carga_origen`, `pallets`.
- Comercial: `costo_transporte` (`DecimalField`, `max_digits=10, decimal_places=2`, `null=True, blank=True, default=0.00`) — agregado vía migración `0003_planificaciondespacho_costo_transporte`, opcional en el formulario, retrocompatible (filas existentes quedaron en `0.00`).
- Auditoría: `creado_por` (FK a `User`, `on_delete=PROTECT`), `fecha_creacion`, `ultima_modificacion`.
- Propiedades calculadas: `numero_semana` (ISO), `nombre_dia` (español), `mes_nombre` (español).
- Ordenado por `-fecha_despacho, cliente`.

**Reglas de negocio encapsuladas en `clean()` / `save()` (y replicadas en `PlanificacionForm.clean()`)**:
1. No se permite crear despachos con `fecha_despacho` en el pasado.
2. No se pueden editar registros cuya fecha ya pasó (bloqueo histórico).
3. La capacidad permitida por día se resuelve con jerarquía: `CapacidadDiaria` (excepción de esa fecha) → si no existe, `ConfiguracionCapacidad.limite_max` (global) → si no existe ninguna config, `7` por defecto.
4. Una misma placa no puede registrarse dos veces el mismo día (validado en el formulario).
5. **Actualmente el sistema NO bloquea superar el límite de capacidad** — la validación que lo impedía está deliberadamente comentada tanto en `models.py` (`clean()`) como en `forms.py` (`clean()`); en su lugar, se permite guardar y el Dashboard señaliza visualmente el estado "SOBRESATURADO"/"CRÍTICO". Es una decisión de producto vigente, no un bug.

## 4. Estado actual de las vistas y UI

Todas las vistas requieren `@login_required`; las de escritura/gestión de capacidad además exigen `@user_passes_test(es_planeamiento)` (usuario en el grupo `Planeamiento` o superusuario). Los templates ocultan condicionalmente botones de edición para usuarios de solo lectura.

| Vista (`views.py`) | Ruta | Template | Función |
|---|---|---|---|
| `dashboard` | `/`, `/dashboard/` | `dashboard.html` | Panel principal: **Dashboard de Operaciones** |
| `registrar_planificacion` | `/nuevo/` | `form_planificacion.html` | Alta de despacho (solo Planeamiento) |
| `editar_planificacion` | `/editar/<pk>/` | `form_planificacion.html` | Edición (solo Planeamiento, fechas futuras) |
| `eliminar_planificacion` | `/eliminar-planificacion/<pk>/` | (redirect) | Baja con bloqueo histórico |
| `detalle_dia` | `/detalle/<fecha>/` | `detalle_dia.html` | Listado de despachos de un día puntual |
| `lista_busqueda` | `/busqueda/` | `lista_busqueda.html` | Historial con filtros (cliente, placa, rango de fechas) |
| `actualizar_capacidad` | `/capacidad/actualizar/` | (redirect) | Ajusta el límite global |
| `gestionar_capacidad_diaria` | `/ajustar-capacidad/` | (redirect) | Crea/actualiza una excepción de capacidad por fecha |
| `estadisticas_dashboard` | `/estadisticas/` | `estadisticas.html` | Módulo de solo lectura: reportes y Dashboards Pro filtrables (ver Sección 5) |
| `carga_masiva_excel` | `/programar/masivo/` | `carga_masiva.html` | Carga masiva de despachos por Excel (solo Planeamiento, ver Sección 6) |
| `descargar_plantilla_excel` | `/programar/masivo/plantilla/` | (descarga) | Plantilla `.xlsx` vacía con las 9 columnas requeridas |
| `exportar_busqueda_excel` | `/busqueda/exportar/` | (descarga) | Exporta a Excel el queryset filtrado completo de "Consultar" |
| `exportar_detalle_dia_excel` | `/detalle/<fecha>/exportar/` | (descarga) | Exporta a Excel los despachos de un día específico |
| Login/Logout (Django auth) | `/login/`, `/logout/` | `login.html` | Autenticación con branding Daryza |

**Navegación responsiva (móvil)** (`base.html`): la barra de navegación superior (`nav`) es la única plantilla que define el navbar (heredada por las 7 vistas), por lo que el soporte móvil vive enteramente ahí:
- Los 5 accesos principales ("Dashboard", "Programar", "Consultar", "Estadística", "Admin") siguen en `hidden md:flex` (visibles solo desde 768px) tal como antes.
- Por debajo de 768px aparece un **botón hamburguesa** (`#navMenuButton`, `md:hidden`) que despliega un **panel colapsable** (`#navPanel`) con los mismos 5 accesos en tap targets de ancho completo — no es un drawer/off-canvas: no usa overlay, focus trap ni bloqueo de scroll, porque reutiliza al 100% la infraestructura ya existente del dropdown de usuario (clases `.dropdown-menu`/`.show` y el keyframe `fadeIn` ya definidos en el `<style>` de `base.html`).
- El bloque `<script>` de `base.html` controla ambos menús (perfil de usuario y navegación móvil) con la misma lógica: toggle de `.show`, cierre al hacer click afuera, cierre con tecla `Escape`, y solo uno de los dos abierto a la vez. Ambos botones exponen `aria-expanded`/`aria-controls` para lectores de pantalla.
- Blindaje defensivo: una regla `@media (min-width: 768px) { #navPanel { display: none !important; } }` evita que el panel quede visible en desktop si el viewport crece (p. ej. rotación de tablet) mientras estaba abierto — no depende de la especificidad CSS entre `.dropdown-menu.show` y `md:hidden`.
- El auto-refresh del Dashboard (ver más abajo) conoce el estado de `#navPanel` para no recargar la página mientras el usuario tiene el menú móvil abierto.

**Patrón `.action-row`** (definido en el `<style>` de `base.html`, heredado por todas las vistas): clase CSS plana — no una utilidad Tailwind, ya que Tailwind se carga vía `cdn.tailwindcss.com` en modo JIT-en-navegador sin paso de build, por lo que `@apply`/`@layer components` en una hoja aparte no está disponible — que estandariza el comportamiento de filas de botones/acciones: `flex-direction: column` (hijos a `width: 100%`) por defecto, y desde `640px` pasa a `flex-direction: row` con `flex-wrap: wrap` (hijos a `width: auto`). Reemplaza los antiguos contenedores `flex ... gap-N` sin wrap que se recortaban o generaban scroll horizontal en móvil. Aplicado hoy en: el header de capacidad del Dashboard (`dashboard.html`) y la fila de acciones de `detalle_dia.html`. Para grillas de tarjetas/métricas que no comparten esta causa raíz (ej. el grid de resultados de `carga_masiva.html`), se resuelve con un prefijo de breakpoint Tailwind normal (`grid-cols-1 sm:grid-cols-3`) en vez de esta clase.

**Dashboard de Operaciones** (`dashboard.html`, vista `dashboard()`):
- Calendario de **2 semanas** (semana actual + siguiente), de lunes a sábado (domingo excluido de la grilla).
- **Navegación histórica por semanas** vía parámetro GET `?week_offset=N` (0 = semana actual, negativo = semanas pasadas), con **clamp defensivo en el servidor** a `[-12, 0]` — nunca permite proyectar semanas futuras aunque se manipule la URL manualmente. La ventana de 14 días (Semana N + Semana N+1) se desplaza completa según el offset; las excepciones de capacidad (`CapacidadDiaria`) y los KPIs se recalculan dinámicamente sobre el rango correspondiente a cada offset (`calcular_kpis_dashboard()` ya estaba parametrizada por `lunes_actual`, por lo que se reutiliza sin cambios). Controles `<<`/`>>` en la cabecera: `>>` se oculta en `week_offset = 0` (no hay "futuro" al que avanzar) y `<<` se oculta al llegar a `week_offset = -12` (límite histórico). El auto-refresh JS existente preserva el offset porque `location.reload()` conserva la URL completa, incluyendo el querystring.
- **Control de capacidad global**: cifra destacada con botones (solo Planeamiento) para abrir dos paneles inline:
  - Ajuste de **capacidad estándar global** (`ConfiguracionCapacidad`).
  - Ajuste de **capacidad por fecha específica** (crea/actualiza `CapacidadDiaria`, con campo `motivo`).
  - Este bloque completo (cifra + botones de ajuste + "Gestión / Filtros" + "Nuevo Registro") usa el patrón `.action-row` (ver más arriba) para apilarse verticalmente a ancho completo en móvil y volver a fila desde 640px, evitando que "Nuevo Registro" quede recortado por el `overflow-hidden` de la tarjeta contenedora.
- **4 tarjetas de KPI**, calculadas en `calcular_kpis_dashboard()` sobre la semana actual (lunes–sábado):
  - **Tendencia de Ocupación**: mini gráfico de barras por día (% de ocupación vs. capacidad, coloreado verde/ámbar/rojo).
  - **Eficiencia Semanal**: % de unidades usadas sobre capacidad total disponible en la semana.
  - **Día Crítico**: día con más unidades asignadas ("mayor demanda esperada").
  - **Alertas**: conteo de días que alcanzaron o superaron su límite (saturación total).
- **Tarjetas de día** (una por cada día del calendario): muestran conteo de placas únicas vs. límite del día, lista de **clientes registrados** ese día, barra de progreso y estado semafórico:
  - `NORMAL` (verde, <80%), `ADVERTENCIA` (ámbar, ≥80%), `CRÍTICO` (ámbar, =100%), `SOBRESATURADO` (rojo, >100%).
- Auto-refresh de página cada 27000 segundos (~7.5 h) si no hay paneles abiertos (constante `REFRESH_SECONDS` en el JS embebido) — la comprobación incluye los 2 paneles de capacidad **y** el panel de navegación móvil (`#navPanel` de `base.html`), para no interrumpir al usuario si el timer cae justo mientras tiene el menú abierto.

**Detalle del día** (`detalle_dia.html`): tabla completa de despachos de una fecha (cliente/sede, placa, conductor, OC, origen, pallets), con indicador de utilización de flota y acceso a "Añadir Carga" si el día es editable (hoy o futuro). La fila de acciones de la cabecera (utilización de flota + "Exportar" + "Añadir Carga") usa el patrón `.action-row`.
- **Barra de totales del día**: "Total de registros" y "Total Pallets" (badge verde), dentro de la tarjeta de la tabla, calculados con `despachos.aggregate(total_registros=Count('id'), total_pallets=Sum('pallets'))` sobre el queryset del día — mismo patrón visual y de agregación ya usado en "Consultar". No es redundante con el indicador "Utilización Flota" ya existente en la cabecera (`{{ conteo_placas }} / {{ capacidad_limite }}`): ese cuenta **placas únicas**, mientras que "Total de registros" cuenta **filas de despacho** — pueden diferir si una misma placa aparece en más de un despacho el mismo día (la validación de "placa duplicada el mismo día" solo existe en `PlanificacionForm.clean()`, no a nivel de modelo, por lo que un registro creado vía Django Admin podría duplicarla sin bloqueo).

**Historial / Búsqueda** (`lista_busqueda.html`): tabla con filtros GET (`cliente`, `placa`, `fecha_desde`, `fecha_hasta`), acciones de editar/eliminar (con modal de confirmación JS) para usuarios de Planeamiento.
- **Paginación**: `Paginator` nativo de Django, `REGISTROS_POR_PAGINA = 10` (constante de módulo en `views.py`), usando `paginator.get_page(request.GET.get('page'))` — maneja de forma segura números de página inválidos o fuera de rango sin necesidad de `try/except` manual. Los enlaces preservan los filtros activos vía un `querystring` construido en la vista con `request.GET.copy()` excluyendo la clave `page` (evita duplicar `?page=N&page=M` en la URL). Controles minimalistas tipo circular (mismo lenguaje visual que la navegación de semanas del Dashboard), ocultos automáticamente cuando `num_pages <= 1`.
  - **4 botones de navegación**: Ir al inicio (`fa-backward-step`, `?page=1&{{ querystring }}`), Anterior (`fa-chevron-left`), Siguiente (`fa-chevron-right`), Ir al final (`fa-forward-step`, `?page={{ page_obj.paginator.num_pages }}&{{ querystring }}`). Los botones de salto extremo reutilizan las mismas condiciones `page_obj.has_previous`/`page_obj.has_next` que ya gobiernan Anterior/Siguiente (equivalentes a "estoy en la página 1" / "estoy en la última página"), sin lógica de comparación adicional. Jerarquía visual: Anterior/Siguiente con fondo `bg-slate-100` (acción primaria); Inicio/Fin sin fondo en reposo (`text-slate-300`, acción secundaria), ambos con hover cromático coherente (`hover:bg-slate-800` a la izquierda, `hover:bg-emerald-600` a la derecha). Íconos de "step" (estilo reproductor) elegidos deliberadamente distintos a los `fa-angle-double-*` que ya usa la navegación de semanas del Dashboard, para no reutilizar el mismo ícono con dos significados distintos (ahí = paso de una semana; aquí habría significado salto al extremo).
- **Barra de totales** (dentro de la tarjeta de la tabla, antes del `<thead>`): "Total de registros" y "Total Pallets" (badge verde), ambos calculados con una sola agregación ORM (`queryset.aggregate(total_registros=Count('id'), total_pallets=Sum('pallets'))`) **sobre el queryset completo ya filtrado, antes de paginar** — no se derivan de los ~10 registros de la página actual.
- **Limpieza de lógica de días**: se eliminó el bucle manual en la vista que anotaba `reg.dia_semana_es` sobre el queryset completo (obligaba a materializar todas las filas en cada request, filtros o no). El template ahora usa directamente la `@property nombre_dia` ya existente en `PlanificacionDespacho` (`models.py`), evitando lógica duplicada entre `views.py` y `models.py`.
- **Filtro de fechas por defecto (mes actual)**: en la primera visita a `/busqueda/` — o al usar el enlace "Limpiar Filtros", que apunta a la misma URL sin querystring — el sistema aplica automáticamente el rango `hoy.replace(day=1)` (primer día del mes actual) hasta `date.today()` (hoy), y los inputs de fecha del formulario se pre-llenan visualmente con ese rango. La detección se hace sobre la **presencia de las claves** `fecha_desde`/`fecha_hasta` en `request.GET`, no sobre su valor: si el usuario envía el formulario con las fechas explícitamente vacías (ej. buscando solo por cliente o placa), esas claves SÍ están presentes (el formulario `method="get"` siempre las incluye) y el sistema respeta el filtro abierto sin fecha límite, sin reinyectar el default. Los valores resueltos (default o explícitos) se pasan al template en un diccionario `filtros` (`cliente`, `placa`, `fecha_desde`, `fecha_hasta`), del cual leen los 4 inputs del formulario — ya no leen directamente de `request.GET`. Nota de diseño: se usa `date.today()` (no `timezone.localdate()`) para mantener consistencia con el resto del archivo (`dashboard()`, `estadisticas_dashboard()`, `PlanificacionDespacho.clean()`), aunque esto hereda la misma limitación preexistente en todo el proyecto: `date.today()` usa la hora del sistema operativo del servidor (UTC en Railway), no la zona horaria de Perú, por lo que entre las 00:00 y 05:00 UTC el "hoy" del servidor puede ir un día adelantado respecto a Lima — una corrección de fondo (`TIME_ZONE = 'America/Lima'` + migrar todo el proyecto a `timezone.localdate()`) queda fuera de este alcance.

**Formulario de Planificación** (`form_planificacion.html`): formulario en dos columnas ("Detalles del Servicio" / "Vehículo y Documentación"), reutilizado para alta y edición, con estilos Tailwind inyectados vía CSS para los widgets de Django.

**Login** (`login.html`): página de acceso con branding Daryza, standalone (no extiende `base.html`).

## 5. Módulo de Estadística (Solo Lectura) — Dashboards Pro

Nuevo módulo de reportes, accesible desde el ítem **"Estadística"** de la barra de navegación (`core/templates/base.html`, junto a "Consultar"), separado intencionalmente del botón "Dashboard" para evitar confusión entre el panel operativo (con escritura) y el panel de reportes (sin escritura).

- **Ruta**: `/estadisticas/` (`planificacion/urls.py`, `name=estadisticas_dashboard`).
- **Vista**: `estadisticas_dashboard()` en `planificacion/views.py`, protegida únicamente con `@login_required` (visible para cualquier usuario autenticado, sin distinción de grupo, ya que no realiza ninguna escritura).
- **Plantilla**: `planificacion/templates/planificacion/estadisticas.html`, hereda de `base.html`.
- **Filtros** (GET, con manejo seguro de valores vacíos/inválidos — degradan a un default en vez de romper la vista): `fecha_desde`, `fecha_hasta` (default: últimos 30 días) y `cliente` (`icontains`, mismo patrón que `lista_busqueda`).
- **Guardrail de rendimiento**: si el rango solicitado supera `MAX_RANGO_ESTADISTICAS_DIAS = 180` días, se trunca automáticamente a los últimos 180 días (con aviso visible en pantalla) para evitar recorridos día a día excesivos sobre rangos arbitrariamente grandes elegidos por el usuario.
- **Chart.js vía CDN** (`chart.umd.min.js`) es la única dependencia nueva del proyecto, consistente con la estrategia existente de dependencias 100% por CDN (Tailwind, Font Awesome). Los datos se pasan del backend al JS de forma segura con el filtro nativo de Django `json_script` (sin exponer un endpoint JSON/API nuevo — no se introdujo DRF).

**Los 5 paneles implementados:**

1. **Volumen y Tendencia de Ocupación Histórica** — gráfico de líneas (Chart.js) día a día: placas únicas operadas (`Count('unidad_placa', distinct=True)` vía ORM) vs. límite dinámico de capacidad (excepción `CapacidadDiaria` o `ConfiguracionCapacidad` global, misma jerarquía que el Dashboard). Incluye contador de "días críticos" (días que igualan o superan su límite) en el rango filtrado.
2. **Matriz de Concentración (Cliente × Día de la Semana)** — tabla cruzada de pallets acumulados por cliente y día (Lunes–Sábado), ordenada por total descendente. El pivote se calcula en Python sobre un único fetch de registros (el ORM no soporta pivotes nativos de forma portable entre backends).
3. **Eficiencia de Carga y Densidad de Pallets** — tarjetas de resumen (Total Pallets, Promedio Pallets/Despacho, Total Despachos) 100% vía agregaciones ORM (`Sum`, `Avg`, `Count`), más gráfico de barras horizontales (Chart.js) con el Top 8 de `carga_origen` por pallets (`values().annotate(Sum).order_by()`).
4. **Auditoría y Frecuencia de Uso de Flota** — tabla de rendimiento por placa: conductor más frecuente (moda calculada con `collections.Counter`, sin equivalente ORM portable directo), días trabajados, servicios totales y clientes atendidos distintos, ordenada por servicios totales.
5. **Histórico de Saturación e Incidencias de Capacidad** — gráfico de dona (Chart.js) con el desglose de `CapacidadDiaria.motivo` (`values('motivo').annotate(Count)`), acompañado de tarjetas con el total de días críticos/sobresaturados y de motivos distintos registrados en el rango.

Todos los paneles comparten el mismo queryset base filtrado (`PlanificacionDespacho` por rango de fechas + cliente), sin introducir modelos, migraciones ni endpoints de escritura nuevos.

## 6. Carga Masiva y Exportación a Excel

Depende de **`openpyxl`** (añadida a `requirements.txt`), paquete 100% Python puro sin dependencias de sistema — instala sin fricción bajo Nixpacks en Railway, a diferencia de librerías con bindings en C (ver la discusión sobre `xmlsec`/`libxmlsec1` documentada en el historial del proyecto). Todo el procesamiento (lectura y escritura de `.xlsx`) ocurre **en memoria** vía `BytesIO`, sin tocar el filesystem — apropiado para el filesystem efímero de Railway.

### Carga Masiva por Excel (`carga_masiva_excel`, `/programar/masivo/`)

- Solo Planeamiento (`@user_passes_test(es_planeamiento)`), enlazada desde "Programar" (`form_planificacion.html`).
- Columnas requeridas, validadas **por nombre** (no por posición fija de columna): `fecha` (AAAAMMDD), `placa`, `conductor`, `cliente`, `sede_destino`, `origen`, `orden_compra`, `pallets`, `costo_transporte` — mapeadas internamente a los campos del modelo (`fecha`→`fecha_despacho`, `placa`→`unidad_placa`, `sede_destino`→`sede`, `origen`→`carga_origen`).
- **Decisión de arquitectura clave**: cada fila se procesa instanciando **`PlanificacionForm`** con los datos parseados y llamando a `form.is_valid()` — reutiliza automáticamente todas las reglas de negocio ya existentes (placa duplicada el mismo día, límites de longitud de campo, etc.) sin duplicar lógica de validación entre el alta manual y la carga masiva. Además, se aplica un chequeo explícito y anterior de "fecha estrictamente de hoy en adelante" con mensaje específico por fila (`fecha X ya pasó...`).
- Parseo de fecha (`_parsear_fecha_aaaammdd`) maneja de forma defensiva que la celda venga como texto, número, o como `date`/`datetime` nativo (si Excel autoformateó la columna) — riesgo real de Excel identificado y cubierto explícitamente.
- Guardarraíles: rechaza archivos que no sean `.xlsx`, valida que las 9 columnas estén presentes antes de procesar cualquier fila, límite de `MAX_FILAS_CARGA_MASIVA = 500` filas por archivo, y descarta silenciosamente filas completamente vacías (comunes al final de una plantilla).
- **Commit parcial, no transaccional**: cada fila válida se guarda individualmente; una fila con error no bloquea las demás. El reporte final muestra filas procesadas, exitosas y fallidas, con el detalle de error de cada fila fallida.
- **Descarga de plantilla** (`descargar_plantilla_excel`, `/programar/masivo/plantilla/`): genera un `.xlsx` en memoria con los 9 encabezados correctos y una fila de ejemplo, para que el usuario no tenga que armar el archivo a mano.
- Las 3 tarjetas de resultado ("Filas Procesadas", "Cargadas OK", "Con Errores") usan `grid-cols-1 sm:grid-cols-3` — apiladas en móvil, 3 columnas desde 640px.

### Exportación a Excel (`exportar_busqueda_excel`, `exportar_detalle_dia_excel`)

- Helper compartido `_generar_excel_despachos()`: cabecera con relleno verde Daryza (`#2BB673`) y texto blanco en negrita, tipos de dato correctos (`fecha_despacho` como fecha nativa de Excel `DD/MM/YYYY`, `pallets` como entero, `costo_transporte` como decimal `#,##0.00`), ancho de columna automático.
- **"Consultar"** (`lista_busqueda.html`): el botón "Exportar a Excel" reutiliza la función `_construir_queryset_busqueda()` — la misma que usa la vista paginada — garantizando que la exportación aplique **exactamente** los mismos filtros (cliente, placa, rango de fechas, incluyendo el default de mes actual) que la búsqueda en pantalla, sobre el queryset **completo** (no solo la página visible).
- **"Detalle de Planificación"** (`detalle_dia.html`): botón "Exportar" junto a "Añadir Carga", descarga los despachos de esa fecha específica. Visible para cualquier usuario autenticado (operación de solo lectura), sin restricción de grupo.

## 7. Comandos clave

### Entorno local
```bash
# Activar entorno virtual (ya existe en /venv)
# Instalar dependencias
pip install -r requirements.txt

# Migraciones
python manage.py makemigrations
python manage.py migrate

# Crear grupos (Planeamiento, Comercial) + superusuario admin
python crear_admin.py

# Levantar servidor de desarrollo
python manage.py runserver
```

### Despliegue (Railway, vía Nixpacks + `Procfile`)
El `Procfile` en la raíz define el comando de arranque `web`:
```
web: python manage.py migrate && python manage.py collectstatic --noinput && python crear_admin.py && gunicorn core.wsgi:application --bind 0.0.0.0:$PORT
```
Es decir, en cada despliegue se ejecutan automáticamente, en orden: migraciones → recolección de estáticos (WhiteNoise) → bootstrap de grupos/admin → arranque de Gunicorn.

No existe `Dockerfile` en el repositorio; el build depende del detector automático de Nixpacks para proyectos Python (`requirements.txt`).

---

## PROPUESTA DE NUEVOS DASHBOARDS Y ESTADÍSTICAS

> **Actualización**: los paneles 2, 3, 4 y 5 de esta propuesta original ya fueron implementados (total o parcialmente) como los 5 Dashboards Pro del nuevo módulo **"Estadística"** — ver Sección 5. Las ideas 1, 7, 8, 9 y 10 permanecen en backlog.

Análisis como consultor de producto sobre qué paneles analíticos adicionales tienen sentido dado el modelo de datos actual (`PlanificacionDespacho`, `CapacidadDiaria`, `ConfiguracionCapacidad`) y la lógica de negocio ya implementada (capacidad, saturación, clientes, unidades). Todas las propuestas son filtrables por **Rango de Fechas** (`fecha_despacho__range`, ya usado en `lista_busqueda`) y por **Cliente** (`cliente__icontains`, ya usado como filtro), reutilizando patrones de query existentes en `views.py`.

1. **Ranking de Clientes por Volumen de Despachos**
   Top-N clientes por cantidad de despachos y de pallets movidos en el rango seleccionado (`GROUP BY cliente`, `Count('id')`, `Sum('pallets')`). Útil para negociación comercial y para detectar concentración de riesgo en pocos clientes. *(Cubierto parcialmente por la columna "Total" de la Matriz de Concentración — Panel 2 de "Estadística" — pero sin un ranking dedicado ni el total de despachos por cliente.)*

2. **Curva de Ocupación de Flota en el Tiempo** — ✅ **Implementado** como Panel 1 ("Volumen y Tendencia de Ocupación Histórica") del módulo "Estadística".
   Extensión directa de la lógica ya existente en `calcular_kpis_dashboard()` (hoy limitada a una semana): gráfico de línea/área con % de ocupación diaria (placas únicas / límite del día) a lo largo de un rango arbitrario de fechas, con anotaciones visuales donde exista una `CapacidadDiaria` (excepción). Permite ver estacionalidad y planificar excepciones con anticipación.

3. **Mapa de Calor Cliente × Día de la Semana** — ✅ **Implementado** como Panel 2 ("Matriz de Concentración") del módulo "Estadística".
   Matriz que cruza `cliente` (o `sede`) contra `nombre_dia` (ya calculado como propiedad del modelo) mostrando volumen de pallets o despachos. Ayuda a detectar patrones ("Cliente X siempre despacha los lunes") útiles para planificar capacidad diaria por adelantado.

4. **Distribución de Pallets por Despacho (Eficiencia de Carga)** — 🟡 **Parcialmente implementado** en el Panel 3 ("Eficiencia de Carga y Densidad de Pallets") del módulo "Estadística".
   Histograma/boxplot de `pallets` por despacho, filtrable por cliente, para identificar clientes con cargas subóptimas (muchos despachos con pocos pallets) que podrían consolidarse — impacto directo en la métrica de "Eficiencia Semanal" ya existente en el dashboard. *El Panel 3 implementado cubre totales/promedio de pallets y el Top de Carga/Origen; el histograma/boxplot de distribución por cliente queda pendiente.*

5. **Ranking de Utilización de Unidades (Placas)** — ✅ **Implementado** como Panel 4 ("Auditoría y Frecuencia de Uso de Flota") del módulo "Estadística".
   Frecuencia de uso de cada `unidad_placa` en el rango de fechas: cuántos días trabajó, para cuántos clientes distintos, y días de inactividad. Complementa el enfoque actual (que solo cuenta placas *únicas por día*, sin historizar el uso de cada una).

6. **Panel de Cumplimiento de Capacidad (Excepciones vs. Límite Global)** — 🟡 **Parcialmente implementado** en el Panel 5 ("Histórico de Saturación e Incidencias de Capacidad") del módulo "Estadística".
   Reporte tabular/gráfico basado en `CapacidadDiaria`: cuántos días del rango tuvieron excepción activa, motivo más frecuente (agrupando el campo `motivo`), y comparación entre el límite personalizado promedio vs. el límite global — mide cuánto se está "improvisando" capacidad fuera de la configuración estándar. *El Panel 5 implementado cubre el desglose de motivos y el conteo de días críticos/sobresaturados; falta el comparativo "límite personalizado promedio vs. límite global".*

7. **Días de Saturación / Sobresaturación Acumulados**
   Extiende el KPI "Alertas" (hoy solo semanal) a un rango configurable: conteo y % de días `CRÍTICO`/`SOBRESATURADO` por cliente o global, con tendencia mes a mes — útil para justificar ante gerencia un aumento del límite global de flota.

8. **Origen de Carga más demandado (`carga_origen`)**
   Ranking de puntos de carga/origen por volumen, filtrable por cliente y fecha — permite decisiones logísticas sobre qué orígenes concentran más operación y podrían necesitar recursos dedicados.

9. **Panel de Actividad por Usuario Registrador (`creado_por`)**
   Aprovechando el campo de auditoría ya existente: cuántos registros crea cada usuario del equipo de Planeamiento en el rango, útil para trazabilidad y carga operativa del equipo (no es un dato de negocio del cliente, sino de gestión interna).

10. **Comparador Semana a Semana / Mes a Mes**
    Vista tipo "este período vs. anterior" para las métricas ya calculadas (eficiencia, unidades totales, alertas), permitiendo ver tendencia de crecimiento/caída en volumen operado — reutiliza `calcular_kpis_dashboard()` corriéndola dos veces sobre rangos consecutivos.

**Nota de implementación**: todos estos paneles pueden construirse con agregaciones nativas de Django ORM (`annotate`, `Count`, `Sum`, `Avg`, `TruncWeek`/`TruncMonth`) sobre `PlanificacionDespacho`, sin necesidad de nuevas tablas ni de un motor de reporting externo — el modelo de datos actual ya contiene todos los campos necesarios (`cliente`, `sede`, `unidad_placa`, `carga_origen`, `pallets`, `fecha_despacho`, `creado_por`). Para gráficos, el proyecto no tiene actualmente ninguna librería de charts instalada (todo el frontend es Tailwind + Font Awesome vía CDN); habría que sumar una librería ligera (p. ej. Chart.js vía CDN, consistente con el patrón actual de dependencias por CDN) o generar los datos y renderizarlos con las mismas técnicas CSS/inline usadas hoy en el mini-gráfico de "Tendencia".
