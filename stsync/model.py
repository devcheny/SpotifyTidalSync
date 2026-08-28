"""Representacion neutra de una pista, comun a los dos servicios."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    service: str            # "spotify" | "tidal"
    id: str                 # id nativo del servicio
    title: str
    artist: str
    album: str = ""
    isrc: str = ""
    duration_ms: int = 0
    artists: tuple[str, ...] = ()   # todos los interpretes, si el servicio los da

    @property
    def credit(self) -> str:
        """Todos los interpretes juntos, como los suele escribir iTunes."""
        return ", ".join(self.artists) if self.artists else self.artist

    @property
    def key(self) -> str:
        """Clave de identidad: ISRC si existe, si no una firma de texto."""
        return f"isrc:{self.isrc.upper()}" if self.isrc else f"txt:{self.text_key}"

    @property
    def text_key(self) -> str:
        return f"{normalize(self.artist)}|{normalize(self.title)}"

    def __str__(self) -> str:
        # TIDAL no siempre devuelve el nombre del artista en las relaciones.
        return f"{self.artist} - {self.title}" if self.artist else self.title


_PAREN = re.compile(r"\s*[\(\[][^)\]]*(remaster|remastered|deluxe|mono|stereo|"
                    r"bonus|edition|version|edit|live)[^)\]]*[\)\]]", re.IGNORECASE)
_FEAT = re.compile(r"\s*[-(\[]?\s*(feat\.?|ft\.?|with)\s+[^)\]]*[\)\]]?", re.IGNORECASE)
_NONWORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Normaliza titulos/artistas para poder compararlos sin ruido."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = _PAREN.sub(" ", text)
    text = _FEAT.sub(" ", text)
    text = _NONWORD.sub(" ", text)
    return " ".join(text.split())
