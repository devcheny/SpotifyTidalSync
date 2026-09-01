"""Cliente de la Web API oficial de Spotify."""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from .config import Config
from .http import ApiError, HttpClient
from .model import Track
from .oauth import OAuthEndpoints, authorize, refresh
from .store import TokenStore

API = "https://api.spotify.com/v1"
ENDPOINTS = OAuthEndpoints(
    authorize_url="https://accounts.spotify.com/authorize",
    token_url="https://accounts.spotify.com/api/token",
)
SCOPES = [
    "user-library-read",
    "user-library-modify",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
]
SERVICE = "spotify"


class SpotifyClient:
    def __init__(self, cfg: Config, tokens: TokenStore,
                 log: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.tokens = tokens
        self.log = log or (lambda _m: None)
        self.http = HttpClient(self.log)
        self._user: dict[str, Any] | None = None

    # -- autenticacion ------------------------------------------------------
    def login(self) -> None:
        token = authorize(
            ENDPOINTS,
            self.cfg.spotify_client_id,
            self.cfg.spotify_redirect_uri,
            SCOPES,
            extra_auth_params={"show_dialog": "true"},
        )
        self.tokens.save(SERVICE, token)

    def logout(self) -> None:
        self.tokens.clear(SERVICE)
        self._user = None

    def _access_token(self) -> str:
        token = self.tokens.get(SERVICE)
        if not token:
            raise ApiError("Spotify: no has iniciado sesion.")
        if token.get("expires_at", 0) <= time.time():
            if not token.get("refresh_token"):
                raise ApiError("Spotify: sesion caducada, vuelve a conectar la cuenta.")
            self.log("  renovando token de Spotify...")
            new = refresh(ENDPOINTS, self.cfg.spotify_client_id, token["refresh_token"])
            self.tokens.save(SERVICE, new)
            token = self.tokens.get(SERVICE) or {}
        return token["access_token"]

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{API}{path}"
        return self.http.request("GET", url, headers=self._headers(), params=params)

    def _write(self, method: str, path: str, body: Any = None,
               params: dict[str, Any] | None = None) -> Any:
        if self.cfg.dry_run:
            self.log(f"    [dry-run] {method} {path} {str(body)[:120]}")
            return None
        return self.http.request(method, f"{API}{path}", headers=self._headers(),
                                 json_body=body, params=params)

    # -- usuario ------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        if self._user is None:
            self._user = self._get("/me")
        return self._user

    @property
    def display_name(self) -> str:
        user = self.me()
        return user.get("display_name") or user.get("id") or "?"

    # -- favoritos ----------------------------------------------------------
    def saved_tracks(self) -> list[Track]:
        out: list[Track] = []
        url: str | None = "/me/tracks"
        params: dict[str, Any] | None = {"limit": 50, "market": "from_token"}
        while url:
            page = self._get(url, params)
            params = None  # el campo 'next' ya trae la query completa
            for item in page.get("items", []):
                track = _to_track(item.get("track"))
                if track:
                    out.append(track)
            url = page.get("next")
        self.log(f"  Spotify: {len(out)} favoritos leidos")
        return out

    def add_saved(self, ids: list[str]) -> None:
        for chunk in _chunks(ids, 50):
            self._write("PUT", "/me/tracks", {"ids": chunk})

    def remove_saved(self, ids: list[str]) -> None:
        for chunk in _chunks(ids, 50):
            self._write("DELETE", "/me/tracks", {"ids": chunk})

    # -- playlists ----------------------------------------------------------
    def my_playlists(self) -> list[dict[str, Any]]:
        me_id = self.me()["id"]
        out: list[dict[str, Any]] = []
        url: str | None = "/me/playlists"
        params: dict[str, Any] | None = {"limit": 50}
        while url:
            page = self._get(url, params)
            params = None
            for playlist in page.get("items", []):
                if playlist and (playlist.get("owner") or {}).get("id") == me_id:
                    out.append(playlist)
            url = page.get("next")
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        fields = ("next,items(track(id,name,album(name),artists(name),"
                  "duration_ms,external_ids(isrc),is_local,type))")
        out: list[Track] = []
        url: str | None = f"/playlists/{playlist_id}/tracks"
        params: dict[str, Any] | None = {
            "limit": 100, "market": "from_token", "fields": fields,
        }
        while url:
            page = self._get(url, params)
            params = None
            for item in page.get("items", []):
                track = _to_track(item.get("track"))
                if track:
                    out.append(track)
            url = page.get("next")
        return out

    def create_playlist(self, name: str, description: str = "",
                        publica: bool = False) -> dict[str, Any]:
        if self.cfg.dry_run:
            self.log(f"    [dry-run] crearia la playlist de Spotify: {name}")
            return {"id": "dry-run", "name": name}
        return self.http.request(
            "POST", f"{API}/users/{self.me()['id']}/playlists",
            headers=self._headers(),
            json_body={"name": name, "description": description[:300],
                       "public": publica},
        )

    def add_to_playlist(self, playlist_id: str, ids: list[str]) -> None:
        for chunk in _chunks(ids, 100):
            self._write("POST", f"/playlists/{playlist_id}/tracks",
                        {"uris": [f"spotify:track:{i}" for i in chunk]})

    def remove_from_playlist(self, playlist_id: str, ids: list[str]) -> None:
        for chunk in _chunks(ids, 100):
            self._write("DELETE", f"/playlists/{playlist_id}/tracks",
                        {"tracks": [{"uri": f"spotify:track:{i}"} for i in chunk]})

    # -- busqueda -----------------------------------------------------------
    def find_by_isrc(self, isrc: str) -> Track | None:
        try:
            data = self._get("/search", {"q": f"isrc:{isrc}", "type": "track", "limit": 1})
        except ApiError:
            return None
        items = ((data or {}).get("tracks") or {}).get("items") or []
        return _to_track(items[0]) if items else None

    def find_by_text(self, title: str, artist: str) -> Track | None:
        query = f'track:"{_clean(title)}" artist:"{_clean(artist)}"'
        try:
            data = self._get("/search", {"q": query, "type": "track", "limit": 5})
        except ApiError:
            return None
        for item in ((data or {}).get("tracks") or {}).get("items") or []:
            track = _to_track(item)
            if track:
                return track
        return None


# --------------------------------------------------------------------------
def _to_track(raw: dict[str, Any] | None) -> Track | None:
    if not raw or raw.get("is_local") or not raw.get("id"):
        return None
    if raw.get("type") not in (None, "track"):
        return None
    artists = [a.get("name", "") for a in raw.get("artists") or []]
    return Track(
        service=SERVICE,
        id=raw["id"],
        title=raw.get("name", ""),
        artist=artists[0] if artists else "",
        # Todos, no solo el principal: es lo que hace falta para completar en
        # iTunes una cancion que alli figura a nombre de uno solo.
        artists=tuple(a for a in artists if a),
        album=(raw.get("album") or {}).get("name", ""),
        isrc=(raw.get("external_ids") or {}).get("isrc", "") or "",
        duration_ms=int(raw.get("duration_ms") or 0),
    )


def _clean(text: str) -> str:
    return text.replace('"', " ").strip()


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
