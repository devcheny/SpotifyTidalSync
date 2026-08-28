"""Motor de sincronizacion entre Spotify y TIDAL.

La identidad de una cancion es su ISRC (el codigo estandar de la industria);
si falta, se cae a una firma normalizada "artista|titulo". Eso evita duplicados
cuando las dos plataformas nombran la misma cancion de forma distinta.
"""
from __future__ import annotations

import csv
import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import Config
from .http import ApiError
from .model import Track, normalize
from .paths import reports_dir
from .spotify import SpotifyClient
from .store import StateStore, TokenStore
from .tidal import TidalClient

# Reintentar una busqueda fallida solo cada 30 dias (evita gastar cuota).
NEGATIVE_TTL = 30 * 24 * 3600


@dataclass
class Stats:
    added_to_spotify: int = 0
    added_to_tidal: int = 0
    removed_from_spotify: int = 0
    removed_from_tidal: int = 0
    playlists_created: int = 0
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Anadidas a Spotify: {self.added_to_spotify} | "
            f"Anadidas a TIDAL: {self.added_to_tidal} | "
            f"Quitadas de Spotify: {self.removed_from_spotify} | "
            f"Quitadas de TIDAL: {self.removed_from_tidal} | "
            f"Playlists creadas: {self.playlists_created} | "
            f"Sin equivalencia: {len(self.unmatched)}"
        )


class SyncEngine:
    def __init__(self, cfg: Config, log: Callable[[str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self.log = log or (lambda msg: print(msg))
        self.should_stop = should_stop or (lambda: False)
        self.tokens = TokenStore()
        self.state = StateStore()
        self.spotify = SpotifyClient(cfg, self.tokens, self.log)
        self.tidal = TidalClient(cfg, self.tokens, self.log)
        self.stats = Stats()

    # ---------------------------------------------------------------- publico
    def run(self) -> Stats:
        started = time.time()
        self.log("=" * 62)
        self.log(f"Sincronizacion iniciada {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
        if self.cfg.dry_run:
            self.log("MODO SIMULACION: no se escribira nada en las cuentas")

        if not self.tokens.has("spotify") or not self.tokens.has("tidal"):
            raise ApiError("Faltan cuentas por conectar. Abre la interfaz y "
                           "conecta Spotify y TIDAL.")

        self.log(f"Spotify: {self.spotify.display_name}")
        self.log(f"TIDAL:   {self.tidal.display_name}")

        if self.cfg.sync_favorites:
            self._sync_favorites()
        if self.cfg.sync_playlists and not self.should_stop():
            self._sync_playlists()

        self.state.last_sync = dt.datetime.now().isoformat(timespec="seconds")
        self.state.data["last_summary"] = self.stats.summary()
        self.state.save()

        self._write_unmatched_report()
        self.log("-" * 62)
        self.log(self.stats.summary())
        self.log(f"Terminado en {time.time() - started:.1f}s")
        return self.stats

    # -------------------------------------------------------------- favoritos
    def _sync_favorites(self) -> None:
        self.log("")
        self.log("== Favoritos ==")
        sp_tracks = self.spotify.saved_tracks()
        td_tracks = self.tidal.favorite_tracks()

        plan = self._plan(sp_tracks, td_tracks, "fav")
        self._apply(
            plan,
            add_spotify=self.spotify.add_saved,
            add_tidal=self.tidal.add_favorites,
            remove_spotify=self.spotify.remove_saved,
            remove_tidal=self.tidal.remove_favorites,
            label="favoritos",
        )

    # -------------------------------------------------------------- playlists
    def _sync_playlists(self) -> None:
        self.log("")
        self.log("== Playlists ==")
        sp_lists = {_pl_key(p.get("name", "")): p for p in self.spotify.my_playlists()}
        td_raw = self.tidal.my_playlists()
        td_lists = {
            _pl_key((p.get("attributes") or {}).get("name", "")): p for p in td_raw
        }
        self.log(f"  Spotify: {len(sp_lists)} playlists propias | "
                 f"TIDAL: {len(td_lists)} playlists propias")

        names = set(sp_lists) | set(td_lists)
        for name in sorted(names):
            if self.should_stop():
                return
            if not self._playlist_allowed(name):
                continue

            sp_pl, td_pl = sp_lists.get(name), td_lists.get(name)
            display = (sp_pl or {}).get("name") or \
                      ((td_pl or {}).get("attributes") or {}).get("name") or name

            try:
                self._sync_one_playlist(display, name, sp_pl, td_pl)
            except ApiError as exc:
                msg = f"  ! playlist '{display}': {exc}"
                self.log(msg)
                self.stats.errors.append(msg)

    def _sync_one_playlist(self, display: str, key: str,
                           sp_pl: dict[str, Any] | None,
                           td_pl: dict[str, Any] | None) -> None:
        direction = self.cfg.direction
        target_name = f"{self.cfg.playlist_prefix}{display}"

        # Crear la playlist que falte en el otro lado.
        if sp_pl and not td_pl:
            if direction == "tidal_to_spotify":
                return
            self.log(f"  + creando en TIDAL: {target_name}")
            td_pl = self.tidal.create_playlist(target_name, "Sincronizada desde Spotify")
            self.stats.playlists_created += 1
        elif td_pl and not sp_pl:
            if direction == "spotify_to_tidal":
                return
            self.log(f"  + creando en Spotify: {target_name}")
            sp_pl = self.spotify.create_playlist(target_name, "Sincronizada desde TIDAL")
            self.stats.playlists_created += 1

        if not sp_pl or not td_pl:
            return
        sp_id, td_id = sp_pl.get("id"), td_pl.get("id")
        if not sp_id or not td_id or sp_id == "dry-run" or td_id == "dry-run":
            return

        sp_tracks = self.spotify.playlist_tracks(sp_id)
        td_tracks = self.tidal.playlist_tracks(td_id)
        plan = self._plan(sp_tracks, td_tracks, f"pl:{key}")
        if not (plan.to_spotify or plan.to_tidal or
                plan.remove_spotify or plan.remove_tidal):
            return

        self.log(f"  ~ {display}: Spotify {len(sp_tracks)} / TIDAL {len(td_tracks)}")
        self._apply(
            plan,
            add_spotify=lambda ids: self.spotify.add_to_playlist(sp_id, ids),
            add_tidal=lambda ids: self.tidal.add_to_playlist(td_id, ids),
            remove_spotify=lambda ids: self.spotify.remove_from_playlist(sp_id, ids),
            remove_tidal=lambda ids: self.tidal.remove_from_playlist(td_id, ids),
            label=display,
        )

    def _playlist_allowed(self, key: str) -> bool:
        include = [_pl_key(n) for n in self.cfg.get("playlist_include", []) or []]
        exclude = [_pl_key(n) for n in self.cfg.get("playlist_exclude", []) or []]
        if include and key not in include:
            return False
        return key not in exclude

    # ------------------------------------------------------------ diferencias
    def _plan(self, sp_tracks: list[Track], td_tracks: list[Track],
              state_key: str) -> "Plan":
        sp_index = {t.key: t for t in sp_tracks}
        td_index = {t.key: t for t in td_tracks}

        prev_sp = self.state.snapshot(f"{state_key}:spotify")
        prev_td = self.state.snapshot(f"{state_key}:tidal")

        gone_sp: set[str] = set()
        gone_td: set[str] = set()
        if self.cfg.propagate_deletions and (prev_sp or prev_td):
            gone_sp = prev_sp - set(sp_index)   # borradas en Spotify
            gone_td = prev_td - set(td_index)   # borradas en TIDAL

        direction = self.cfg.direction
        to_tidal, to_spotify = set(), set()
        if direction in ("both", "spotify_to_tidal"):
            to_tidal = set(sp_index) - set(td_index) - gone_td
        if direction in ("both", "tidal_to_spotify"):
            to_spotify = set(td_index) - set(sp_index) - gone_sp

        return Plan(
            sp_index=sp_index,
            td_index=td_index,
            to_spotify=to_spotify,
            to_tidal=to_tidal,
            remove_spotify=gone_td & set(sp_index) if direction != "spotify_to_tidal" else set(),
            remove_tidal=gone_sp & set(td_index) if direction != "tidal_to_spotify" else set(),
            state_key=state_key,
        )

    # -------------------------------------------------------------- ejecucion
    def _apply(self, plan: "Plan", add_spotify: Callable[[list[str]], None],
               add_tidal: Callable[[list[str]], None],
               remove_spotify: Callable[[list[str]], None],
               remove_tidal: Callable[[list[str]], None], label: str) -> None:
        # --- borrados (primero, para no re-anadir lo que se acaba de quitar) --
        if plan.remove_spotify:
            ids = [plan.sp_index[k].id for k in plan.remove_spotify]
            self.log(f"  - quitando {len(ids)} de Spotify ({label})")
            remove_spotify(ids)
            self.stats.removed_from_spotify += len(ids)
        if plan.remove_tidal:
            ids = [plan.td_index[k].id for k in plan.remove_tidal]
            self.log(f"  - quitando {len(ids)} de TIDAL ({label})")
            remove_tidal(ids)
            self.stats.removed_from_tidal += len(ids)

        # --- altas -----------------------------------------------------------
        added_td_keys: set[str] = set()
        if plan.to_tidal:
            self.log(f"  > buscando {len(plan.to_tidal)} canciones en TIDAL...")
            ids, matched = self._resolve(
                [plan.sp_index[k] for k in sorted(plan.to_tidal)], self.tidal)
            if ids:
                add_tidal(ids)
                self.stats.added_to_tidal += len(ids)
                self.log(f"  + {len(ids)} anadidas a TIDAL ({label})")
            added_td_keys = matched

        added_sp_keys: set[str] = set()
        if plan.to_spotify:
            self.log(f"  > buscando {len(plan.to_spotify)} canciones en Spotify...")
            ids, matched = self._resolve(
                [plan.td_index[k] for k in sorted(plan.to_spotify)], self.spotify)
            if ids:
                add_spotify(ids)
                self.stats.added_to_spotify += len(ids)
                self.log(f"  + {len(ids)} anadidas a Spotify ({label})")
            added_sp_keys = matched

        # --- nuevo snapshot ---------------------------------------------------
        final_sp = (set(plan.sp_index) - plan.remove_spotify) | added_sp_keys
        final_td = (set(plan.td_index) - plan.remove_tidal) | added_td_keys
        self.state.set_snapshot(f"{plan.state_key}:spotify", final_sp)
        self.state.set_snapshot(f"{plan.state_key}:tidal", final_td)
        self.state.save()

    # ------------------------------------------------------------ equivalencias
    def _resolve(self, tracks: list[Track],
                 target: SpotifyClient | TidalClient) -> tuple[list[str], set[str]]:
        """Devuelve (ids en el destino, claves resueltas)."""
        service = "tidal" if isinstance(target, TidalClient) else "spotify"
        cache: dict[str, Any] = self.state.data.setdefault("resolve", {}) \
                                               .setdefault(service, {})
        ids: list[str] = []
        matched: set[str] = set()

        for i, track in enumerate(tracks, 1):
            if self.should_stop():
                break
            if i % 25 == 0:
                self.log(f"    {i}/{len(tracks)}...")

            entry = cache.get(track.key)
            if isinstance(entry, dict):
                if entry.get("id"):
                    ids.append(entry["id"])
                    matched.add(track.key)
                    continue
                if time.time() - entry.get("ts", 0) < NEGATIVE_TTL:
                    self.stats.unmatched.append((service, str(track)))
                    continue  # ya sabemos que no esta, no gastamos otra busqueda

            found: Track | None = None
            if track.isrc:
                found = target.find_by_isrc(track.isrc)
            if found is None:
                found = target.find_by_text(track.title, track.artist)
                if found and found.text_key != track.text_key:
                    found = None  # el buscador devolvio otra cancion

            if found:
                cache[track.key] = {"id": found.id, "ts": time.time()}
                ids.append(found.id)
                matched.add(track.key)
            else:
                cache[track.key] = {"id": "", "ts": time.time()}
                self.stats.unmatched.append((service, str(track)))

        return ids, matched

    # ---------------------------------------------------------------- informe
    def _write_unmatched_report(self) -> None:
        if not self.stats.unmatched:
            return
        limit = int(self.cfg.get("max_unmatched_report", 500))
        path = reports_dir() / f"sin-equivalencia-{dt.date.today():%Y%m%d}.csv"
        try:
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle, delimiter=";")
                writer.writerow(["destino", "cancion"])
                writer.writerows(self.stats.unmatched[:limit])
            self.log(f"Canciones sin equivalencia listadas en: {path}")
        except OSError as exc:
            self.log(f"No se pudo escribir el informe: {exc}")


@dataclass
class Plan:
    sp_index: dict[str, Track]
    td_index: dict[str, Track]
    to_spotify: set[str]
    to_tidal: set[str]
    remove_spotify: set[str]
    remove_tidal: set[str]
    state_key: str


def _pl_key(name: str) -> str:
    return normalize(name)
