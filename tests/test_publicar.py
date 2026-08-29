"""Prueba de publicar.py: que la secuencia de git sea la que toca.

No se ejecuta ni un solo comando de git de verdad: se sustituye por un
registrador. Asi se puede comprobar que empuja donde debe sin publicar nada.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import publicar


class Git:
    """Sustituye a publicar.git y apunta lo que se le pide."""

    def __init__(self, rama="itunes-sync", pendiente="", falla_merge=False):
        self.ordenes: list[tuple[str, ...]] = []
        self.rama = rama
        self.pendiente = pendiente
        self.falla_merge = falla_merge

    def __call__(self, *args, capturar=True):
        self.ordenes.append(args)
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return self.rama
        if args[0] == "status":
            return self.pendiente
        if args[0] == "merge" and self.falla_merge:
            raise SystemExit("conflicto de prueba")
        return ""

    @property
    def resumen(self):
        return [" ".join(o) for o in self.ordenes]


def correr(respuestas, **ajustes):
    """Lanza publicar.main() con las respuestas dadas y sin tocar el disco."""
    guardado = {"version": "1.0.2"}
    respuestas = list(respuestas)
    falso = Git(**ajustes)

    publicar.git = falso
    publicar.version_actual = lambda: guardado["version"]
    publicar.INIT = type("Falso", (), {
        "read_text": lambda self, **k: '__version__ = "1.0.2"',
        "write_text": lambda self, texto, **k: guardado.update(escrito=texto),
    })()
    publicar.input = lambda _: respuestas.pop(0)   # type: ignore[attr-defined]
    try:
        codigo = publicar.main()
    except SystemExit as exc:
        codigo = exc
    return codigo, falso, guardado


publicar.input = input  # se sustituye en cada llamada
sys.argv = ["publicar.py", "parche"]

# --- 1. desde otra rama, aceptando llevarlo a main --------------------------
codigo, falso, guardado = correr([""])          # Enter = si
print("1. ordenes:", falso.resumen)
assert "escrito" in guardado, "deberia haber subido la version"
assert '__version__ = "1.0.3"' in guardado["escrito"], guardado["escrito"]
assert falso.resumen == [
    "rev-parse --abbrev-ref HEAD",
    "status --porcelain",
    "add " + str(publicar.INIT),
    "commit -m Version 1.0.3",
    "checkout main",
    "merge itunes-sync --ff-only",
    "push origin main",
    "checkout itunes-sync",
    # La rama de trabajo tambien se empuja: si se queda atras en GitHub, una
    # release etiquetada sobre ella llevaria dentro la version anterior.
    "push origin itunes-sync",
], falso.resumen
print("   commit en la rama, merge a main, push a las dos y vuelta. Correcto.")

# --- 2. desde otra rama, diciendo que no ------------------------------------
codigo, falso, guardado = correr(["n", "s"])
print("2. ordenes:", falso.resumen)
assert "push origin itunes-sync" in falso.resumen, falso.resumen
assert "checkout main" not in falso.resumen, "no debe tocar main sin permiso"
print("   sube la version en la rama y no toca main. Correcto.")

# --- 3. cancelando del todo no se toca nada ---------------------------------
codigo, falso, guardado = correr(["n", "n"])
print("3. ordenes:", falso.resumen)
assert "escrito" not in guardado, "cancelado no deberia escribir la version"
assert not [o for o in falso.resumen if o.startswith(("commit", "push", "checkout"))]
print("   ni escribe, ni commitea, ni empuja. Correcto.")

# --- 4. ya en main ----------------------------------------------------------
codigo, falso, guardado = correr(["s"], rama="main")
print("4. ordenes:", falso.resumen)
assert falso.resumen[-1] == "push origin main", falso.resumen
assert "checkout main" not in falso.resumen, "ya estaba en main: no hace falta"
print("   empuja main directamente. Correcto.")

# --- 5. si main y la rama han divergido, no se deja a medias ----------------
codigo, falso, guardado = correr([""], falla_merge=True)
print("5. ordenes:", falso.resumen)
assert "checkout itunes-sync" == falso.resumen[-1], "debe devolverte a tu rama"
assert "push origin main" not in falso.resumen, "no debe empujar un merge fallido"
assert isinstance(codigo, SystemExit) and "caminos distintos" in str(codigo)
print("   avisa, no empuja y te devuelve a tu rama. Correcto.")

print()
print("PUBLICAR OK")
