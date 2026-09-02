"""Punto de entrada.

  python main.py            -> interfaz grafica
  python main.py --sync     -> sincroniza y sale (lo que usa la tarea programada)
  python main.py --itunes   -> solo vuelca las playlists de TIDAL en iTunes
  python main.py --flac2alac -> convierte a ALAC los FLAC de la carpeta de iTunes
  python main.py --itunes --playlist "Mi lista"  -> solo esa playlist
  python main.py --buscar "hay que venir"  -> por que no casa esa cancion
  python main.py --version  -> version instalada y si hay una mas nueva
  python main.py --actualizar -> se baja la ultima release de GitHub
  python main.py --biblioteca -> repasa el volumen de toda la biblioteca
  python main.py --status   -> muestra el estado actual
  python main.py --schedule 03:00 / --unschedule
  python main.py --schedule-flac 04:00 / --unschedule-flac
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from stsync import scheduler
from stsync.config import Config
from stsync.convert import ConvertError, FlacConverter
from stsync.http import ApiError
from stsync.itunes import ITunesError, inspect_track
from stsync.normalize import normalize_library
from stsync.oauth import OAuthError
from stsync.paths import app_dir, log_file
from stsync.store import StateStore, TokenStore
from stsync.sync import PASOS, SyncEngine
from stsync.updates import UpdateError, apply_release, check, current_version

MAX_LOG_BYTES = 2 * 1024 * 1024


def _rotate_log() -> None:
    path = log_file()
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_suffix(".1.log"))


def _file_logger():
    _rotate_log()
    handle = open(log_file(), "a", encoding="utf-8")

    def log(message: str) -> None:
        line = f"{dt.datetime.now():%H:%M:%S} {message}"
        handle.write(line + "\n")
        handle.flush()
        try:
            print(line)
        except (OSError, ValueError):
            pass  # sin consola (pythonw): basta con el fichero

    return log, handle


def run_sync(itunes_only: bool = False, playlist: str | None = None) -> int:
    log, handle = _file_logger()
    try:
        engine = SyncEngine(Config.load(), log)
        if itunes_only:
            engine.run_itunes(playlist)
        else:
            engine.run()
        return 0
    except (ApiError, OAuthError) as exc:
        log(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR inesperado: {exc}")
        return 2
    finally:
        handle.close()


def _process_user() -> str:
    """Con que cuenta corre esto: si no es la de tu sesion, el Explorador no
    podra abrir la carpeta de datos aunque Python si escriba en ella."""
    import ctypes
    import getpass
    try:
        elevado = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        elevado = False
    return f"{getpass.getuser()}{' (como administrador)' if elevado else ''}"


def _data_dir_state() -> str:
    """Comprueba de verdad la carpeta de datos: %APPDATA% cambia con el usuario."""
    folder = app_dir()
    probe = folder / ".prueba-escritura"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"NO SE PUEDE ESCRIBIR: {exc}"
    reports = folder / "reports"
    informes = (len(list(reports.glob("sin-equivalencia-*.csv")))
                if reports.is_dir() else 0)
    return f"correcta, {informes} informe{'s' if informes != 1 else ''}"


def run_flac2alac() -> int:
    log, handle = _file_logger()
    try:
        FlacConverter(Config.load(), log).run()
        return 0
    except ConvertError as exc:
        log(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR inesperado: {exc}")
        return 2
    finally:
        handle.close()


def run_library() -> int:
    log, handle = _file_logger()
    try:
        normalize_library(Config.load(), log)
        return 0
    except (ConvertError, ITunesError) as exc:
        log(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR inesperado: {exc}")
        return 2
    finally:
        handle.close()


def run_inspect(query: str) -> int:
    """Compara una cancion en iTunes y en TIDAL para ver por que no casa."""
    from stsync.store import TokenStore as _Tokens
    from stsync.tidal import TidalClient
    cfg, tokens = Config.load(), _Tokens()
    if not tokens.has("tidal"):
        print("Conecta TIDAL antes de buscar.")
        return 1
    try:
        inspect_track(cfg, TidalClient(cfg, tokens, print), query, print)
        return 0
    except (ITunesError, ApiError) as exc:
        print(f"ERROR: {exc}")
        return 1


def run_update(aplicar: bool) -> int:
    """Mira si hay version nueva en GitHub y, si se pide, la instala."""
    cfg = Config.load()
    print(f"Version instalada : {current_version()}")
    repo = cfg.repo()
    try:
        hay, release = check(repo)
    except UpdateError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Ultima publicada  : {release.version}")
    if not hay:
        print("Ya tienes la ultima version.")
        return 0
    if not aplicar:
        print("Hay una version nueva. Para instalarla:")
        print("   actualizar.bat   (o python main.py --actualizar)")
        return 0

    try:
        apply_release(release, print)
    except UpdateError as exc:
        print(f"ERROR: {exc}")
        print("No se ha tocado nada: sigues con la version de antes.")
        return 1
    print(f"Actualizado a {release.version}. Cierra y vuelve a abrir la aplicacion.")
    return 0


def show_status() -> int:
    cfg, tokens, state = Config.load(), TokenStore(), StateStore()
    names = state.data.get("names", {})
    print(f"Version          : {current_version()}")
    print(f"Usuario          : {_process_user()}")
    print(f"Carpeta de datos : {app_dir()}")
    print(f"                   {_data_dir_state()}")
    print(f"Spotify          : "
          f"{'conectado ' + names.get('spotify', '') if tokens.has('spotify') else 'SIN CONECTAR'}")
    print(f"TIDAL            : "
          f"{'conectado ' + names.get('tidal', '') if tokens.has('tidal') else 'SIN CONECTAR'}")
    print(f"Direccion        : {cfg.direction}")
    print(f"Favoritos        : {'si' if cfg.sync_favorites else 'no'}")
    print(f"Playlists        : {'si' if cfg.sync_playlists else 'no'}")
    print(f"Borrados         : {'se propagan' if cfg.propagate_deletions else 'no se propagan'}")
    print(f"Simulacion       : {'SI' if cfg.dry_run else 'no'}")
    print(f"Ultima sync      : {state.last_sync or 'nunca'}")
    print(f"Ultimo iTunes    : {state.data.get('last_itunes_sync', 'nunca')}")
    print(f"Ultimo resumen   : {state.data.get('last_summary', '-')}")
    print(f"Tarea de sync    : "
          f"{scheduler.task_info() if scheduler.task_exists() else 'no registrada'}")
    flac_task = scheduler.FLAC
    print(f"Tarea de FLAC    : "
          f"{scheduler.task_info(flac_task) if scheduler.task_exists(flac_task) else 'no registrada'}")

    # Lo que de verdad hace la tarea diaria, en su orden. Se lee de la misma
    # lista que recorre el motor, asi que no puede quedarse desfasado.
    print()
    print("Cola de cada sincronizacion (y de la tarea de cada 24 h):")
    print(f"  [{'x' if cfg.sync_favorites else ' '}] Favoritos entre Spotify y TIDAL")
    print(f"  [{'x' if cfg.sync_playlists else ' '}] Playlists entre Spotify y TIDAL")
    for paso in PASOS:
        print(f"  [{'x' if cfg.get(paso.clave) else ' '}] {paso.nombre}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spotify-tidal-sync",
        description="Mantiene sincronizados favoritos y playlists de Spotify y TIDAL.",
    )
    parser.add_argument("--sync", action="store_true",
                        help="sincroniza una vez y sale")
    parser.add_argument("--itunes", action="store_true",
                        help="vuelca las playlists de TIDAL en iTunes y sale")
    parser.add_argument("--playlist", metavar="NOMBRE",
                        help="con --itunes, sincroniza solo esa playlist de TIDAL")
    parser.add_argument("--flac2alac", action="store_true",
                        help="convierte a ALAC los FLAC de la carpeta de iTunes")
    parser.add_argument("--buscar", metavar="TEXTO",
                        help="ensena como ve esa cancion en iTunes y en TIDAL")
    parser.add_argument("--version", action="store_true",
                        help="version instalada y si hay una mas nueva")
    parser.add_argument("--actualizar", action="store_true",
                        help="instala la ultima release de GitHub")
    parser.add_argument("--biblioteca", action="store_true",
                        help="repasa el volumen de toda la biblioteca")
    parser.add_argument("--status", action="store_true",
                        help="muestra el estado y sale")
    parser.add_argument("--schedule", metavar="HH:MM",
                        help="registra la tarea diaria a esa hora")
    parser.add_argument("--unschedule", action="store_true",
                        help="elimina la tarea programada")
    parser.add_argument("--schedule-flac", metavar="HH:MM",
                        help="registra el repaso diario de FLAC a esa hora")
    parser.add_argument("--unschedule-flac", action="store_true",
                        help="elimina el repaso diario de FLAC")
    parser.add_argument("--dry-run", action="store_true",
                        help="fuerza el modo simulacion en esta ejecucion")
    args = parser.parse_args(argv)

    if args.dry_run:
        cfg = Config.load()
        cfg.set("dry_run", True)
        cfg.save()

    if args.version or args.actualizar:
        return run_update(aplicar=args.actualizar)
    if args.buscar:
        return run_inspect(args.buscar)
    if args.status:
        return show_status()
    if args.schedule:
        ok, msg = scheduler.create_task(args.schedule)
        print(msg)
        return 0 if ok else 1
    if args.unschedule:
        ok, msg = scheduler.delete_task()
        print(msg)
        return 0 if ok else 1
    if args.schedule_flac:
        ok, msg = scheduler.create_task(args.schedule_flac, scheduler.FLAC)
        print(msg)
        return 0 if ok else 1
    if args.unschedule_flac:
        ok, msg = scheduler.delete_task(scheduler.FLAC)
        print(msg)
        return 0 if ok else 1
    if args.flac2alac:
        return run_flac2alac()
    if args.biblioteca:
        return run_library()
    if args.itunes or args.playlist:
        return run_sync(itunes_only=True, playlist=args.playlist)
    if args.sync:
        return run_sync()

    from stsync.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
