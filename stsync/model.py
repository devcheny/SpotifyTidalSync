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
    year: int = 0                   # de publicacion, 0 si no se sabe

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


# --------------------------------------------------------------------------
# Validar que dos canciones son la misma grabacion
# --------------------------------------------------------------------------
# Margen por defecto. Un remaster suele quedarse en uno o dos segundos; una
# version en directo, un radio edit o una version extendida se van mucho mas.
DURATION_TOLERANCE_S = 7.0


def mmss(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def same_length(a_seconds: float, b_seconds: float,
                tolerance: float = DURATION_TOLERANCE_S) -> bool:
    """Si una de las dos no se sabe, no se penaliza: no hay con que juzgar."""
    if not a_seconds or not b_seconds:
        return True
    return abs(a_seconds - b_seconds) <= tolerance


def same_recording(mine: Track, found: Track,
                   tolerance: float = DURATION_TOLERANCE_S) -> str:
    """Motivo por el que 'found' NO parece la misma grabacion, o "" si cuela.

    Titularse igual no basta: 'Bohemian Rhapsody' puede ser el disco, el
    directo de Wembley o una version de un tributo. Lo unico que identifica de
    verdad una grabacion es el ISRC; cuando no lo hay, la duracion es el mejor
    indicio que queda.
    """
    if mine.isrc and found.isrc and mine.isrc.upper() == found.isrc.upper():
        return ""                       # mismo ISRC: es esa y no otra
    if same_length(mine.duration_ms / 1000.0, found.duration_ms / 1000.0,
                   tolerance):
        return ""
    return (f"alli dura {mmss(found.duration_ms / 1000.0)} y la tuya "
            f"{mmss(mine.duration_ms / 1000.0)}: parece otra version")


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
