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

Las relaciones de pistas se piden con include=items.artists para que llegue
tambien el nombre del artista y no solo su id.
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

    def _collect(self, path: str) -> list[Track]:
        """Lee todas las paginas de una relacion de pistas.

        Se piden tambien los artistas porque sin ellos TIDAL solo devuelve sus
        ids: el nombre llega vacio y no hay forma de comparar por texto (ni con
        iTunes ni con Spotify). Y los albumes, que es donde vive la fecha de
        publicacion: en la pista no hay ningun año.

        Si el servidor no admite un include se prueba con menos, hasta el
        basico de siempre, en vez de quedarse sin nada.
        """
        for include in ("items.artists,items.albums", "items.artists", "items"):
            params = {"countryCode": self.country, "include": include,
                      "locale": "en-US"}
            raw_tracks: list[dict[str, Any]] = []
            artists: dict[str, str] = {}
            years: dict[str, int] = {}
            try:
                for page in self._paginate(path, params):
                    for raw in page.get("included") or []:
                        tipo, ident = raw.get("type"), str(raw.get("id") or "")
                        attrs = raw.get("attributes") or {}
                        if tipo == "tracks":
                            raw_tracks.append(raw)
                        elif tipo == "artists" and ident and attrs.get("name"):
                            artists[ident] = str(attrs["name"])
                        elif tipo == "albums" and ident:
                            year = _year(attrs.get("releaseDate"))
                            if year:
                                years[ident] = year
            except ApiError:
                if include == "items":
                    raise
                self.log(f"    TIDAL rechazo include={include}, pruebo con menos")
                continue
            return [t for t in (_to_track(r, artists, years) for r in raw_tracks) if t]
        return []

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
        out = self._collect("/userCollectionTracks/me/relationships/items")
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
        return self._collect(f"/playlists/{playlist_id}/relationships/items")

    def create_playlist(self, name: str, description: str = "",
                        publica: bool = False) -> dict[str, Any]:
        if self.cfg.dry_run:
            self.log(f"    [dry-run] crearia la playlist de TIDAL: {name}")
            return {"id": "dry-run", "attributes": {"name": name}}
        body = {
            "data": {
                "type": "playlists",
                "attributes": {
                    "name": name,
                    "description": description[:500],
                    # UNLISTED se ve con el enlace pero no sale en tu
                    # perfil; PUBLIC si. Nunca se pone sin pedirlo.
                    "accessType": "PUBLIC" if publica else "UNLISTED",
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

    def playlist_item_metas(self, playlist_id: str) -> dict[str, list[dict[str, Any]]]:
        """El "meta" de cada entrada de la playlist, agrupado por cancion.

        Identifica la entrada concreta, no la cancion: la misma puede estar
        varias veces en la lista, y para borrar hay que decir cual.
        """
        out: dict[str, list[dict[str, Any]]] = {}
        params = {"countryCode": self.country}
        for page in self._paginate(f"/playlists/{playlist_id}/relationships/items",
                                   params):
            for raw in page.get("data") or []:
                meta = raw.get("meta")
                if raw.get("id") and isinstance(meta, dict) and meta:
                    out.setdefault(str(raw["id"]), []).append(meta)
        return out

    def remove_from_playlist(self, playlist_id: str, ids: list[str]) -> None:
        """Quita canciones de una playlist.

        La API rechaza el borrado si no se le devuelve el "meta" de la entrada
        (INVALID_REQUEST_BODY en data/0/meta), asi que primero hay que leer la
        playlist para saber cual corresponde a cada cancion.
        """
        metas = self.playlist_item_metas(playlist_id)
        payload: list[dict[str, Any]] = []
        sin_meta: list[str] = []
        for track_id in ids:
            entradas = metas.get(str(track_id))
            if not entradas:
                sin_meta.append(str(track_id))
                continue
            payload.extend({"id": str(track_id), "type": "tracks",
                            "meta": _item_meta(meta)} for meta in entradas)

        if sin_meta:
            self.log(f"    {len(sin_meta)} canciones ya no estaban en la playlist")
        for chunk in _chunks(payload, WRITE_BATCH):
            self._write(
                "DELETE", f"/playlists/{playlist_id}/relationships/items",
                {"data": chunk},
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
def _to_track(raw: dict[str, Any] | None,
              artists: dict[str, str] | None = None,
              years: dict[str, int] | None = None) -> Track | None:
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
    names = _artist_names(raw, artists or {})
    return Track(
        service=SERVICE,
        id=str(raw["id"]),
        title=attrs.get("title", ""),
        artist=names[0] if names else "",
        album="",
        isrc=isrc,
        duration_ms=_duration_ms(attrs.get("duration")),
        artists=tuple(names),
        year=_track_year(raw, years or {}),
    )


def _track_year(raw: dict[str, Any], years: dict[str, int]) -> int:
    """El año sale del album al que pertenece la pista."""
    rel = ((raw.get("relationships") or {}).get("albums") or {}).get("data") or []
    for item in rel:
        if isinstance(item, dict) and years.get(str(item.get("id", ""))):
            return years[str(item["id"])]
    return 0


def _year(release_date: Any) -> int:
    """De "2022-07-08" saca 2022. Lo que no cuadre se queda en 0."""
    texto = str(release_date or "")[:4]
    if len(texto) == 4 and texto.isdigit():
        numero = int(texto)
        # Un año fuera de rango es un dato mal puesto, no una fecha.
        if 1900 <= numero <= 2100:
            return numero
    return 0


def _artist_names(raw: dict[str, Any], artists: dict[str, str]) -> list[str]:
    """Nombres de los interpretes, del meta de la relacion o de los includes."""
    rel = ((raw.get("relationships") or {}).get("artists") or {}).get("data") or []
    out: list[str] = []
    for item in rel:
        if not isinstance(item, dict):
            continue
        name = ((item.get("meta") or {}).get("name")
                or artists.get(str(item.get("id", "")), ""))
        if name:
            out.append(str(name))
    return out


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


def _item_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Lo justo para senalar la entrada al borrarla.

    Se prefiere el identificador si se reconoce; si no, se devuelve el meta tal
    y como llego, que es lo que la API espera recibir de vuelta.
    """
    for key in ("itemId", "id"):
        if meta.get(key):
            return {key: meta[key]}
    return meta


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]
