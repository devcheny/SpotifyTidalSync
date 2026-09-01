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


def caja(tipo, cuerpo):
    return (len(cuerpo) + 8).to_bytes(4, "big") + tipo + cuerpo


def m4a_falso(rate, con_audio=True, segundos=180):
    """Un .m4a con la forma justa para que lo lean nuestros comprobadores.

    No suena, pero tiene los bloques que se miran antes de dar una conversion
    por buena: el mvhd con la duracion y la entrada de audio con su frecuencia
    en el campo de 16.16 bits. Con con_audio=False sale la cancion sin pista,
    que es justo el desastre del que hay que protegerse.
    """
    mvhd = caja(b"mvhd", bytes(12) + (1000).to_bytes(4, "big")
                + (segundos * 1000).to_bytes(4, "big") + bytes(80))
    # mdhd y hdlr no los mira nuestro codigo, pero mutagen si: sin ellos no
    # reconoce la pista de audio y se niega a escribir las etiquetas.
    mdhd = caja(b"mdhd", bytes(12) + (rate).to_bytes(4, "big")
                + (segundos * rate).to_bytes(4, "big") + bytes(4))
    hdlr = caja(b"hdlr", bytes(8) + b"soun" + bytes(12) + b"SoundHandler" + bytes(1))
    entrada = bytes(8) + bytes(8) + bytes(8) + (rate << 16).to_bytes(4, "big")
    stsd = caja(b"stsd", bytes(8) + (caja(b"alac", entrada) if con_audio
                                     else caja(b"mjpg", entrada)))
    stbl = caja(b"stbl", stsd)
    trak = caja(b"trak", caja(b"mdia", mdhd + hdlr + caja(b"minf", stbl)))
    return (caja(b"ftyp", b"M4A isomiso2") + caja(b"moov", mvhd + trak)
            + caja(b"mdat", b"A" * 400))
