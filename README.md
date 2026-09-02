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

### Que sea la misma grabación, no solo el mismo título

El audio **no se puede comparar**: de Spotify y de TIDAL solo llegan datos, nunca
el fichero, así que no hay forma de contrastar la onda. Una huella acústica
(Chromaprint y compañía) tampoco sirve aquí, porque hace falta el audio de los dos
lados y del suyo no dispones.

Lo que sí identifica una grabación concreta es el **ISRC**, y donde lo hay manda
él. El problema son las que no lo traen: ahí el emparejamiento es por texto, y
*Bohemian Rhapsody* puede ser el disco, el directo de Wembley o una versión de un
grupo de tributo. Por eso, cuando no hay ISRC que compare, se exige además que
**duren más o menos lo mismo**:

| | |
|---|---|
| Mismo ISRC en los dos lados | Vale, sin mirar nada más |
| Sin ISRC, y la duración cuadra | Vale |
| Sin ISRC, y se van más del margen | **Se descarta**, y el informe dice cuánto dura cada una |
| Alguna de las dos no da su duración | Vale: sin dato no se castiga |

El margen son **7 segundos** por defecto, en *Ajustes*. Un remaster se queda en uno
o dos; un directo, un *radio edit* o una versión extendida se van mucho más. Si
alguna vez te deja fuera algo que sí tenías, súbelo o desmarca la casilla: la
comprobación entera se puede apagar.

Esto vale para los tres emparejamientos —Spotify ↔ TIDAL, TIDAL → iTunes y el de
la pestaña *Publicar*—, y lo descartado aparece en el informe con el motivo
completo, del estilo `la tuya (Eagles - Hotel California) dura 6:31 y esta 7:12:
parece otra version`.

De paso, cuando en tu biblioteca hay **dos versiones de la misma canción del mismo
artista**, ahora se queda con la que dura lo mismo en vez de con la primera que
encuentre.

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

### Qué se hace después de sincronizar

Recuadro **Después de sincronizar**, en la pestaña *Sincronización*. Viene plegado
y el título dice cuántos pasos hay puestos; se abre con el `+`. La lista tiene su
propio scroll y una altura fija, así que abrirla no deja el registro sin sitio.

Primero van siempre los favoritos y las playlists entre Spotify y TIDAL. Detrás se
encadena lo que marques, **en este orden**, que no es casual: primero lo que trae
canciones nuevas, luego lo que las convierte, luego lo que las arregla y al final
lo que le cuenta a iTunes que han cambiado.

| # | Paso | Qué hace |
|---|---|---|
| 1 | **Volcar las playlists de TIDAL en iTunes** | Una lista de iTunes por cada una de TIDAL, con lo que ya tengas. |
| 2 | **Publicar tus listas de iTunes** | El camino contrario: sube a Spotify (y a TIDAL) las que tengas marcadas. |
| 3 | **Convertir a ALAC lo que haya llegado** | Los FLAC y WAV de la carpeta de auto-añadir. |
| 4 | **Revisar y arreglar los ficheros** | Los que pasan del techo de calidad y los que tienen saltos en la línea de tiempo. Recorre la biblioteca. |
| 5 | **Arreglar las carátulas** | Pasa a JPEG las portadas que un `.m4a` no admite. Recorre la biblioteca. |
| 6 | **Completar datos desde TIDAL** | Rellena artista, álbum y año buscando cada canción en TIDAL. Gasta cuota. |
| 7 | **Completar los artistas por ISRC** | Las que figuran a nombre de uno solo y son de varios. Gasta cuota. |
| 8 | **Repasar toda la biblioteca (volumen)** | Deja todo al mismo volumen. **Lo más lento**: la primera vez son horas. |
| 9 | **Releer los datos en iTunes** | Que iTunes se entere de lo que ha cambiado. Va el último por eso. |

Todo empieza desmarcado: lo que toca ficheros no se enciende solo. **La tarea
automática de cada 24 h ejecuta exactamente esta misma cola**, no hay una lista
aparte que se pueda quedar desfasada — y `estado.bat` te la enseña marcada tal
como está.

Si un paso falla, los demás siguen y el fallo sale en el resumen: que iTunes esté
cerrado no puede dejar los FLAC sin convertir.

Los pasos 1, 2 y 3 salen también en su propia pestaña, con la misma casilla:
marcarla en un sitio la marca en el otro.

---

## Volcar las playlists de TIDAL en iTunes

Pestaña **iTunes › Traer de TIDAL**. Todo lo que toca iTunes vive bajo la misma
pestaña, con las suyas dentro: *Traer de TIDAL*, *Publicar*, *Convertir a ALAC*,
*Repasar la biblioteca* y *Una sola canción*. Las tres primeras trabajan con
playlists o con lo que entra nuevo; la cuarta recorre las 7000 y la última toca
un fichero y nada más.

Por cada playlist de TIDAL crea (o actualiza) una playlist en
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
| **Volcar en iTunes al sincronizar** | Lo mete en la cola de cada sincronización, incluida la tarea de las 24 h. Es el paso 1 de *Después de sincronizar*, y la casilla es la misma. Desmarcado, solo se ejecuta cuando lo pides a mano. |
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

## Publicar tus listas de iTunes (y traerlas de vuelta)

Pestaña **iTunes › Publicar**, que es el camino contrario al de arriba: parte de una lista
tuya de iTunes y crea la misma en Spotify con el nombre `iTunes - <nombre>`,
buscando allí cada canción por título y artista.

Pulsa *Cargar de iTunes* y marca, para cada lista, en qué sentido va:

| Columna | Qué hace |
|---|---|
| **llevar** | iTunes → Spotify. Lo que tengas en la lista local aparece en la de Spotify. Es el único sentido posible para las listas inteligentes y para TIDAL. |
| **traer** | Spotify → iTunes. Lo que hayas añadido en la lista de Spotify **y ya tengas en tu biblioteca** entra en la lista de iTunes. No descarga nada. |
| **publica** | Deja la lista de Spotify visible para cualquiera. Por defecto ninguna lo es, y antes de crear una pública te lo pregunta. |

Marcando **las dos primeras** la lista se mantiene igual en los dos sitios: es la
sincronización bidireccional. Las **listas inteligentes** aparecen marcadas como
tales y con la casilla *traer* apagada: iTunes no deja añadirles canciones, las
llena él con sus propias reglas.

**A TIDAL se llega por Spotify.** La API v2 de TIDAL no busca por texto, así que
allí solo se puede enlazar una canción por su ISRC, que iTunes no da (se saca de
las etiquetas del fichero con ffprobe, y muchos no lo traen). Por eso Spotify
viene marcado y TIDAL no: lo normal es publicar en Spotify y dejar que la
sincronización de siempre lo lleve a TIDAL. Traer solo funciona desde Spotify.

**La lista de lo que te falta.** Al traer, las canciones de Spotify que no estén
en tu biblioteca se quedan apuntadas en una lista aparte de Spotify llamada
`iTunes - <nombre> - Faltantes en iTunes`, cómoda para ir consiguiéndolas. Se
mantiene al día en los dos sentidos: en cuanto una aparece en tu biblioteca sale
de ahí sola, así que siempre es lo que te falta **ahora** y no un histórico.
Nunca es pública, y **no se copia a TIDAL**: la sincronización se salta por el
nombre cualquier lista que acabe en `- Faltantes en iTunes`, en los dos lados.

El botón *Probar* hace la pasada entera sin escribir nada, ni en las cuentas ni
en iTunes.

---

## Convertir a ALAC

Pestaña **iTunes › Convertir a ALAC**. iTunes no sabe leer FLAC: lo que dejas en la carpeta de
auto-añadir acaba arrinconado en su subcarpeta `No añadido\<fecha>\`. Esto recorre
esa carpeta entera y convierte a ALAC con **ffmpeg** todo lo que sea sin pérdida
—FLAC, WAV, AIFF, APE, WavPack—, dejando el `.m4a` en la raíz, que es donde
iTunes sí lo recoge solo. Un WAV iTunes sí lo lee, pero ocupa el triple y apenas
admite etiquetas.

**Los MP3 y demás formatos con pérdida no se tocan**: pasarlos a ALAC no les
devolvería la calidad que ya perdieron, solo ocuparían el triple.

Necesita ffmpeg: `winget install Gyan.FFmpeg`, o indica la ruta a `ffmpeg.exe` en
la pestaña. Arriba te dice en verde o en rojo si está listo.

| Ajuste | Qué hace |
|---|---|
| **Carpeta** | Dónde buscar y dónde dejar los ALAC. Por defecto `C:\Music\iTunes\iTunes Media\Añadir automáticamente a iTunes`. Busca en subcarpetas; los `.m4a` van siempre a la raíz. |
| **Convertir al terminar la sincronización** | Lo mete en la cola de cada sincronización, y en la tarea diaria de las 24 h, sin registrar nada nuevo. Es el paso 3 de *Después de sincronizar*, y la casilla es la misma. |
| **Calidad CD** | Deja el ALAC en 16 bits y 44,1 kHz, los **1411 kbps** de un CD. Sin esto, un FLAC de 24 bits y 192 kHz sale a **9216 kbps** y ocupa unas seis veces más, porque ALAC no pierde información: se lleva la resolución del original tal cual. Activado por defecto. |
| **Normalizar el volumen** | `loudnorm=I=-9:TP=-1.5:LRA=11`, lo mismo que hacía el `flac2alac.bat` de siempre. |
| **Completar lo que falte** | Un FLAC sin etiquetas entra en iTunes como *Artista desconocido* y ya no hay quien lo empareje. Como esos ficheros suelen llamarse `Artista - Titulo.flac`, de ahí salen el artista, el título y el número de pista **que el fichero no traiga**. Lo que ya trae manda siempre. |
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

### Repasar toda la biblioteca

Pestaña **iTunes › Repasar la biblioteca**, primer recuadro. Es el trabajo que hacía
`NormalizeLibrary.ps1`, pero **sin duplicar la biblioteca en otra carpeta** y
midiendo antes de tocar: cada canción se analiza y solo se reescribe si su
volumen se sale del margen (−9,5 a −8,5 LUFS) o si está grabada por encima del
techo de calidad. Lo que ya está bien no se toca.

Para forzar que repase **todo otra vez**, incluso lo que ya hizo, marca *Repasar
TODO otra vez* en ese mismo recuadro. Normalmente no hace falta: si cambias los
ajustes, la huella cambia y se repasa entero solo.

Se hace **una vez y se olvida**: lo que entre después ya sale normalizado del
conversor. Con una biblioteca grande tarda un buen rato, porque mide canción por
canción.

Cómo se protege lo tuyo, que aquí se reescriben ficheros de verdad:

- Los **formatos con pérdida** (MP3, AAC, WMA) se saltan por defecto: volver a
  comprimirlos los degrada. Hay una casilla para incluirlos si te da igual.
- Cada canción se convierte **a un temporal** y solo entonces se sustituye la
  original, así que un fallo a medias no deja nada destrozado.
- Los **WAV y FLAC que ya estén en la biblioteca** se pasan a ALAC: mismo sonido,
  la mitad de espacio. Como eso cambia la extensión, se le dice a iTunes dónde
  está ahora la canción y el original no se borra hasta que lo ha aceptado; así
  no te quedan canciones rotas con la exclamación.
- Lo que no sea un fichero local (nube, CD) ni se mira.
- El botón **Probar** de al lado lo mide todo y te dice qué haría, sin escribir.

Al terminar cada canción se le dice a iTunes que **relea el fichero**, así que los
kbps y el tamaño se actualizan solos: no hace falta analizar la biblioteca a mano.
Si alguna no se deja (porque iTunes la esté reproduciendo, por ejemplo), sale en el
registro y en el resumen como *sin releer en iTunes*; esas se arreglan
seleccionándolas y usando *Archivo → Biblioteca → Obtener información*.

También `python main.py --biblioteca`.

### Arreglar carátulas y refrescar iTunes

Dos recuadros más de *Repasar la biblioteca*, para dos problemas que salen justo
después de convertir y que no son el mismo aunque lo parezcan.

**Repasar carátulas.** Un FLAC suele traer la portada en **PNG**, y dentro de un
`.m4a` eso está fuera de norma: MP4 espera JPEG. iTunes la enseña igual, pero
otros programas no perdonan — **rekordbox se cierra sin decir nada** al cargar
esa canción. El botón recorre la biblioteca, busca los `.m4a` cuya portada no sea
JPEG y los reescribe **copiando el audio tal cual** (`-c:a copy`): no se
normaliza, no se recodifica y no se pierde ni un bit, así que son segundos por
canción y nada que ver con el repaso de arriba. Los FLAC y los MP3 con portada
PNG están en su derecho y no se tocan.

Pulsa **Probar** primero: te dice cuántas hay y con qué formato viene la portada
de cada una, sin escribir nada. Y si aun convertida a JPEG sigue dando guerra,
está la casilla para **quitar la portada** en vez de convertirla.

Desde esta versión el conversor y el repaso ya dejan la portada en JPEG por su
cuenta, así que esto es solo para lo convertido antes.

**Revisar y arreglar ficheros.** El arreglo del crash de rekordbox, y merece la
pena entender por qué. En un MP4 la frecuencia de muestreo va en la cabecera como un
número de 16.16 bits: **la parte entera son 16 bits, o sea hasta 65535 Hz**. Una
canción de 192 kHz no cabe, así que ffmpeg deja ese campo **a cero** y apunta la
frecuencia buena en la cookie del códec. iTunes y ffprobe leen la cookie y no ven
nada raro; un programa que se fíe de la cabecera se encuentra un 0 y no todos lo
sobreviven — rekordbox se cierra al analizar la canción, sin mensaje.

```
24 bits / 192 kHz  ->  frecuencia en la cabecera: 0 Hz      <- rekordbox muere
16 bits / 44,1 kHz ->  frecuencia en la cabecera: 44100 Hz  <- bien
```

El botón recorre la biblioteca y reescribe lo que tenga algo mal, por dos
motivos:

| Qué encuentra | Por qué está mal |
|---|---|
| **Por encima del techo** | No puede declarar su frecuencia en la cabecera, y ocupa cinco veces más. |
| **Con saltos en la línea de tiempo** | Su tabla `stts` declara fotogramas imposibles, y ahí es donde rekordbox se cierra. |

**No se pierde nada al repararlos**: ALAC, FLAC y WAV son formatos sin pérdida,
así que descodificar y volver a codificar devuelve exactamente las mismas
muestras — solo cambia el encuadre, que es justo lo que estaba mal. Y **no mide
el volumen**, que es lo que tarda en el repaso completo, así que va rápido; el
volumen se queda exactamente como estuviera.

Con *Probar* te dice cuántos hay de cada clase sin tocar nada.

### Hasta qué calidad se graba

En *iTunes › Convertir a ALAC*, y vale para el conversor y para las pasadas de
*Repasar la biblioteca*. Es un **techo, no un objetivo**: lo que ya venga por debajo se
queda como está, porque subirlo no añadiría nada que no estuviera ya y solo
ocuparía más.

| Techo | Ocupa | Cuándo |
|---|---|---|
| **24 bits / 48 kHz** | 2304 kbps | Por defecto. El equilibrio, y lo más alto que un `.m4a` puede declarar en su cabecera. |
| **16 bits / 44,1 kHz** | 1411 kbps | Calidad CD, lo que menos ocupa. |

Con el techo en 24/48, esto es lo que le pasa a cada FLAC al convertirlo:

| FLAC de origen | ALAC que sale |
|---|---|
| 16 bits / 44,1 kHz | **igual**, no se toca |
| 24 bits / 44,1 kHz | **igual**, no se toca |
| 24 bits / 48 kHz | **igual**, no se toca |
| 24 bits / 96 kHz | 24 bits / 48 kHz |
| 24 bits / 192 kHz | 24 bits / 48 kHz |

Los bits solo bajan si eliges calidad CD; con el techo alto se conservan siempre.
Y nunca se sube nada: un 16/44,1 no se convierte en 24/48, porque eso no añadiría
nada que no estuviera ya y ocuparía el doble.

No hay una tercera opción de «no bajar nada», y es a propósito: por encima de 48
kHz el fichero no puede declarar su frecuencia, y eso no es una preferencia sino
un fichero roto. Aunque elijas el techo alto, un `.m4a` nunca se escribe por
encima de 48 kHz.

**La frecuencia de salida se fija siempre**, aunque no haya nada que bajar. No es
manía: el filtro `loudnorm` de ffmpeg trabaja por dentro a **192 kHz y saca a esa
frecuencia lo que le entre**. Sin fijar la salida, un FLAC de 44,1 kHz acaba
siendo un ALAC de 192 kHz —que la cabecera de un `.m4a` no puede declarar— y ese
fichero cierra rekordbox al analizarlo. Fue exactamente lo que estropeó las
canciones: no venían de origen en alta resolución, **las subió el normalizador**.

Si vienes de una versión anterior, la casilla de *calidad CD* se convierte sola:
marcada pasa a «16/44,1», y desmarcada a «24/48».

### iTunes no ve la canción hasta que está terminada

La carpeta de auto-añadir la vigila iTunes: **en cuanto ve aparecer un `.m4a` se
lo lleva a la biblioteca**. Si se escribe ahí directamente, iTunes puede
llevárselo a medio hacer, y para cuando se le van a poner las etiquetas o a
comprobar cómo ha salido, el fichero ya no está o está bloqueado
(`Permission denied`).

Cambiarle la extensión no basta —iTunes lo toca igual—, así que **la conversión
se hace fuera de esa carpeta**, en una carpeta de trabajo temporal. Allí se
convierte, se comprueba y se etiqueta, y solo el fichero terminado se trae a la
carpeta de auto-añadir con un cambio de nombre, que es instantáneo: iTunes lo ve
entero o no lo ve.

Si la carpeta de trabajo cae en otra unidad hay que copiar en vez de renombrar;
entonces se copia primero a `canción.m4a.tmp`, que iTunes no mira, y se renombra
al final.

### Las etiquetas no se pierden en silencio

`-map_metadata` copia al `.m4a` las etiquetas que ffmpeg sabe traducir a MP4 y
**tira el resto sin decir nada**. En un FLAC de tienda eso se lleva por delante
el ISRC, el código de barras, el sello… y el ISRC no es un adorno: es la única
llave con la que se empareja una canción en TIDAL, porque su API no busca por
texto.

Así que se hacen tres cosas, en este orden:

1. Se vuelven a pasar todas a mano con `-metadata`, además del `-map_metadata`.
2. Las que **aun así** no entren se escriben aparte con **`mutagen`**, como
   *átomos libres* (`----` con `mean = com.apple.iTunes`) — que es donde las
   pone iTunes y donde las buscan los demás programas. No se hace a mano porque
   agrandar el índice de un MP4 obliga a recolocar los desplazamientos de cada
   trozo de audio, y esa es justo la clase de cosa que no conviene escribirse
   uno mismo.
3. Se vuelve a leer el fichero para confirmarlo. Y si algo sigue faltando, se
   dice en el registro:

```
OJO: el .m4a no se ha quedado con estas etiquetas: barcode, isrc, publisher
     el ISRC es el codigo con el que se empareja una cancion en TIDAL:
     sin el, esa no se puede publicar alli
```

No se aborta por esto —la música está entera, que es lo que importa— pero
tampoco se calla: enterarte un mes después, con la biblioteca ya convertida, no
te sirve de nada.

Al leer las etiquetas de un `.m4a` también se miran las libres, así que el ISRC
recién escrito sirve de verdad para publicar en TIDAL, que era el motivo.

### Los artistas que faltan

Un FLAC de tienda suele traer **un solo artista** aunque la canción sea de
varios, y así entra en iTunes. Se completa por dos vías, que se complementan:

**Al convertir, desde el título.** Lo que va en `(feat. X)`, `ft. Y` o
`[with Z]` se añade al artista. Es gratis, va en el mismo paso y no necesita
red:

| Artista | Título | Queda |
|---|---|---|
| `Lola Indigo` | `EL BACHATÓN (feat. Lucho RK)` | `Lola Indigo; Lucho RK` |
| `Karol G` | `Provenza ft. Maria Becerra, Nicki Nicole` | `Karol G; Maria Becerra; Nicki Nicole` |
| `ROSALÍA; The Weeknd` | `La Fama (feat. The Weeknd)` | *sin cambio*, ya estaba |
| `Manolo` | `Canción (Remastered 2016)` | *sin cambio*, eso no es nadie |

Solo suma: nunca quita ni sustituye, y no repite a quien ya estuviera escrito
aunque sea de otra manera. El título se deja como está.

**Después, por el ISRC.** Botón **Artistas por ISRC** en *iTunes › Traer de TIDAL*.
Recorre la biblioteca, y de las canciones que figuran a nombre de uno solo
busca su ISRC en Spotify (o TIDAL) y les añade los demás intérpretes. Como el
ISRC identifica **esa grabación exacta**, la lista es la del sello y no una
adivinanza: por eso aquí no hace falta emparejar por texto ni desconfiar del
resultado, al revés que en el resto de la aplicación.

Solo mira las que pueden ganar algo —las que ya traen varios nombres se saltan—
y recuerda lo buscado, así que una segunda pasada no vuelve a preguntar.

### Antes de machacar una canción

Toda reescritura pasa por `comprobar_salida`, que mira el fichero nuevo **antes**
de que sustituya al viejo. Que ffmpeg termine diciendo que todo ha ido bien no es
prueba suficiente: pasó una vez que una conversión salió con la portada y sin
pista de audio, ffmpeg contestó 0, y al sustituir se perdió el original.

Se comprueban tres cosas, de la más grave a la menos:

1. **Que siga habiendo audio.** Un `.m4a` con la portada y nada más pesa 60 KB y
   se abre sin dar ningún error.
2. **Que no haya saltos en la línea de tiempo.** Un ALAC declara en su cookie
   cuánto mide un fotograma (4096 muestras) y ninguno puede medir más. Cuando la
   tabla `stts` declara uno de 7666, ahí no hay un fotograma gigante: hay **un
   hueco en el reloj**. ffmpeg lo decodifica sin quejarse y la duración total
   cuadra, pero quien recorra la tabla para dibujar la onda se encuentra el
   agujero — y rekordbox se cierra ahí.

   Sale de normalizar sin reencuadrar después: el filtro guarda audio por
   delante para decidir, al terminar lo suelta de golpe, y el multiplexor apunta
   el salto como si fuera un fotograma larguísimo. Por eso `filtro_audio` pone
   siempre un `aresample` detrás del `loudnorm`, aunque no se normalice.
3. **Que la cabecera declare su frecuencia**, no un 0 (lo de arriba).
4. **Que dure lo mismo.** Bajar la calidad cambia lo que ocupa, nunca lo que dura.

Si algo de eso falla, el fichero nuevo se tira, el viejo se queda donde estaba y
la canción aparece en la lista de errores.

Los dos botones siguientes están en **iTunes › Una sola canción**, aparte de las
pasadas: no recorren nada, trabajan sobre el fichero que elijas.

**Examinar un fichero.** Cuando un reproductor se cierra al abrir una canción y no
dice por qué, la única vía es poner al lado una que sí funcione y ver en qué se
diferencian. Este botón cuenta todo lo que se sabe de un fichero: contenedor,
duración y bitrate reales, cada stream con su códec y su formato de muestra, todas
las etiquetas (cortando las larguísimas, pero diciendo cuánto miden), si el índice
`moov` va al principio o al final, **qué frecuencia declara la cabecera** (el 0 Hz
de arriba se ve aquí), **si el fichero llega entero o está cortado** —eso se ve
leyendo sus bloques, sin decodificar nada— y por último decodifica el audio de
cabo a rabo para ver si da errores.

El informe se abre en una ventana con **Copiar todo**, y además queda en el
registro de la pestaña Sincronización.

**Convertir/arreglar uno.** El mismo botón, pero además de mirar hace el
trabajo, sobre **esa canción y ninguna más**. Qué hace depende de lo que le des:

| Le das | Hace |
|---|---|
| Un FLAC, WAV, AIFF, APE, WavPack | Lo **convierte a ALAC** al lado, como si estuviera en la carpeta de auto-añadir: normaliza el volumen, completa las etiquetas y aplica el techo de calidad. **El original se queda donde está**, aquí no se borra nada. |
| Un `.m4a` | Lo **arregla**: baja la calidad si pasa del techo, **reencuadra el audio** si su línea de tiempo tiene saltos y pasa la portada a JPEG si hace falta. Lo mismo que le haría la pasada completa, para poder verlo en una antes. |

Enseña el antes, lo que le ha hecho y el después. Es la forma de probar en un
fichero antes de soltar una pasada contra la biblioteca entera; con un `.m4a`,
hazlo sobre una **copia** la primera vez, que ahí sí se reescribe en el sitio.

**Releer datos en iTunes.** Para cuando iTunes sigue diciendo *9216 kbps* de una
canción que en disco ya está a 1411. No es un fallo de la conversión: iTunes se
queda con lo que anotó el día que la importó, y el fichero solo se relee si se lo
pides. El botón lo hace por ti, y solo sobre las que declaran más calidad de la
que cabe en un CD, que son las únicas que pueden estar desfasadas. Si tu
reproductor dice 1411 y iTunes 9216, el fichero está bien: el que miente es
iTunes.

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
`convertir-a-alac.bat`, o `python main.py --schedule-flac 04:00` /
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
python main.py --status         estado, tarea programada y la cola marcada
python main.py --dry-run --sync simulación
python main.py --schedule 03:00 registra la tarea diaria
python main.py --unschedule     la elimina
```

O los lanzadores: `sincronizar-ahora.bat`, `estado.bat`, `convertir-a-alac.bat`,
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

Doble clic en **`publicar.bat`**. Sube el número, hace el commit y el push; la
release la crea GitHub sola:

```
publicar.bat            arreglos        1.1.0 -> 1.1.1
publicar.bat menor      cosas nuevas    1.1.0 -> 1.2.0
publicar.bat mayor      cambios gordos  1.1.0 -> 2.0.0
publicar.bat 1.5.2      ese número exacto
```

Te enseña qué va a hacer y pide confirmación antes de tocar nada. Si estás en
otra rama te lo dice y **se ofrece a llevarlo a `main` y publicar desde allí**,
dejándote de vuelta en la tuya al terminar; GitHub solo publica desde `main`.

Por debajo lo publica [`.github/workflows/publicar.yml`](.github/workflows/publicar.yml):
cuando llega un push a `main`, lee `__version__`, comprueba que esa versión no
esté ya publicada, verifica que todo compila y crea la etiqueta y la release con
sus notas. Si la versión no ha cambiado no hace nada, así que puedes hacer todos
los push que quieras sin publicar de más.

Lo puedes seguir en la pestaña **Actions** del repositorio. Y si algún día
prefieres hacerlo a mano, sigue valiendo: cambias `__version__`, push, y creas la
release con la etiqueta `v1.1.0`.

La aplicación compara su versión con la etiqueta de la última release. Si publicas
una **menor o igual** a la instalada, nadie se actualiza — para retirar una versión
mala, publica otra con número mayor.

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

**No llego a un botón, se queda por debajo del borde** — no debería pasar: cada
pestaña se desplaza sola en cuanto su contenido no cabe, con la barra a la
derecha y la rueda del ratón. Si la ventana está muy baja y aun así no ves la
barra, agrándala un poco: hay un mínimo de 560×360.

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
