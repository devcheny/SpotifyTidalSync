"""Prueba del convertidor FLAC -> ALAC con un ffmpeg simulado."""
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stsync.config import Config
from stsync.convert import DONE_DIR, ConvertError, FlacConverter

AQUI = Path(__file__).parent
FFMPEG = str(AQUI / "ffmpeg-falso.bat")
RAIZ = AQUI / "prueba-flac"


def montar() -> Path:
    """Reproduce lo que hace iTunes: los FLAC caen en 'No anadido/<fecha>'."""
    if RAIZ.exists():
        shutil.rmtree(RAIZ)
    folder = RAIZ / "Anadir automaticamente a iTunes"
    rechazados = folder / "No anadido"
    (rechazados / "2026-08-28 16.20.32").mkdir(parents=True)
    (rechazados / "2026-08-27 10.00.00").mkdir(parents=True)

    (rechazados / "2026-08-28 16.20.32" / "Xiyo - Do You Remember.flac").write_bytes(b"flac1")
    (rechazados / "2026-08-27 10.00.00" / "Xiyo - Do You Remember.flac").write_bytes(b"flac2")
    (rechazados / "2026-08-27 10.00.00" / "Otra cancion.flac").write_bytes(b"flac3")
    # Ya convertido antes: no se debe machacar.
    (folder / "Xiyo - Do You Remember.m4a").write_bytes(b"ya estaba")
    # Otros formatos: no se tocan.
    (folder / "algo.mp3").write_bytes(b"mp3")
    return folder


def config(folder: Path, **extra) -> Config:
    cfg = Config(dict(Config().data))
    cfg.set("flac_folder", str(folder))
    cfg.set("ffmpeg_path", FFMPEG)
    for key, value in extra.items():
        cfg.set(key, value)
    return cfg


def correr(nombre, folder, cfg):
    lineas = []
    stats = FlacConverter(cfg, lineas.append).run()
    print(f"===== {nombre} =====")
    print("\n".join(lineas))
    print("->", stats.summary())
    return stats


def contenido(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)).replace("\\", "/")
                  for p in folder.rglob("*"))


# --- 1. Ejecucion normal ----------------------------------------------------
folder = montar()
stats = correr("normal", folder, config(folder))
print("queda:", contenido(folder), "\n")
assert stats.converted == 3, stats.converted
assert not stats.failed, stats.failed
hay = contenido(folder)
assert "Xiyo - Do You Remember.m4a" in hay          # el de antes, intacto
assert "Xiyo - Do You Remember (2).m4a" in hay      # no lo machaca
assert "Xiyo - Do You Remember (3).m4a" in hay
assert "Otra cancion.m4a" in hay
assert "algo.mp3" in hay                            # no se toca
assert not [p for p in hay if p.endswith(".flac")], "quedaron FLAC sin borrar"
assert (folder / "Xiyo - Do You Remember.m4a").read_bytes() == b"ya estaba"
assert "No anadido" not in hay, "las carpetas vacias deberian irse"

# --- 2. Sin borrar el original ---------------------------------------------
folder = montar()
stats = correr("mover en vez de borrar", folder, config(folder, flac_delete_source=False))
hay = contenido(folder)
print("queda:", hay, "\n")
guardados = [p for p in hay if p.startswith(DONE_DIR) and p.endswith(".flac")]
assert len(guardados) == 3, guardados
assert DONE_DIR + "/Xiyo - Do You Remember (2).flac" in hay, hay

# --- 3. ffmpeg rechaza la caratula -> reintento sin ella --------------------
folder = montar()
os.environ["FALLA_CARATULA"] = "1"
stats = correr("caratula rechazada", folder, config(folder))
del os.environ["FALLA_CARATULA"]
print()
assert stats.converted == 3, stats.converted
assert not stats.failed, stats.failed

# --- 4. ffmpeg falla del todo ----------------------------------------------
folder = montar()
os.environ["FALLA_TODO"] = "1"
stats = correr("ffmpeg falla", folder, config(folder))
del os.environ["FALLA_TODO"]
hay = contenido(folder)
print("queda:", hay, "\n")
assert stats.converted == 0, stats.converted
assert len(stats.failed) == 3, stats.failed
assert len([p for p in hay if p.endswith(".flac")]) == 3, "no debe borrar si fallo"
assert len([p for p in hay if p.endswith(".m4a")]) == 1, "no debe dejar restos"

# --- 5. Simulacion ----------------------------------------------------------
folder = montar()
antes = contenido(folder)
stats = correr("simulacion", folder, config(folder, dry_run=True))
print()
assert contenido(folder) == antes, "la simulacion no debe tocar nada"
assert stats.converted == 3, stats.converted

# --- 6. Sin ffmpeg y carpeta inexistente ------------------------------------
for nombre, cfg in (("sin ffmpeg", config(folder, ffmpeg_path=str(AQUI / "no-existe.exe"))),
                    ("carpeta mala", config(Path(AQUI / "no-hay-nada")))):
    try:
        FlacConverter(cfg, lambda m: None).run()
        raise SystemExit(f"ERROR: {nombre} deberia haber fallado")
    except ConvertError as exc:
        print(f"{nombre}: {exc}")

shutil.rmtree(RAIZ)
print()
print("TODOS LOS ESCENARIOS OK")
