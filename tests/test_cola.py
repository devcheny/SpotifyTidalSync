"""La cola: que se encadena detras de cada sincronizacion.

Es lo que corre la tarea de cada 24 h, asi que aqui se comprueba lo que de
verdad importa de ella: que solo haga lo marcado, en el orden de la lista, y
que un paso que falle no se lleve por delante a los que vienen detras.

Los pasos de verdad (convertir, repasar, publicar) tienen sus propias pruebas.
Aqui se cambian por unos de mentira: lo que se prueba es la cola.
"""
import os
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

# Los datos de la aplicacion van a una carpeta de usar y tirar: esto no puede
# tocar la configuracion ni el estado de quien lo ejecute.
os.environ["APPDATA"] = str(AQUI / "datos-cola")

from stsync import sync as mod
from stsync.config import Config, DEFAULTS
from stsync.sync import PASOS, Paso, SyncEngine


class Cuentas:
    """TokenStore de mentira: dice que si a todo."""
    def has(self, _servicio):
        return True


def motor(**ajustes):
    cfg = Config(dict(Config().data))
    cfg.set("sync_favorites", False)      # eso ya tiene sus propias pruebas
    cfg.set("sync_playlists", False)
    for clave, valor in ajustes.items():
        cfg.set(clave, valor)
    lineas = []
    engine = SyncEngine(cfg, lineas.append)
    engine.tokens = Cuentas()
    # display_name sale a preguntarle a la API por el nombre de la cuenta.
    type(engine.spotify).display_name = property(lambda _s: "cheny (falso)")
    type(engine.tidal).display_name = property(lambda _s: "cheny (falso)")
    return engine, lineas


def pasos_de_mentira(hechos, romper=()):
    """Una lista de pasos que solo apuntan que se les ha llamado."""
    def hacer(nombre):
        def dentro(_engine):
            hechos.append(nombre)
            if nombre in romper:
                raise OSError("se ha roto a proposito")
            return f"{nombre} hecho"
        return dentro
    return [Paso(f"paso_{n}", f"Paso {n}", "de mentira", hacer(f"paso_{n}"))
            for n in ("uno", "dos", "tres")]


# --- 1. solo corre lo marcado, y en el orden de la lista --------------------
hechos = []
mod.PASOS = pasos_de_mentira(hechos)
engine, lineas = motor(paso_uno=True, paso_dos=False, paso_tres=True)
engine.run()
print("1. se han hecho:", hechos)
assert hechos == ["paso_uno", "paso_tres"], hechos
assert any("paso_uno hecho" in l for l in lineas), lineas
assert not any("paso_dos" in l for l in lineas), "el dos no estaba marcado"

# --- 2. con nada marcado no se encadena nada -------------------------------
hechos = []
mod.PASOS = pasos_de_mentira(hechos)
engine, lineas = motor()
engine.run()
print("2. sin marcar nada:", hechos)
assert hechos == [], hechos

# --- 3. un paso que falla no para a los que vienen detras ------------------
# Lo importante de una tarea desatendida: que iTunes este cerrado no puede
# dejar los FLAC sin convertir.
hechos = []
mod.PASOS = pasos_de_mentira(hechos, romper=("paso_uno",))
engine, lineas = motor(paso_uno=True, paso_dos=True, paso_tres=True)
stats = engine.run()
print("3. tras romperse el primero:", hechos)
assert hechos == ["paso_uno", "paso_dos", "paso_tres"], hechos
assert len(stats.errors) == 1, stats.errors
assert "Paso uno" in stats.errors[0], stats.errors
assert "se ha roto a proposito" in stats.errors[0], stats.errors

# --- 4. si se pide parar, la cola se corta ---------------------------------
hechos = []
mod.PASOS = pasos_de_mentira(hechos)
cfg = Config(dict(Config().data))
for clave in ("paso_uno", "paso_dos", "paso_tres"):
    cfg.set(clave, True)
cfg.set("sync_favorites", False)
cfg.set("sync_playlists", False)
parar = [False]
engine = SyncEngine(cfg, lambda m: None, lambda: parar[0])
engine.tokens = Cuentas()


def para_despues_del_primero(_engine):
    hechos.append("uno")
    parar[0] = True
    return ""


mod.PASOS = [Paso("paso_uno", "Paso uno", "", para_despues_del_primero)] \
    + mod.PASOS[1:]
engine.run()
print("4. con parada a mitad:", hechos)
assert hechos == ["uno"], "no puede seguir con los de detras"

mod.PASOS = PASOS      # devolver la lista de verdad para lo que queda

# --- 5. la lista de verdad esta bien formada -------------------------------
# Un paso nuevo se anade en un solo sitio, asi que lo unico que puede
# olvidarse es su valor por defecto: sin el, cfg.get devuelve None y el paso
# no correria nunca, en silencio.
print()
print("5. los", len(PASOS), "pasos de verdad:")
claves = [paso.clave for paso in PASOS]
assert len(claves) == len(set(claves)), f"hay claves repetidas: {claves}"
for paso in PASOS:
    marca = "x" if DEFAULTS.get(paso.clave) else " "
    print(f"   [{marca}] {paso.clave:22} {paso.nombre}")
    assert paso.clave in DEFAULTS, f"{paso.clave} no tiene valor por defecto"
    assert callable(paso.hacer), paso.clave
    assert paso.nombre and paso.detalle, paso.clave

# Y el ultimo tiene que ser el de releer: iTunes se entera al final, cuando
# ya no queda nadie que vaya a tocar los ficheros.
assert PASOS[-1].clave == "refresh_after_sync", claves

print()
print("COLA OK")
