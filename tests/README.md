# Pruebas

Doble clic en **`ejecutar-tests.bat`**. No hacen falta iTunes, ffmpeg ni cuentas
conectadas: todo lo de fuera se sustituye por dobles, así que también valen en un
equipo de desarrollo pelado.

| Prueba | Qué cubre |
|---|---|
| `test_emparejar.py` | Que una canción de TIDAL encuentre la suya en iTunes: acentos, `feat.`, `(Remastered)`, recopilatorios, erratas de una letra… **y sobre todo lo que NO debe casar**, que es donde está el peligro. |
| `test_playlists.py` | El motor TIDAL → iTunes: crear y actualizar listas, simulación, espejo, y que la lista de faltantes suelte lo que ya no falta. |
| `test_tidal.py` | El parseo de la API: nombres de artista incluidos, el formato antiguo y el reintento si TIDAL rechaza el `include`. |
| `test_convertir.py` | El conversor FLAC → ALAC con un ffmpeg de mentira: nombres que chocan, calidad CD, borrar o mover el original, y que un fallo no deje restos. |
| `test_buscar.py` | La salida de `--buscar`, que es lo que se mira cuando algo no casa. |
| `test_actualizar.py` | La actualizacion desde GitHub: comparar versiones, los errores de la API, y que una descarga que no vale no toque la instalacion. |
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

## Cómo están escritas

Sin dependencias: son scripts que se lanzan solos y terminan con un `assert`. El
que falla dice qué esperaba y qué obtuvo. `ffmpeg_falso.py` imita a ffmpeg —crea
el fichero de salida, apunta los argumentos que recibió y sabe fallar a propósito—
para poder probar el conversor sin convertir nada.
