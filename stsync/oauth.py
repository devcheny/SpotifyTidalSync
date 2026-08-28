"""OAuth 2.0 Authorization Code + PKCE con callback en localhost.

Spotify y TIDAL usan exactamente el mismo flujo, asi que sirve para los dos.
No hace falta client_secret: PKCE esta pensado para apps de escritorio.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

import requests

_HTML = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>
 body{{font-family:Segoe UI,system-ui,sans-serif;background:#12131a;color:#e8e8ef;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
 .c{{text-align:center;max-width:32rem;padding:2rem}}
 h1{{font-size:1.4rem;margin:0 0 .5rem}} p{{color:#a5a5b8;margin:0}}
 .ok{{color:#1db954}} .err{{color:#ff6b6b}}
</style>
<div class="c"><h1 class="{cls}">{title}</h1><p>{msg}</p></div>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SpotifyTidalSync"

    def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por la clase base)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") not in ("", self.server.expected_path.rstrip("/")):
            self.send_error(404)
            return

        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.result = params

        if "error" in params:
            body = _HTML.format(
                cls="err", title="Autorizacion cancelada",
                msg=f"El servicio devolvio: {params['error']}. Puedes cerrar esta pestana.",
            )
        else:
            body = _HTML.format(
                cls="ok", title="Cuenta conectada",
                msg="Ya puedes cerrar esta pestana y volver a la aplicacion.",
            )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: Any) -> None:  # silencia el log a stderr
        pass


class _CallbackServer(http.server.HTTPServer):
    result: dict[str, str] | None = None
    expected_path: str = "/callback"


@dataclass
class OAuthEndpoints:
    authorize_url: str
    token_url: str


class OAuthError(RuntimeError):
    pass


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def authorize(
    endpoints: OAuthEndpoints,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    extra_auth_params: dict[str, str] | None = None,
    timeout: int = 300,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Abre el navegador, espera el callback y devuelve el token."""
    parsed = urllib.parse.urlparse(redirect_uri)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 80

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    params.update(extra_auth_params or {})
    url = f"{endpoints.authorize_url}?{urllib.parse.urlencode(params)}"

    try:
        server = _CallbackServer((host, port), _CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            f"No se pudo abrir el puerto {port} para el callback ({exc}). "
            "Cierra lo que lo este usando o cambia el redirect_uri en Ajustes."
        ) from exc

    server.expected_path = parsed.path or "/callback"
    server.timeout = timeout

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    if open_browser:
        webbrowser.open(url)
    thread.join(timeout)
    server.server_close()

    result = server.result
    if not result:
        raise OAuthError(
            "No se recibio respuesta del navegador. Si no se abrio solo, "
            f"pega esta URL manualmente:\n{url}"
        )
    if "error" in result:
        raise OAuthError(f"Autorizacion rechazada: {result['error']}")
    if result.get("state") != state:
        raise OAuthError("El parametro 'state' no coincide (posible intento de CSRF).")
    if "code" not in result:
        raise OAuthError("El servicio no devolvio ningun codigo de autorizacion.")

    return exchange_code(endpoints, client_id, redirect_uri, result["code"], verifier)


def exchange_code(
    endpoints: OAuthEndpoints,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> dict[str, Any]:
    resp = requests.post(
        endpoints.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return _token_or_raise(resp, "intercambiar el codigo por un token")


def refresh(
    endpoints: OAuthEndpoints, client_id: str, refresh_token: str
) -> dict[str, Any]:
    resp = requests.post(
        endpoints.token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    return _token_or_raise(resp, "renovar el token")


def _token_or_raise(resp: requests.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:400]
        raise OAuthError(f"Error al {what} (HTTP {resp.status_code}): {detail}")
    return resp.json()
