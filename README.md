# Spotify ↔ TIDAL Sync

Mantiene sincronizados tus favoritos y tus playlists entre Spotify y TIDAL usando
**solo las APIs oficiales** de cada servicio. Sincronización automática cada 24 h
mediante el Programador de tareas de Windows, sincronización manual desde una
interfaz gráfica, e inicio de sesión OAuth 2.0 con PKCE en el navegador.

Además puede **volcar las playlists de TIDAL en iTunes**, emparejándolas con la
música que ya tienes en la biblioteca local de este equipo, y **convertir a ALAC
los FLAC** que iTunes no sabe leer.

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

Los filtros `playlist_include` y `playlist_exclude` se editan en el `config.json`.
El botón *Abrir carpeta de datos* lista los ficheros de la aplicación y deja verlos
o sacar una copia sin salir de la ventana.

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
`ROSALÍA, The Weeknd` encuentra a la de `ROSALÍA; The Weeknd`.

La misma canción aparece escrita de muchas maneras, así que cada una se busca
también por estas variantes de su título:

| En iTunes o en TIDAL | También se busca como |
|---|---|
| `Hay Que Venir Al Sur (A Far L'Amore…)` | `Hay Que Venir Al Sur` |
| `Hay que venir al sur - Remasterizado 2016` | `Hay que venir al sur` |
| `01 Hay Que Venir Al Sur` | `Hay Que Venir Al Sur` |

Del guion solo se corta si lo que sigue es una coletilla de edición (*remaster*,
*version*, *live*, *mix*, *radio*…), y el número de pista solo si lo que queda
tiene varias palabras: así *99 Luftballons* o *7 Rings* se quedan como están.

Con los **recopilatorios**, donde el artista suele ser `Varios Artistas` o estar
vacío, no hay artista con el que comparar: en ese caso se acepta la canción si es
la única con ese título, o si la duración cuadra con la de TIDAL. Fuera de esos
casos, **si el artista no encaja no la añade**: prefiere dejarla en el informe
antes que meterte una versión que no es.

Los acentos no son problema: `Carrà`, `Carra` y hasta un `CarrÃ ` mal codificado
se comparan igual. Lo que sí rompe es que el acento se haya **perdido** al
etiquetar y en iTunes ponga `Carr?` o `Carr�`; ahí el nombre queda cortado y solo
se puede comparar por cómo empieza, cosa que se hace únicamente cuando no hay
ninguna otra candidata posible.

**Erratas en el título.** Un `Hay Quel Venir al Sur` en TIDAL frente a un
`Hay Que Venir al Sur` en iTunes dejaba la canción fuera para siempre. Como último
recurso se admite un título casi igual, pero con el cinturón muy corto: solo entre
canciones **del mismo artista**, con títulos de al menos 10 caracteres, con una
única candidata parecida y **si los números coinciden** (`Parte 1` y `Parte 2` no
son la misma canción por mucho que se parezcan).

### Por qué no casa una canción concreta

```
buscar-cancion.bat "hay que venir"
```

o `python main.py --buscar "hay que venir"`. Enseña esa canción tal y como la ve
el programa en los dos lados —el texto en crudo, para que se vean los acentos
perdidos y los espacios raros, y la forma normalizada con la que de verdad se
compara— y termina diciendo si casan y por qué no:

```
== En iTunes, titulos que contienen 'hay que venir' ==
  'Hay Que Venir Al Sur'
      artista : 'Raffaella Carr?'
      compara : 'hay que venir al sur' | 'raffaella carr'
      OJO: hay un '?' o un rombo donde deberia ir un acento

== En tus playlists de TIDAL ==
  [Animacion Old] 'Hay que venir al Sur' de 'Raffaella Carrà'
      compara : 'hay que venir al sur' | 'raffaella carra'
      CASA con 'Hay Que Venir Al Sur' de 'Raffaella Carr?'
```

**Qué te falta:** el botón *Ver que falta en iTunes* abre el informe del día en
una tabla dentro de la propia aplicación, con un botón para copiar lo que marques
y otro para abrirlo en Excel. Tres columnas:

| destino | cancion | motivo |
|---|---|---|
| `itunes / Animacion Old` | `Raffaella Carrà - Hay que venir al sur` | `en iTunes ese titulo esta a nombre de: Georgie Dann` |
| `itunes / Rock` | `Queen - Innuendo` | `no esta en la biblioteca` |
| `tidal` | `Extremoduro - So Payaso` | `sin equivalencia en el catalogo` |

El **motivo** distingue lo que de verdad no tienes de lo que sí tienes pero con
otra etiqueta:

| Motivo | Qué significa |
|---|---|
| `en iTunes ese titulo esta a nombre de: …` | La tienes, pero con un artista que no cuadra con el de TIDAL. |
| `con ese titulo no, pero de ese artista tienes: 'Hay Que Venir al Sur'` | El título no coincide. Compáralo con el de la columna de al lado: ahí se ven las erratas. |
| `no esta en la biblioteca` | Ni el título ni el artista aparecen. Esta sí te falta de verdad. |

En los dos primeros casos, corrigiendo la etiqueta en iTunes se empareja sola en la
siguiente pasada.

El mismo botón está como *Ver informe* en la pestaña Sincronización, donde además
salen las canciones que no tienen equivalencia entre Spotify y TIDAL.

El *Modo simulación* también vale aquí: enseña qué playlists crearía y cuántas
canciones añadiría sin tocar iTunes.

---

## Convertir FLAC a ALAC

Pestaña **FLAC a ALAC**. iTunes no sabe leer FLAC: lo que dejas en la carpeta de
auto-añadir acaba arrinconado en su subcarpeta `No añadido\<fecha>\`. Esto recorre
esa carpeta entera, convierte cada FLAC a ALAC con **ffmpeg** y deja el `.m4a` en
la raíz, que es donde iTunes sí lo recoge solo.

Necesita ffmpeg: `winget install Gyan.FFmpeg`, o indica la ruta a `ffmpeg.exe` en
la pestaña. Arriba te dice en verde o en rojo si está listo.

| Ajuste | Qué hace |
|---|---|
| **Carpeta** | Dónde buscar los FLAC y dónde dejar los ALAC. Por defecto `C:\Music\iTunes\iTunes Media\Añadir automáticamente a iTunes`. Busca en subcarpetas; los `.m4a` van siempre a la raíz. |
| **Convertir al terminar la sincronización** | Igual que la opción de iTunes: se encadena como **último paso de la cola**, después del volcado de playlists, y entra en la tarea diaria de las 24 h sin registrar nada nuevo. |
| **Calidad CD** | Deja el ALAC en 16 bits y 44,1 kHz, los **1411 kbps** de un CD. Sin esto, un FLAC de 24 bits y 192 kHz sale a **9216 kbps** y ocupa unas seis veces más, porque ALAC no pierde información: se lleva la resolución del original tal cual. Activado por defecto. |
| **Normalizar el volumen** | `loudnorm=I=-9:TP=-1.5:LRA=11`, lo mismo que hacía el `flac2alac.bat` de siempre. |
| **Conservar la carátula** | Copia la portada al `.m4a`. Si ffmpeg la rechaza, reintenta sin ella en vez de dar el fichero por perdido. |
| **Borrar el FLAC** | Al convertirlo bien. Si lo desmarcas, el original se mueve a `_convertidos\` en vez de borrarse. En ambos casos deja de reconvertirse la próxima vez. |

Viene del `flac2alac.bat` de siempre y cambia en tres cosas, todas para no perder
nada:

- Mira **cómo terminó ffmpeg**, no si el fichero destino existe. iTunes se lleva
  el `.m4a` en cuanto aparece, así que la comprobación del `.bat` podía fallar y
  dejar el FLAC sin recoger.
- **No machaca** un `.m4a` que ya estuviera ahí: el segundo pasa a ser `nombre (2).m4a`.
  El `.bat` usaba `ffmpeg -y` y lo sobrescribía.
- Si algo falla, el FLAC **no se borra** y el `.m4a` a medias se limpia.

Al terminar borra las carpetas vacías que quedan dentro (`No añadido\<fecha>\`).
Con *Modo simulación* te dice qué convertiría sin tocar nada.

### Que lo revise solo una vez al día

Dos formas, y puedes usar la que prefieras:

1. **Encadenado a la sincronización** — marca *Convertir al terminar la
   sincronización*. Va detrás del volcado a iTunes, en la misma ejecución diaria.
   Es lo mismo que hace la opción de iTunes: no crea ninguna tarea nueva.
2. **Por su cuenta** — pulsa *Activar* junto a la hora (por defecto las **04:00**,
   una hora después de la sincronización). Registra en Windows una segunda tarea,
   `SpotifyTidalSync - FLAC a ALAC`, independiente de la otra: puedes tener una,
   la otra o las dos. Al lado te enseña la última y la próxima ejecución.

Si lo único que quieres es el repaso de FLAC, sin sincronizar Spotify ni TIDAL,
usa la segunda: no necesita cuentas conectadas.

Desde la línea de comandos: `python main.py --flac2alac`, el lanzador
`convertir-flac.bat`, o `python main.py --schedule-flac 04:00` /
`--unschedule-flac` para la tarea diaria.

## Uso desde la línea de comandos

```
python main.py                  interfaz gráfica
python main.py --sync           sincroniza una vez y sale
python main.py --itunes         solo vuelca las playlists de TIDAL en iTunes
python main.py --itunes --playlist "La Caseta"   solo esa playlist
python main.py --flac2alac      convierte los FLAC de la carpeta de iTunes a ALAC
python main.py --buscar "hay que venir"   por que esa cancion no casa con iTunes
python main.py --version        version instalada y si hay una mas nueva
python main.py --actualizar     instala la ultima release de GitHub
python main.py --status         estado de cuentas, ajustes y tarea programada
python main.py --dry-run --sync simulación
python main.py --schedule 03:00 registra la tarea diaria
python main.py --unschedule     la elimina
```

O los lanzadores: `sincronizar-ahora.bat`, `estado.bat`, `convertir-flac.bat`,
`buscar-cancion.bat`, `actualizar.bat` y `sincronizar-itunes.bat` (estos dos aceptan el texto o el
nombre de la playlist como argumento; si no se lo das, te lo preguntan).

## Dónde vive todo

`%APPDATA%\SpotifyTidalSync\`

| | |
|---|---|
| `config.json` | Ajustes y Client IDs. |
| `tokens.dat` | Tokens **cifrados con DPAPI**: solo los descifra tu usuario de Windows en este equipo. No están en texto plano ni salen de tu máquina. |
| `state.json` | Snapshots para detectar borrados y caché de equivalencias. |
| `logs\sync.log` | Registro de cada ejecución (rota a los 2 MB). |
| `reports\` | CSV de canciones sin equivalencia, uno por día. El botón **Ver informe** muestra el más reciente en una tabla dentro de la aplicación. |

Si borras `state.json`, la siguiente sincronización vuelve a partir de cero: es
inocua (solo une), pero perderá la referencia para propagar borrados.

## Las tareas programadas

Son dos, independientes entre sí:

| Tarea | Qué ejecuta | Se activa en |
|---|---|---|
| `SpotifyTidalSync` | `main.py --sync` (con iTunes y la conversión de FLAC detrás, si los has marcado) | Pestaña *Sincronización* |
| `SpotifyTidalSync - FLAC a ALAC` | `main.py --flac2alac` | Pestaña *FLAC a ALAC* |

Se registran en el contexto de tu usuario, **sin permisos de administrador**. Usan
`pythonw.exe`, así que no aparece ninguna ventana. Llevan `StartWhenAvailable`: si
el equipo estaba apagado a la hora prevista, se ejecutan al arrancar. Puedes verlas
en *Programador de tareas* → *Biblioteca*, y `estado.bat` te dice de las dos cuándo
corrieron por última vez y cuándo toca la siguiente.

## Repartirla entre amigos, con actualizaciones desde GitHub

Cada uno instala **Python** una vez; a partir de ahí la aplicación se actualiza
sola desde las *releases* de tu repositorio. No hace falta git ni empaquetar nada.

### Lo que hacen ellos, una vez

1. Instalan Python desde [python.org](https://www.python.org/downloads/), marcando
   *Add Python to PATH*.
2. Descargan el proyecto desde
   [github.com/devcheny/SpotifyTidalSync](https://github.com/devcheny/SpotifyTidalSync)
   (botón verde *Code* → *Download ZIP*) y lo descomprimen donde quieran.
3. Doble clic en `instalar.bat`.

Ya está: el proyecto viene apuntando a ese repositorio, así que no hay nada que
configurar. Cada vez que abran la aplicación les avisará si has publicado algo
nuevo, y con el botón **Instalar** se pone al día sola. También vale
`actualizar.bat`.

Si alguien quiere apuntar a otro sitio (un *fork* suyo, por ejemplo), lo cambia en
**Ajustes** → *Actualizaciones*.

### Lo que haces tú para publicar una versión

1. Subes el número en `stsync/__init__.py`:
   ```python
   __version__ = "1.1.0"
   ```
2. Commit y `git push`.
3. En GitHub, *Releases* → *Draft a new release* → etiqueta **`v1.1.0`** (la `v`
   es opcional) → *Publish release*.

Eso es todo: la aplicación compara su versión con la etiqueta de la última release
y se descarga su ZIP. Si publicas una release con una etiqueta **menor o igual** a
la instalada, nadie se actualiza — así puedes retirar una versión mala publicando
otra con número mayor.

### Qué se conserva y qué no

Al actualizar se reemplazan los ficheros del programa. **No se toca**:

- La carpeta `.venv`.
- Nada de `%APPDATA%\SpotifyTidalSync`: cuentas, ajustes, informes y registros.

Si la descarga se corta, el ZIP viene mal o el `main.py` que trae no compila, **no
se escribe nada** y sigues con la versión que tenías. Tras copiar, se revisa
`requirements.txt` por si la versión nueva necesita algún paquete más.

### Antes de repartirla: los Client ID

Los de Spotify y TIDAL son **tuyos**. Si van dentro de la aplicación, tus amigos
gastan tu cuota, y en Spotify tendrías que dar de alta a cada uno en *User
Management* (las apps en modo desarrollo solo funcionan con las cuentas de esa
lista, y caben 25). Lo limpio es que **cada uno cree las suyas**, que es gratis y
son cinco minutos: ver *Crear tus apps de desarrollador*, más arriba.

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

**«Ubicación no disponible» al pulsar *Abrir carpeta de datos*, pero `estado.bat`
dice que la carpeta es correcta** — las dos cosas pueden ser ciertas a la vez. La
ruta sale de `%APPDATA%`, que cambia según la cuenta de Windows: si la aplicación
corre con una cuenta (por ejemplo elevada como administrador) y el Explorador con
otra, Python escribe ahí sin problema pero el Explorador no puede entrar.

Compara las dos primeras líneas de `estado.bat`:

```
Usuario          : mi-usuario (como administrador)
Carpeta de datos : C:\Users\mi-usuario\AppData\Roaming\SpotifyTidalSync
                   correcta, 1 informe
```

Si ese usuario no es con el que has iniciado sesión, **el Explorador no va a poder
abrir esa carpeta y no hay nada que la aplicación pueda hacer al respecto**. Por eso
los dos botones enseñan el contenido por su cuenta:

- **Ver informe** abre el CSV en una tabla dentro de la aplicación.
- **Abrir carpeta de datos** lista los ficheros (configuración, registros,
  informes) con su tamaño y su fecha. Desde ahí puedes **Ver** cualquiera —los CSV
  como tabla, los registros como texto— o **Guardar copia…** para sacarlo a tu
  Escritorio o a Documentos, donde sí llegas con el Explorador.

`tokens.dat` aparece en la lista pero no se puede leer: está cifrado con DPAPI.

**No aparece ningún informe** — es lo normal hasta que una sincronización deje
alguna canción sin equivalencia; solo entonces se crea `reports\`. `estado.bat`
te dice cuántos informes hay.

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
