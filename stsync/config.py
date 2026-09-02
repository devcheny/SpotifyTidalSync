"""Configuracion persistente de la aplicacion."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

from .paths import config_file

DEFAULTS: dict[str, Any] = {
    # --- Credenciales de las apps de desarrollador (las creas tu, ver README) ---
    "spotify_client_id": "",
    "spotify_redirect_uri": "http://127.0.0.1:8898/callback",
    "tidal_client_id": "",
    "tidal_redirect_uri": "http://127.0.0.1:8899/callback",

    # --- Que se sincroniza ---
    "sync_favorites": True,          # canciones que te gustan / favoritos
    "sync_playlists": True,          # playlists propias
    "direction": "both",             # both | spotify_to_tidal | tidal_to_spotify
    "propagate_deletions": False,    # si borras en un lado, borrar en el otro
    "playlist_prefix": "",           # prefijo al crear playlists en el destino
    "playlist_exclude": [],          # nombres de playlist a ignorar
    "playlist_include": [],          # si no esta vacio, SOLO estas se sincronizan

    # --- iTunes (Windows, con iTunes de Apple instalado) ---
    "itunes_enabled": False,          # volcar las playlists de TIDAL en cada sync
    "itunes_playlist_prefix": "TIDAL - ",
    "itunes_playlists": [],           # vacio = todas las playlists de TIDAL
    "itunes_remove_extra": False,     # quitar de iTunes lo que ya no esta en TIDAL
    "itunes_missing_playlist": False, # dejar en TIDAL "<nombre> - Faltantes en iTunes"

    # --- Conversion de FLAC a ALAC (necesita ffmpeg) ---
    # La n con virgulilla va escapada para que la ruta siga siendo correcta
    # aunque este fichero se copie con otra codificacion.
    "flac_folder": "C:\\Music\\iTunes\\iTunes Media\\"
                   "A\u00f1adir autom\u00e1ticamente a iTunes",
    # Hasta donde se graba, como techo (ver convert.OBJETIVOS):
    #   48k  24 bits / 48 kHz, 2304 kbps. El equilibrio, y lo maximo que un
    #        .m4a puede declarar en su cabecera.
    #   cd   16 bits / 44,1 kHz, 1411 kbps. Lo que menos ocupa.
    "quality_target": "48k",
    "flac_normalize": True,           # loudnorm, como el flac2alac.bat de siempre
    "flac_two_pass": True,            # medir antes de normalizar (mas preciso)
    "flac_complete_tags": True,       # rellenar artista/titulo que falten
    "flac_keep_artwork": True,        # copiar la caratula al .m4a si la trae
    "flac_delete_source": True,       # borrar el FLAC tras convertirlo bien
    "ffmpeg_path": "",                # vacio = buscarlo en el PATH
    "library_min_lufs": -9.5,         # margen que se da por bueno al
    "library_max_lufs": -8.5,         # repasar toda la biblioteca
    "library_to_alac": True,          # pasar WAV y FLAC de la biblioteca a ALAC
    "library_include_lossy": False,   # tocar tambien MP3 y demas
    "library_skip_done": True,        # no volver a medir lo ya repasado
    "artwork_remove": False,          # al repasar caratulas, quitarlas
                                      # en vez de pasarlas a JPEG
    "flac_schedule_time": "04:00",    # o repaso propio, una hora despues

    # --- La cola: que se encadena detras de cada sincronizacion ---
    # Tambien las hace la tarea diaria, que llama a lo mismo. El orden en que
    # corren no es este, sino el de sync.PASOS. Todas apagadas por defecto:
    # lo que toca ficheros no se enciende solo.
    "flac_after_sync": False,         # convertir a ALAC lo que haya llegado
    "publish_after_sync": False,      # subir las listas de iTunes a Spotify
    "fix_after_sync": False,          # revisar y arreglar los ficheros
    "artwork_after_sync": False,      # pasar a JPEG las portadas que no valgan
    "artists_after_sync": False,      # completar datos desde TIDAL
    "isrc_after_sync": False,         # completar los artistas por ISRC
    "library_after_sync": False,      # repaso de volumen (lo mas lento)
    "refresh_after_sync": False,      # que iTunes relea lo que ha cambiado

    # --- Publicar en Spotify y TIDAL las listas de iTunes ---
    "publish_to_spotify": True,       # replicar en Spotify
    "publish_to_tidal": False,        # replicar en TIDAL (solo lo que tenga ISRC)
    "publish_playlists": [],          # que playlists de iTunes se llevan fuera
    "publish_import": [],             # cuales se traen de vuelta desde Spotify
    "publish_public": [],             # cuales de esas quedan publicas
    "publish_prefix": "iTunes - ",    # asi se distinguen de las demas
    "publish_missing_playlist": True, # dejar en Spotify "<lista> - Faltantes en iTunes"

    # --- Actualizaciones (para repartir la app entre conocidos) ---
    "github_repo": "devcheny/SpotifyTidalSync",   # de donde salen las versiones
    "update_check": True,           # mirar si hay version nueva al abrir

    # --- Validar que es la misma grabacion, no solo el mismo titulo ---
    "match_check_duration": True,    # descartar lo que dure muy distinto
    "match_duration_tolerance": 7,   # segundos de margen

    # --- Comportamiento ---
    "country_code": "ES",            # ISO 3166-1 alpha-2, para el catalogo de TIDAL
    "dry_run": False,                # simula: no escribe nada en las cuentas
    "max_unmatched_report": 500,
}


def _migrar(data: dict[str, Any]) -> None:
    """Trae a la forma de ahora los ajustes que tenian otra forma antes.

    La calidad era una casilla de si o no ("calidad CD") y ahora es un techo
    con dos alturas. Quien la tuviera marcada queria calidad CD y se queda con
    ella; quien no, pasa al equilibrio de 24 bits / 48 kHz, que es lo mas alto
    que un .m4a puede declarar (por encima, la cabecera se queda a cero y hay
    programas que se cierran al abrirlo).
    """
    if "quality_target" not in data and "flac_cd_quality" in data:
        data["quality_target"] = "cd" if data["flac_cd_quality"] else "48k"
    data.pop("flac_cd_quality", None)


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    # -- acceso comodo ------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["data"][name]
        except KeyError as exc:  # pragma: no cover - solo errores de programacion
            raise AttributeError(name) from exc

    def get(self, name: str, default: Any = None) -> Any:
        return self.data.get(name, default)

    def set(self, name: str, value: Any) -> None:
        self.data[name] = value

    def repo(self) -> str:
        """El proyecto de GitHub del que salen las actualizaciones.

        Un config.json de antes de que esto existiera lo tiene guardado en
        blanco, y un valor guardado gana al de por defecto: por eso vacio se
        entiende como "el de la aplicacion" y no como "ninguno".
        """
        return str(self.data.get("github_repo") or DEFAULTS["github_repo"])

    # -- persistencia -------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        path = config_file()
        data = dict(DEFAULTS)
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    data.update(stored)
            except (json.JSONDecodeError, OSError):
                pass  # config corrupta -> se usan los valores por defecto
        _migrar(data)
        cfg = cls(data)
        if not path.exists():
            cfg.save()
        return cfg

    def save(self) -> None:
        """Escribe primero en un temporal y luego reemplaza: si algo falla a
        media escritura, el config.json anterior sigue intacto."""
        path = config_file()
        tmp = path.with_suffix(".json.tmp")
        payload = json.dumps(self.data, indent=2, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    def is_configured(self) -> bool:
        return bool(self.data["spotify_client_id"] and self.data["tidal_client_id"])
