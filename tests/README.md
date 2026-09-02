# Pruebas

Doble clic en **`ejecutar-tests.bat`**. No hacen falta iTunes, ffmpeg ni cuentas
conectadas: todo lo de fuera se sustituye por dobles, así que también valen en un
equipo de desarrollo pelado.

| Prueba | Qué cubre |
|---|---|
| `test_emparejar.py` | Que una canción de TIDAL encuentre la suya en iTunes: acentos, `feat.`, `(Remastered)`, recopilatorios, erratas de una letra… **y sobre todo lo que NO debe casar**, que es donde está el peligro: entre otras, que el directo no se cuele por llamarse igual que la del disco, y que con dos versiones tuyas gane la que dura lo mismo. |
| `test_playlists.py` | El motor TIDAL → iTunes: crear y actualizar listas, simulación, espejo, y que la lista de faltantes suelte lo que ya no falta. |
| `test_tidal.py` | El parseo de la API: nombres de artista incluidos, el formato antiguo y el reintento si TIDAL rechaza el `include`. |
| `test_convertir.py` | El conversor FLAC → ALAC con un ffmpeg de mentira: nombres que chocan, calidad CD, completar las etiquetas que faltan, borrar o mover el original, y que un fallo no deje restos. |
| `test_buscar.py` | La salida de `--buscar`, que es lo que se mira cuando algo no casa. |
| `test_actualizar.py` | La actualizacion desde GitHub: comparar versiones, los errores de la API, y que una descarga que no vale no toque la instalacion. |
| `test_completar.py` | Completar los artistas que faltan en iTunes: que anada los que faltan, que no pise un artista distinto y que la simulacion no escriba. |
| `test_publicar.py` | La secuencia de git al publicar una version, con git sustituido por un registrador: comprueba donde empuja sin empujar nada. |
| `test_normalizar.py` | El repaso de toda la biblioteca: que arregle lo que hace falta, deje lo demas, y que si ffmpeg falla el original no se pierda. |
| `test_publicar_listas.py` | Los dos sentidos de la pestaña Publicar: llevar las listas de iTunes a Spotify y TIDAL (que las cree, cuáles quedan públicas, y que sin ISRC no se pueda enlazar en TIDAL) y traerlas de vuelta (que meta en iTunes lo que ya tienes, que apunte lo que no en la lista de faltantes y la limpie cuando deje de faltar, que no toque las inteligentes, y que esa lista no viaje a TIDAL). |
| `test_caratulas.py` | Las portadas PNG dentro de un `.m4a`, que dejan el fichero fuera de norma: que las encuentre sin tocar nada en simulación, que al arreglarlas copie el audio tal cual (`-c:a copy`, sin normalizar), que no toque las que ya vienen en JPEG ni los FLAC, que un ffmpeg roto deje el original intacto y sin temporales, y que releer los datos en iTunes solo mire las que declaran más calidad de la que cabe en un CD. También bajar a calidad CD: que solo toque lo que está por encima, que no lleve `-af` (ahí no se normaliza), y que un ffmpeg roto deje la canción intacta. |
| `test_examinar.py` | El informe de un fichero suelto: que lea los bloques de un `.m4a` montado a mano (dónde está el índice, y que un fichero cortado se detecte sin decodificarlo), que corte las etiquetas larguísimas diciendo cuánto miden, y que si el audio no decodifica el informe traiga las quejas de ffmpeg. |
| **regresión de pérdida de datos** | En `test_caratulas.py` (casos 15 y 16) y `test_examinar.py` (12 y 13): con `SIN_AUDIO=1` el ffmpeg de mentira produce un `.m4a` con la portada y sin pista de audio, como pasó de verdad. La prueba exige que el original **siga intacto** y que el fallo se apunte. Si alguien vuelve a quitar la comprobación, esto se pone rojo. |
| `test_ventana.py` | Que las pestañas se desplacen cuando el contenido no cabe: que en una ventana pequeña salga la barra y el botón de actualizar acabe siendo alcanzable, que con sitio de sobra no salga barra y el registro siga estirándose, y que la rueda mueva el recuadro que hay debajo del ratón (dentro de una lista de playlists gana la lista, no la pestaña). Abre la ventana de verdad un instante, casi transparente. |
| `test_cola.py` | La cola que se encadena detras de cada sincronizacion, que es lo que corre la tarea de cada 24 h: que solo haga lo marcado y en el orden de la lista, que un paso que falle no se lleve por delante a los de detras, que la parada corte la cola, y que todos los pasos de verdad tengan valor por defecto (sin el, uno nuevo no correria nunca y en silencio). |
| `test_borrar.py` | Que el borrado en una playlist de TIDAL mande el `meta` de cada entrada, sin el cual la API responde 400. |

## `test_tareas.py` va aparte

Da de alta tareas de verdad en el Programador de Windows (y las borra al
terminar), por eso no entra en el lanzador. Ejecútalo a mano cuando toques
`stsync\scheduler.py`:

```
..\.venv\Scripts\python.exe test_tareas.py
```

Antes de nada comprueba que no existan ya las tareas y se planta si las hay, para
no pisar las tuyas.

## Los dobles de iTunes

`dobles.py` tiene el `Cancion`, el `Coleccion` y el `Biblioteca` que comparten varias pruebas: una colección COM de iTunes recorrida por índice desde 1, sin COM ni pywin32. Como las cuatro pasadas que recorren la biblioteca entera lo hacen por `itunes.recorrer_biblioteca`, basta con parchear `itunes.ITunesLibrary` para todas.

## Cómo están escritas

Sin dependencias: son scripts que se lanzan solos y terminan con un `assert`. El
que falla dice qué esperaba y qué obtuvo. `ffmpeg_falso.py` imita a ffmpeg —crea
el fichero de salida, apunta los argumentos que recibió y sabe fallar a propósito—
para poder probar el conversor sin convertir nada.
