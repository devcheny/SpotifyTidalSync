"""Punto de entrada.

  python main.py            -> interfaz grafica
  python main.py --sync     -> sincroniza y sale (lo que usa la tarea programada)
  python main.py --itunes   -> solo vuelca las playlists de TIDAL en iTunes
  python main.py --itunes --playlist "Mi lista"  -> solo esa playlist
  python main.py --status   -> muestra el estado actual
  python main.py --schedule 03:00 / --unschedule
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from stsync import scheduler
from stsync.config import Config
from stsync.http import ApiError
from stsync.oauth import OAuthError
from stsync.paths import app_dir, log_file
from stsync.store import StateStore, TokenStore
from stsync.sync import SyncEngine

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


def show_status() -> int:
    cfg, tokens, state = Config.load(), TokenStore(), StateStore()
    names = state.data.get("names", {})
    print(f"Carpeta de datos : {app_dir()}")
    print(f"Spotify          : "
          f"{'conectado ' + names.get('spotify', '') if tokens.has('spotify') else 'SIN CONECTAR'}")
    print(f"TIDAL            : "
          f"{'conectado ' + names.get('tidal', '') if tokens.has('tidal') else 'SIN CONECTAR'}")
    print(f"Direccion        : {cfg.direction}")
    print(f"Favoritos        : {'si' if cfg.sync_favorites else 'no'}")
    print(f"Playlists        : {'si' if cfg.sync_playlists else 'no'}")
    itunes = ("si, prefijo '%s'" % cfg.get("itunes_playlist_prefix", "")
              if cfg.get("itunes_enabled") else "no")
    print(f"iTunes           : {itunes}")
    print(f"Borrados         : {'se propagan' if cfg.propagate_deletions else 'no se propagan'}")
    print(f"Simulacion       : {'SI' if cfg.dry_run else 'no'}")
    print(f"Ultima sync      : {state.last_sync or 'nunca'}")
    print(f"Ultimo iTunes    : {state.data.get('last_itunes_sync', 'nunca')}")
    print(f"Ultimo resumen   : {state.data.get('last_summary', '-')}")
    print(f"Tarea programada : "
          f"{scheduler.task_info() if scheduler.task_exists() else 'no registrada'}")
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
    parser.add_argument("--status", action="store_true",
                        help="muestra el estado y sale")
    parser.add_argument("--schedule", metavar="HH:MM",
                        help="registra la tarea diaria a esa hora")
    parser.add_argument("--unschedule", action="store_true",
                        help="elimina la tarea programada")
    parser.add_argument("--dry-run", action="store_true",
                        help="fuerza el modo simulacion en esta ejecucion")
    args = parser.parse_args(argv)

    if args.dry_run:
        cfg = Config.load()
        cfg.set("dry_run", True)
        cfg.save()

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
    if args.itunes or args.playlist:
        return run_sync(itunes_only=True, playlist=args.playlist)
    if args.sync:
        return run_sync()

    from stsync.gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
