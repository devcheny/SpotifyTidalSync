# Spotify ↔ TIDAL Sync

Mantiene sincronizados tus favoritos y tus playlists entre Spotify y TIDAL usando
**solo las APIs oficiales** de cada servicio. Sincronización automática cada 24 h
mediante el Programador de tareas de Windows, sincronización manual desde una
interfaz gráfica, e inicio de sesión OAuth 2.0 con PKCE en el navegador.

Además puede **volcar las playlists de TIDAL en iTunes**, emparejándolas con la
música que ya tienes en la biblioteca local de este equipo.

```
┌─ Interfaz (tkinter) ──────────────────────────────────────┐
│  Conectar cuentas · Sincronizar ahora · Programar 24 h    │
└───────────────┬───────────────────────────────────────────┘
                │
        ┌───────▼────────┐        emparejamiento por ISRC
        │  Motor de sync │◄──────────────────────────────────┐
        └───┬────────┬───┘                                   │
            │        │                                       │
   api.spotify.com  openapi.tidal.com/v2          tokens cifrados (DPAPI)
            │
            └──► iTunes (COM, local)   emparejamiento por título + artista
```

---

## 1. Instalación

Requiere **Python 3.10 o superior** ([python.org](https://www.python.org/downloads/),
marca *Add Python to PATH* al instalar).

Doble clic en **`instalar.bat`**. Crea un entorno virtual en `.venv` e instala
`requests`. Es lo único que hace falta.

También intenta instalar `pywin32`, que solo se usa para hablar con iTunes. Si
esa parte falla, el instalador lo avisa y continúa: todo lo demás funciona igual.

## 2. Crear tus apps de desarrollador

El script usa tus propias credenciales: no hay servidor intermedio ni terceros
viendo tu biblioteca. Necesitas un **Client ID** por servicio (gratis, 5 minutos).
No hace falta *client secret*: PKCE está diseñado para apps de escritorio.

### Spotify

1. Entra en [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) → **Create app**.
2. Nombre y descripción: lo que quieras.
3. **Redirect URI**: `http://127.0.0.1:8898/callback` — pégala tal cual y pulsa *Add*.
4. En *Which API/SDKs*: marca **Web API**.
5. Guarda y copia el **Client ID** desde *Settings*.
6. En **User Management**, añade tu propio correo y nombre de usuario de Spotify.
   Las apps nuevas nacen en *Development Mode* y solo funcionan con las cuentas
   que aparezcan en esa lista.

### TIDAL

1. Entra en [developer.tidal.com/dashboard](https://developer.tidal.com/dashboard) → crea una aplicación.
2. **Redirect URI**: `http://127.0.0.1:8899/callback`.
3. Activa los permisos (*scopes*): `user.read`, `collection.read`, `collection.write`,
   `playlists.read`, `playlists.write`.
4. Copia el **Client ID**.

## 3. Poner en marcha

1. Doble clic en **`abrir-interfaz.bat`**.
2. Pestaña **Ajustes** → pega los dos Client ID → **Guardar ajustes**.
3. Pestaña **Sincronización** → **Iniciar sesión** en cada tarjeta. Se abre el
   navegador, autorizas, y la pestaña se cierra sola.
4. **Recomendado la primera vez**: marca *Modo simulación* en Ajustes y pulsa
   **Sincronizar ahora**. No escribe nada; solo te enseña en el registro qué haría.
5. Cuando el resultado te convenza, desmarca la simulación y sincroniza de verdad.
6. Pulsa **Activar** junto a *Automático cada 24 h* para registrar la tarea.

---

## Cómo empareja las canciones

La identidad de una canción es su **ISRC**, el código estándar de la industria que
las dos plataformas exponen en su API. Es lo que hace que *Bohemian Rhapsody
(2011 Remaster)* de un lado encuentre su equivalente exacto en el otro sin
depender del texto del título.

Si una canción no tiene ISRC, se cae a una firma normalizada `artista|título`
(sin acentos, sin `(Remastered)`, sin `feat.`). Y si aun así no hay equivalente
—porque el catálogo de un servicio no incluye esa canción— se anota en
`reports\sin-equivalencia-AAAAMMDD.csv` en lugar de adivinar. **Nunca inventa un
emparejamiento dudoso.**

Las búsquedas fallidas se cachean 30 días para no gastar cuota de API repitiendo
lo que ya se sabe que no está.

## Opciones

| Ajuste | Qué hace |
|---|---|
| **Favoritos** | Sincroniza *Canciones que te gustan* ↔ *Favoritos*. |
| **Playlists propias** | Empareja por nombre (ignorando mayúsculas y acentos) y crea en el destino la que falte. |
| **Dirección** | Bidireccional (unión), solo Spotify→TIDAL, o solo TIDAL→Spotify. |
| **Propagar borrados** | Desactivado por defecto. Si lo activas, quitar una canción en un lado la quita en el otro. La primera ejecución nunca borra: necesita un snapshot previo con el que comparar. |
| **Modo simulación** | Lee todo y muestra el plan, pero no escribe nada en tus cuentas. |
| **País** | Código ISO de 2 letras para el catálogo de TIDAL (`ES`, `MX`, `US`…). |

Los filtros `playlist_include` y `playlist_exclude` se editan en el `config.json`
(botón *Abrir carpeta de datos*).

---

## Volcar las playlists de TIDAL en iTunes

Pestaña **iTunes**. Por cada playlist de TIDAL crea (o actualiza) una playlist en
iTunes llamada `TIDAL - <nombre>` y le añade las canciones **que ya tienes en tu
biblioteca local**. No descarga nada ni convierte streams en ficheros: lo que no
tengas se queda fuera y se apunta en el informe.

**Requisitos:** Windows, **iTunes para Windows de apple.com** (la versión de la
Microsoft Store no se puede automatizar) y `pywin32`. La pestaña te dice en verde
o en rojo si este equipo cumple, sin necesidad de abrir iTunes para comprobarlo.

**Qué playlists se mantienen:** pulsa *Cargar de TIDAL* y marca las que quieras.
Al recargar, las que ya no existan en TIDAL siguen apareciendo marcadas en vez de
desaparecer sin avisar: las quitas tú cuando lo veas. En `config.json` esto es
`itunes_playlists`, y una lista vacía significa *todas*.

| Ajuste | Qué hace |
|---|---|
| **Volcar en iTunes al sincronizar** | Añade el volcado al final de cada sincronización, incluida la tarea programada de las 24 h. Si lo dejas desmarcado, solo se ejecuta cuando lo pides a mano. |
| **Nombre en iTunes** | Prefijo del nombre de la playlist. Por defecto `TIDAL - `, con el espacio final. |
| **Qué playlists** | Pulsa *Cargar de TIDAL* y marca las que quieras mantener. O deja marcado *Todas*, que incluye también las que crees en TIDAL más adelante. |
| **Quitar lo que ya no esté** | Convierte la playlist de iTunes en un espejo: lo que desaparece de TIDAL desaparece de iTunes. Desactivado por defecto (solo añade). |
| **Playlist de faltantes** | Deja en TIDAL una lista `<nombre> - Faltantes en iTunes` con las canciones que no están en tu biblioteca, cómoda para ir comprándolas o descargándolas. |

Cómo empareja: iTunes **no expone el ISRC**, así que aquí el emparejamiento es por
texto. Se comparan título y artista normalizados —sin acentos, sin `(Remastered)`,
sin `feat.`, y con las comas y los puntos y coma tratados igual—, de modo que
*Despechá* de `ROSALÍA` encuentra a *Despecha* de `Rosalia`, y *La Fama* de
`ROSALÍA, The Weeknd` encuentra a la de `ROSALÍA; The Weeknd`. Si varias canciones
comparten título, desempata por duración; si el artista no encaja, **no la añade**.

El *Modo simulación* también vale aquí: enseña qué playlists crearía y cuántas
canciones añadiría sin tocar iTunes.

## Uso desde la línea de comandos

```
python main.py                  interfaz gráfica
python main.py --sync           sincroniza una vez y sale
python main.py --itunes         solo vuelca las playlists de TIDAL en iTunes
python main.py --itunes --playlist "La Caseta"   solo esa playlist
python main.py --status         estado de cuentas, ajustes y tarea programada
python main.py --dry-run --sync simulación
python main.py --schedule 03:00 registra la tarea diaria
python main.py --unschedule     la elimina
```

O los lanzadores: `sincronizar-ahora.bat`, `estado.bat` y
`sincronizar-itunes.bat` (que acepta el nombre de una playlist como argumento).

## Dónde vive todo

`%APPDATA%\SpotifyTidalSync\`

| | |
|---|---|
| `config.json` | Ajustes y Client IDs. |
| `tokens.dat` | Tokens **cifrados con DPAPI**: solo los descifra tu usuario de Windows en este equipo. No están en texto plano ni salen de tu máquina. |
| `state.json` | Snapshots para detectar borrados y caché de equivalencias. |
| `logs\sync.log` | Registro de cada ejecución (rota a los 2 MB). |
| `reports\` | CSV de canciones sin equivalencia. |

Si borras `state.json`, la siguiente sincronización vuelve a partir de cero: es
inocua (solo une), pero perderá la referencia para propagar borrados.

## La tarea programada

Se registra como `SpotifyTidalSync` en el contexto de tu usuario, **sin permisos
de administrador**. Usa `pythonw.exe`, así que no aparece ninguna ventana. Lleva
`StartWhenAvailable`: si el equipo estaba apagado a la hora prevista, se ejecuta
al arrancar. Puedes verla en *Programador de tareas* → *Biblioteca*.

## Copiar el proyecto a otro equipo o a una máquina virtual

Copia todo **menos la carpeta `.venv`**, y ejecuta `instalar.bat` en el destino.
Un entorno virtual guarda la ruta absoluta del Python que lo creó, así que no
funciona en otra máquina. Si lo copias de todos modos, `instalar.bat` lo detecta
y lo rehace solo.

Los tokens tampoco viajan: están cifrados con DPAPI y solo los descifra tu usuario
en el equipo donde iniciaste sesión. En la máquina nueva tendrás que volver a
conectar las dos cuentas, pero los Client ID del `config.json` se pueden reutilizar
tal cual.

## Resolución de problemas

**`did not find executable at '...\python.exe'`** — estás usando un `.venv`
copiado de otro equipo. Borra la carpeta `.venv` y ejecuta `instalar.bat`.

**«No se pudo abrir el puerto 8898»** — algo ocupa ese puerto. Cambia el
*Redirect URI* en Ajustes a otro puerto y actualízalo también en el portal del
servicio: tienen que coincidir carácter a carácter.

**Spotify devuelve `INVALID_CLIENT` o `Invalid redirect URI`** — la URI del portal
no coincide exactamente con la de Ajustes. Ojo con `127.0.0.1` frente a
`localhost`: Spotify exige la IP, no el nombre.

**Spotify devuelve 403 al leer tu biblioteca** — tu cuenta no está en
*User Management* de la app. Añádela.

**TIDAL devuelve 401** — falta algún scope. Revísalos en el portal, cierra sesión
en la app y vuelve a conectar para que el token se emita con los permisos nuevos.

**TIDAL devuelve 404 en los favoritos** — ver la nota de abajo.

**Muchas canciones sin equivalencia** — normal en discos poco comunes o ediciones
regionales: si el catálogo del otro servicio no tiene esa grabación, no hay nada
que enlazar. Comprueba también que el código de país sea el tuyo.

**«Falta el paquete pywin32»** — ejecuta `instalar.bat` otra vez en ese equipo.

**«No se ve iTunes en este equipo»** — o no está instalado, o es la versión de la
Microsoft Store, que no permite automatización. Instala iTunes desde apple.com.

**El volcado a iTunes no encuentra casi nada** — mira el registro: si TIDAL te
devolvió los artistas vacíos (aparece *«TIDAL rechazó include=items.artists»*), el
emparejamiento por texto se queda sin la mitad del dato. También conviene revisar
que en iTunes el campo *Artista* esté relleno, no solo *Artista del álbum*.

**iTunes se abre solo al sincronizar** — es normal: la automatización COM arranca
el programa. Puedes minimizarlo; la tarea programada lo hará igual en segundo
plano si iTunes ya está cerrado.

---

## Nota honesta sobre la API de TIDAL

Los endpoints implementados están tomados de la especificación OpenAPI pública de
TIDAL (`openapi.tidal.com/v2`) y son los actuales:

- `GET/POST/DELETE /userCollectionTracks/me/relationships/items` (favoritos)
- `GET/POST /playlists` y `/playlists/{id}/relationships/items`
- `GET /tracks?filter[isrc]=…` (emparejamiento)
- `GET /users/me`

Dos avisos que conviene conocer antes de empezar:

1. TIDAL fue **abriendo la colección de usuario por fases** —primero álbumes,
   artistas y playlists; las canciones favoritas llegaron después— y hay
   desarrolladores que han reportado `404` en endpoints de colección incluso con
   `collection.read` concedido, según el tier de acceso de su app. Si te pasa,
   las playlists sí funcionan: desmarca *Favoritos* y sincroniza solo playlists.
2. TIDAL **no expone búsqueda libre de texto estable en la v2**, así que en ese
   sentido el emparejamiento depende del ISRC. En la práctica cubre casi todo el
   catálogo comercial, pero una canción sin ISRC no se podrá enlazar hacia TIDAL.

En Spotify no hay ninguna limitación de este tipo: biblioteca, playlists y
búsqueda por ISRC funcionan con normalidad.

## Límites de las APIs

El cliente HTTP reintenta con *backoff* exponencial y respeta la cabecera
`Retry-After` en los `429`. Las escrituras van por lotes según lo que admite cada
API: 50 favoritos y 100 pistas de playlist en Spotify, 20 elementos en TIDAL.
