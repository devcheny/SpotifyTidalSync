"""Sube el numero de version, lo commitea y lo empuja a GitHub.

De publicar la release se encarga solo el proyecto: al llegar el push a main,
.github/workflows/publicar.yml ve que la version ha cambiado y la publica con
sus notas. Aqui solo se cambia el numero.

  python publicar.py parche   1.1.0 -> 1.1.1   (arreglos)
  python publicar.py menor    1.1.0 -> 1.2.0   (cosas nuevas)
  python publicar.py mayor    1.1.0 -> 2.0.0   (cambios gordos)
  python publicar.py 1.5.2    ese numero exacto
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
INIT = RAIZ / "stsync" / "__init__.py"
PATRON = re.compile(r'(__version__\s*=\s*")([^"]+)(")')


def git(*args: str, capturar: bool = True) -> str:
    salida = subprocess.run(["git", *args], cwd=RAIZ, capture_output=capturar,
                            text=True, encoding="utf-8", errors="replace")
    if salida.returncode != 0:
        raise SystemExit(f"ERROR en 'git {' '.join(args)}':\n"
                         f"{(salida.stderr or salida.stdout or '').strip()}")
    return (salida.stdout or "").strip()


def version_actual() -> str:
    encontrado = PATRON.search(INIT.read_text(encoding="utf-8"))
    if not encontrado:
        raise SystemExit(f"No se encuentra __version__ en {INIT}")
    return encontrado.group(2)


def siguiente(actual: str, salto: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", salto):
        return salto            # nos han dado el numero exacto
    numeros = [int(x) for x in actual.split(".")] + [0, 0, 0]
    mayor, menor, parche = numeros[:3]
    if salto == "mayor":
        return f"{mayor + 1}.0.0"
    if salto == "menor":
        return f"{mayor}.{menor + 1}.0"
    if salto == "parche":
        return f"{mayor}.{menor}.{parche + 1}"
    raise SystemExit(f"No entiendo '{salto}'. Usa parche, menor, mayor o 1.2.3")


def main() -> int:
    salto = sys.argv[1] if len(sys.argv) > 1 else "parche"
    actual = version_actual()
    nueva = siguiente(actual, salto)
    if nueva == actual:
        raise SystemExit(f"La version ya es {actual}: no hay nada que subir.")

    rama = git("rev-parse", "--abbrev-ref", "HEAD")
    pendiente = git("status", "--porcelain")
    print(f"Version   : {actual}  ->  {nueva}")
    print(f"Rama      : {rama}")
    if pendiente:
        print("AVISO: hay cambios sin commitear; se subira solo la version:")
        for linea in pendiente.splitlines()[:10]:
            print(f"   {linea}")

    # Fuera de main no se publica nada, porque el workflow solo escucha ahi.
    # En vez de dejarte los comandos escritos, se ofrece a hacerlo.
    por_main = rama == "main"
    if not por_main:
        print()
        print("=" * 62)
        print(f"Estas en '{rama}', y GitHub solo publica desde main.")
        print("=" * 62)
        print()
        respuesta = input(
            f"¿Llevo '{rama}' a main y publico desde alli? [S/n] ").strip().lower()
        if respuesta in ("", "s", "si", "y"):
            por_main = True
        elif input(f"Entonces subo la version en '{rama}' SIN publicar "
                   "release. ¿Seguimos? [s/N] ").strip().lower() \
                not in ("s", "si", "y"):
            print("Cancelado, no se ha tocado nada.")
            return 1
    elif input("Se hara commit y push a GitHub. ¿Seguimos? [s/N] ").strip().lower() \
            not in ("s", "si", "y"):
        print("Cancelado, no se ha tocado nada.")
        return 1

    INIT.write_text(
        PATRON.sub(lambda m: m.group(1) + nueva + m.group(3),
                   INIT.read_text(encoding="utf-8")),
        encoding="utf-8")
    git("add", str(INIT))
    git("commit", "-m", f"Version {nueva}")

    if por_main and rama != "main":
        _llevar_a_main(rama)
    else:
        git("push", "origin", rama)

    print()
    if por_main:
        print(f"Subida la version {nueva}. GitHub publicara la v{nueva} en un minuto;")
        print("puedes seguirlo en la pestana Actions del repositorio.")
    else:
        print(f"Version {nueva} subida en '{rama}'. NO se ha publicado release:")
        print("para publicarla, vuelve a lanzar esto desde main.")
    return 0


def _llevar_a_main(rama: str) -> None:
    """Mueve lo que hay en la rama a main y lo empuja, sin dejarte alli.

    Se vuelve a la rama de trabajo pase lo que pase: quedarse en main sin
    haberlo pedido es la clase de sorpresa que acaba en un commit perdido.
    """
    git("checkout", "main")
    try:
        git("merge", rama, "--ff-only")
        git("push", "origin", "main")
    except SystemExit:
        git("checkout", rama)
        raise SystemExit(
            f"main y '{rama}' han seguido caminos distintos, asi que no se puede\n"
            "juntar sin mirar. Hazlo a mano y vuelve a intentarlo:\n"
            f"   git checkout main && git merge {rama}\n"
            "La version ya esta subida en tu rama: no hace falta volver a subirla.")
    git("checkout", rama)
    print(f"Llevado a main y empujado; sigues en '{rama}'.")


if __name__ == "__main__":
    sys.exit(main())
