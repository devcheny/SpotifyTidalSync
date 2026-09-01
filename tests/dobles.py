"""Los dobles de iTunes que comparten varias pruebas.

Una coleccion COM de iTunes se recorre por indice empezando en 1, y de cada
cancion solo se le piden unos pocos campos. Eso es lo que imita esto, sin
COM, sin pywin32 y sin iTunes: lo justo para que el codigo de verdad no note
la diferencia.

Quien necesite algo mas (que falle al releer, que sea de la nube, que no deje
cambiar la ruta) lo pide por parametro en vez de copiarse la clase.
"""
from pathlib import Path


class Cancion:
    """Lo poco que se le pide a un IITTrack."""

    def __init__(self, ruta, kind=1, bitrate=1411, rate=44100,
                 refresco_falla=False, location_falla=False):
        object.__setattr__(self, "Location", str(ruta))
        self.Kind = kind                    # 1 = fichero; lo demas, nube o CD
        self.BitRate = bitrate
        self.SampleRate = rate
        self.Name = Path(ruta).name
        self.refrescada = False
        self.refresco_falla = refresco_falla
        self.location_falla = location_falla

    def __setattr__(self, campo, valor):
        if campo == "Location" and getattr(self, "location_falla", False):
            raise OSError("iTunes no acepta esa ruta")
        object.__setattr__(self, campo, valor)

    def UpdateInfoFromFile(self):
        if self.refresco_falla:
            raise OSError("iTunes esta ocupado")
        self.refrescada = True


class Coleccion:
    """Una coleccion COM: cuenta desde 1, como las de iTunes."""

    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):
        return self.items[i - 1]


class Biblioteca:
    """Hace de ITunesLibrary. Las canciones se ponen en Biblioteca.canciones.

    Es un atributo de clase a proposito: el codigo de verdad crea su propia
    instancia por dentro, asi que la prueba no puede pasarle la lista.
    """

    canciones: list = []

    def __init__(self, log=None):
        self.log = log or (lambda _m: None)
        self.app = type("App", (), {})()
        self.app.LibraryPlaylist = type("PL", (), {})()
        self.app.LibraryPlaylist.Tracks = Coleccion(Biblioteca.canciones)

    def connect(self):
        self.log("  iTunes conectado (falso)")

    def close(self):
        pass

    @staticmethod
    def is_writable(playlist):
        return not bool(getattr(playlist, "Smart", False))
