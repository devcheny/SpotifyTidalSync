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

    # --- Comportamiento ---
    "country_code": "ES",            # ISO 3166-1 alpha-2, para el catalogo de TIDAL
    "dry_run": False,                # simula: no escribe nada en las cuentas
    "max_unmatched_report": 500,
}


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
