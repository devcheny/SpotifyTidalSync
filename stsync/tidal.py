"""Cliente de la API oficial de TIDAL (openapi.tidal.com/v2, formato JSON:API).

Endpoints usados (confirmados contra la especificacion OpenAPI publica):
  GET  /users/me
  GET  /userCollectionTracks/me/relationships/items
  POST /userCollectionTracks/me/relationships/items      (max 20 por peticion)
  DELETE /userCollectionTracks/me/relationships/items
  GET  /playlists?filter[owners.id]=me
  POST /playlists
  GET  /playlists/{id}/relationships/items
  POST /playlists/{id}/relationships/items               (max 20 por peticion)
  GET  /tracks?filter[isrc]=...
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

from .config import Config
from .http import ApiError, HttpClient
from .model import Track
from .oauth import OAuthEndpoints, authorize, refresh
from .store import TokenStore

API = "https://openapi.tidal.com/v2"
ENDPOINTS = OAuthEndpoints(
    authorize_url="https://login.tidal.com/authorize",
    token_url="https://auth.tidal.com/v1/oauth2/token",
)
SCOPES = [
    "user.read",
    "collection.read",
    "collection.write",
    "playlists.read",
    "playlists.write",
]
SERVICE = "tidal"
JSONAPI = "application/vnd.api+json"

# La API limita a 20 elementos por peticion de escritura en relaciones "to-many".
WRITE_BATCH = 20


class TidalClient:
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
            self.cfg.tidal_client_id,
            self.cfg.tidal_redirect_uri,
            SCOPES,
        )
        self.tokens.save(SERVICE, token)

    def logout(self) -> None:
        self.tokens.clear(SERVICE)
        self._user = None

    def _access_token(self) -> str:
        token = self.tokens.get(SERVICE)
        if not token:
            raise ApiError("TIDAL: no has iniciado sesion.")
        if token.get("expires_at", 0) <= time.time():
            if not token.get("refresh_token"):
                raise ApiError("TIDAL: sesion caducada, vuelve a conectar la cuenta.")
            self.log("  renovando token de TIDAL...")
            new = refresh(ENDPOINTS, self.cfg.tidal_client_id, token["refresh_token"])
            self.tokens.save(SERVICE, new)
            token = self.tokens.get(SERVICE) or {}
        return token["access_token"]

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Accept": JSONAPI,
            "Content-Type": JSONAPI,
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.http.request("GET", f"{API}{path}", headers=self._headers(),
                                 params=params)

    def _write(self, method: str, path: str, body: Any,
               params: dict[str, Any] | None = None) -> Any:
        if self.cfg.dry_run:
            self.log(f"    [dry-run] {method} {path} {str(body)[:120]}")
            return None
        return self.http.request(method, f"{API}{path}", headers=self._headers(),
                                 json_body=body, params=params)

    @property
    def country(self) -> str:
        return (self.cfg.country_code or "US").upper()

    # -- paginacion por cursor ---------------------------------------------
    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        params = dict(params)
        seen: set[str] = set()
        while True:
            page = self._get(path, params)
            if not page:
                return
            yield page

            cursor = _next_cursor(page)
            # Sin cursor nuevo (o repetido) se termina: evita bucles infinitos.
            if not cursor or cursor in seen:
                return
            seen.add(cursor)
            params["page[cursor]"] = cursor

    # -- usuario ------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        if self._user is None:
            data = self._get("/users/me")
            self._user = (data or {}).get("data") or {}
        return self._user

    @property
    def user_id(self) -> str:
        return str(self.me().get("id", "me"))

    @property
    def display_name(self) -> str:
        attrs = self.me().get("attributes") or {}
        return attrs.get("username") or attrs.get("email") or self.user_id

    # -- favoritos ----------------------------------------------------------
    def favorite_tracks(self) -> list[Track]:
        out: list[Track] = []
        params = {"countryCode": self.country, "include": "items", "locale": "en-US"}
        for page in self._paginate("/userCollectionTracks/me/relationships/items", params):
            for raw in page.get("included") or []:
                track = _to_track(raw)
                if track:
                    out.append(track)
        self.log(f"  TIDAL: {len(out)} favoritos leidos")
        return out

    def add_favorites(self, ids: list[str]) -> None:
        for chunk in _chunks(ids, WRITE_BATCH):
            self._write(
                "POST", "/userCollectionTracks/me/relationships/items",
                {"data": [{"id": i, "type": "tracks"} for i in chunk]},
                params={"countryCode": self.country},
            )

    def remove_favorites(self, ids: list[str]) -> None:
        for chunk in _chunks(ids, WRITE_BATCH):
            self._write(
                "DELETE", "/userCollectionTracks/me/relationships/items",
                {"data": [{"id": i, "type": "tracks"} for i in chunk]},
            )

    # -- playlists ----------------------------------------------------------
    def my_playlists(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        params = {"filter[owners.id]": "me", "countryCode": self.country}
        for page in self._paginate("/playlists", params):
            out.extend(page.get("data") or [])
        return out

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        out: list[Track] = []
        params = {"countryCode": self.country, "include": "items", "locale": "en-US"}
        for page in self._paginate(f"/playlists/{playlist_id}/relationships/items", params):
            for raw in page.get("included") or []:
                track = _to_track(raw)
                if track:
                    out.append(track)
        return out

    def create_playlist(self, name: str, description: str = "") -> dict[str, Any]:
        if self.cfg.dry_run:
            self.log(f"    [dry-run] crearia la playlist de TIDAL: {name}")
            return {"id": "dry-run", "attributes": {"name": name}}
        body = {
            "data": {
                "type": "playlists",
                "attributes": {
                    "name": name,
                    "description": description[:500],
                    "accessType": "UNLISTED",
                },
            }
        }
        resp = self._write("POST", "/playlists", body,
                           params={"countryCode": self.country})
        return (resp or {}).get("data") or {}

    def add_to_playlist(self, playlist_id: str, ids: list[str]) -> None:
        for chunk in _chunks(ids, WRITE_BATCH):
            self._write(
                "POST", f"/playlists/{playlist_id}/relationships/items",
                {"data": [{"id": i, "type": "tracks"} for i in chunk]},
                params={"countryCode": self.country},
            )

    def remove_from_playlist(self, playlist_id: str, ids: list[str]) -> None:
        for chunk in _chunks(ids, WRITE_BATCH):
            self._write(
                "DELETE", f"/playlists/{playlist_id}/relationships/items",
                {"data": [{"id": i, "type": "tracks"} for i in chunk]},
            )

    # -- busqueda -----------------------------------------------------------
    def find_by_isrc(self, isrc: str) -> Track | None:
        try:
            data = self._get("/tracks", {"filter[isrc]": isrc,
                                         "countryCode": self.country})
        except ApiError:
            return None
        for raw in (data or {}).get("data") or []:
            track = _to_track(raw)
            if track:
                return track
        return None

    def find_by_text(self, title: str, artist: str) -> Track | None:
        """TIDAL no expone busqueda libre estable en v2: sin ISRC no hay match."""
        return None


# --------------------------------------------------------------------------
def _to_track(raw: dict[str, Any] | None) -> Track | None:
    if not raw or raw.get("type") != "tracks" or not raw.get("id"):
        return None
    attrs = raw.get("attributes") or {}
    isrc = attrs.get("isrc") or ""
    external = attrs.get("externalLinks") or []
    if not isrc and isinstance(external, list):
        for link in external:
            if isinstance(link, dict) and link.get("meta", {}).get("type") == "ISRC":
                isrc = link.get("href", "")
                break
    return Track(
        service=SERVICE,
        id=str(raw["id"]),
        title=attrs.get("title", ""),
        artist=_first_artist(raw),
        album="",
        isrc=isrc,
        duration_ms=_duration_ms(attrs.get("duration")),
    )


def _first_artist(raw: dict[str, Any]) -> str:
    """El nombre del artista solo llega si se pidio include=artists; si no, vacio."""
    rel = ((raw.get("relationships") or {}).get("artists") or {}).get("data") or []
    if rel and isinstance(rel[0], dict):
        return str(rel[0].get("meta", {}).get("name", "") or "")
    return ""


def _duration_ms(value: Any) -> int:
    """La duracion llega como ISO-8601 (PT3M21S) o como segundos."""
    if isinstance(value, (int, float)):
        return int(value * 1000)
    if not isinstance(value, str) or not value.startswith("PT"):
        return 0
    total, number = 0.0, ""
    for char in value[2:]:
        if char.isdigit() or char == ".":
            number += char
        else:
            factor = {"H": 3600, "M": 60, "S": 1}.get(char.upper(), 0)
            total += float(number or 0) * factor
            number = ""
    return int(total * 1000)


def _next_cursor(page: dict[str, Any]) -> str | None:
    """El cursor viaja en meta.nextCursor o dentro del enlace links.next."""
    for container in (page.get("meta"), (page.get("links") or {}).get("meta")):
        if isinstance(container, dict) and container.get("nextCursor"):
            return str(container["nextCursor"])
    return _cursor_from_link((page.get("links") or {}).get("next"))


def _cursor_from_link(link: Any) -> str | None:
    if not isinstance(link, str) or "page" not in link:
        return None
    from urllib.parse import parse_qs, urlparse, unquote
    query = parse_qs(urlparse(unquote(link)).query)
    values = query.get("page[cursor]")
    return values[0] if values else None


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
