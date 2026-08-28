"""Interfaz grafica (tkinter): inicio de sesion, sincronizacion manual y ajustes."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from urllib.parse import urlparse

from . import scheduler
from .config import Config
from .http import ApiError
from .oauth import OAuthError
from .paths import app_dir
from .spotify import SpotifyClient
from .store import StateStore, TokenStore
from .sync import SyncEngine
from .tidal import TidalClient

BG = "#12131a"
CARD = "#1c1e27"
FG = "#e8e8ef"
MUTED = "#9a9ab0"
SPOTIFY_GREEN = "#1db954"
TIDAL_BLUE = "#00c4ff"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify <-> TIDAL Sync")
        self.geometry("880x680")
        self.minsize(760, 600)
        self.configure(bg=BG)

        self.cfg = Config.load()
        self.tokens = TokenStore()
        self.state = StateStore()
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self._save_status_job: str | None = None

        self._build_styles()
        self._build_ui()
        self._refresh_accounts()
        self._refresh_schedule()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ estilo
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=CARD)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Segoe UI", 16, "bold"))
        style.configure("Head.TLabel", background=CARD, foreground=FG,
                        font=("Segoe UI", 11, "bold"))
        style.configure("TButton", padding=7, background="#2b2e3c", foreground=FG,
                        borderwidth=0)
        style.map("TButton", background=[("active", "#3a3e50"),
                                         ("disabled", "#22242e")])
        style.configure("Accent.TButton", background=SPOTIFY_GREEN,
                        foreground="#06210f", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#25d366"),
                                                ("disabled", "#1a5230")])
        style.configure("TCheckbutton", background=CARD, foreground=FG)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#22242e", foreground=MUTED,
                        padding=(16, 8))
        style.map("TNotebook.Tab", background=[("selected", CARD)],
                  foreground=[("selected", FG)])
        style.configure("TEntry", fieldbackground="#22242e", foreground=FG,
                        insertcolor=FG, borderwidth=0)
        style.configure("TCombobox", fieldbackground="#22242e", foreground=FG,
                        background="#22242e", arrowcolor=FG)

    # ---------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Spotify  <->  TIDAL", style="Title.TLabel").pack(side="left")
        self.last_sync_label = ttk.Label(header, text="", style="TLabel",
                                         foreground=MUTED)
        self.last_sync_label.pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        self.tab_main = ttk.Frame(notebook, padding=14)
        self.tab_settings = ttk.Frame(notebook, padding=14)
        notebook.add(self.tab_main, text="Sincronizacion")
        notebook.add(self.tab_settings, text="Ajustes")

        self._build_main_tab()
        self._build_settings_tab()

    def _build_main_tab(self) -> None:
        accounts = ttk.Frame(self.tab_main)
        accounts.pack(fill="x")
        accounts.columnconfigure(0, weight=1, uniform="acc")
        accounts.columnconfigure(1, weight=1, uniform="acc")

        self.sp_status, self.sp_button = self._account_card(
            accounts, 0, "Spotify", SPOTIFY_GREEN, self._toggle_spotify)
        self.td_status, self.td_button = self._account_card(
            accounts, 1, "TIDAL", TIDAL_BLUE, self._toggle_tidal)

        actions = ttk.Frame(self.tab_main, style="Card.TFrame", padding=14)
        actions.pack(fill="x", pady=(12, 0))

        row = ttk.Frame(actions, style="Card.TFrame")
        row.pack(fill="x")
        self.sync_button = ttk.Button(row, text="Sincronizar ahora",
                                      style="Accent.TButton", command=self._start_sync)
        self.sync_button.pack(side="left")
        self.stop_button = ttk.Button(row, text="Detener", command=self._stop_sync,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Abrir carpeta de datos",
                   command=lambda: webbrowser.open(str(app_dir()))).pack(side="right")

        sched = ttk.Frame(actions, style="Card.TFrame")
        sched.pack(fill="x", pady=(12, 0))
        ttk.Label(sched, text="Automatico cada 24 h a las",
                  style="Card.TLabel").pack(side="left")
        self.time_var = tk.StringVar(value=self.cfg.get("schedule_time", "03:00"))
        ttk.Entry(sched, textvariable=self.time_var, width=7).pack(side="left", padx=8)
        self.schedule_button = ttk.Button(sched, text="Activar",
                                          command=self._toggle_schedule)
        self.schedule_button.pack(side="left")
        self.schedule_label = ttk.Label(sched, text="", style="Muted.TLabel")
        self.schedule_label.pack(side="left", padx=(12, 0))

        self.progress = ttk.Progressbar(self.tab_main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(12, 4))

        log_frame = ttk.Frame(self.tab_main, style="Card.TFrame", padding=2)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, bg="#0e0f16", fg="#cfd0dd", bd=0,
                                font=("Consolas", 9), wrap="word", padx=10, pady=8,
                                insertbackground=FG, state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("err", foreground="#ff6b6b")
        self.log_text.tag_configure("ok", foreground=SPOTIFY_GREEN)

    def _account_card(self, parent: ttk.Frame, column: int, name: str,
                      color: str, command) -> tuple[ttk.Label, ttk.Button]:
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.grid(row=0, column=column, sticky="ew",
                  padx=(0, 6) if column == 0 else (6, 0))
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        tk.Label(top, text="●", fg=color, bg=CARD,
                 font=("Segoe UI", 13)).pack(side="left")
        ttk.Label(top, text=name, style="Head.TLabel").pack(side="left", padx=(6, 0))
        status = ttk.Label(card, text="Sin conectar", style="Muted.TLabel")
        status.pack(anchor="w", pady=(6, 10))
        button = ttk.Button(card, text="Iniciar sesion", command=command)
        button.pack(anchor="w")
        return status, button

    def _build_settings_tab(self) -> None:
        creds = ttk.Frame(self.tab_settings, style="Card.TFrame", padding=14)
        creds.pack(fill="x")
        ttk.Label(creds, text="Credenciales de desarrollador",
                  style="Head.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(creds, text="Crea una app gratuita en cada portal y pega aqui el "
                              "Client ID. Anade el Redirect URI tal cual aparece.",
                  style="Muted.TLabel", wraplength=760,
                  justify="left").grid(row=1, column=0, columnspan=3,
                                       sticky="w", pady=(2, 12))
        creds.columnconfigure(1, weight=1)

        self.vars: dict[str, tk.Variable] = {}
        rows = [
            ("spotify_client_id", "Spotify Client ID",
             "https://developer.spotify.com/dashboard"),
            ("spotify_redirect_uri", "Spotify Redirect URI", None),
            ("tidal_client_id", "TIDAL Client ID",
             "https://developer.tidal.com/dashboard"),
            ("tidal_redirect_uri", "TIDAL Redirect URI", None),
        ]
        for i, (key, label, url) in enumerate(rows, start=2):
            ttk.Label(creds, text=label, style="Card.TLabel").grid(
                row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = var
            ttk.Entry(creds, textvariable=var).grid(row=i, column=1,
                                                    sticky="ew", pady=3)
            if url:
                ttk.Button(creds, text="Abrir portal", width=13,
                           command=lambda u=url: webbrowser.open(u)).grid(
                    row=i, column=2, padx=(8, 0))

        options = ttk.Frame(self.tab_settings, style="Card.TFrame", padding=14)
        options.pack(fill="x", pady=(12, 0))
        ttk.Label(options, text="Que se sincroniza",
                  style="Head.TLabel").pack(anchor="w", pady=(0, 8))

        for key, text in [
            ("sync_favorites", "Canciones que te gustan / favoritos"),
            ("sync_playlists", "Playlists propias"),
            ("propagate_deletions",
             "Propagar borrados (si quitas algo en un lado, se quita en el otro)"),
            ("dry_run", "Modo simulacion (no escribe nada en las cuentas)"),
        ]:
            var = tk.BooleanVar(value=bool(self.cfg.get(key)))
            self.vars[key] = var
            ttk.Checkbutton(options, text=text, variable=var).pack(anchor="w", pady=2)

        grid = ttk.Frame(options, style="Card.TFrame")
        grid.pack(anchor="w", pady=(10, 0))
        ttk.Label(grid, text="Direccion", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.direction_var = tk.StringVar(value=self._direction_label(self.cfg.direction))
        ttk.Combobox(grid, textvariable=self.direction_var, state="readonly", width=34,
                     values=list(_DIRECTIONS.values())).grid(row=0, column=1, sticky="w")
        ttk.Label(grid, text="Pais (TIDAL)", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self.country_var = tk.StringVar(value=str(self.cfg.get("country_code", "ES")))
        ttk.Entry(grid, textvariable=self.country_var, width=6).grid(
            row=1, column=1, sticky="w", pady=(8, 0))

        save_row = ttk.Frame(self.tab_settings)
        save_row.pack(fill="x", pady=14)
        ttk.Button(save_row, text="Guardar ajustes", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")
        self.save_status = ttk.Label(save_row, text="", style="TLabel")
        self.save_status.pack(side="left", padx=12)

    # ------------------------------------------------------------------ estado
    def _refresh_accounts(self) -> None:
        for service, status, button in (
            ("spotify", self.sp_status, self.sp_button),
            ("tidal", self.td_status, self.td_button),
        ):
            if self.tokens.has(service):
                name = self.state.data.get("names", {}).get(service, "")
                status.configure(text=f"Conectado{f' como {name}' if name else ''}")
                button.configure(text="Cerrar sesion")
            else:
                status.configure(text="Sin conectar")
                button.configure(text="Iniciar sesion")

        last = self.state.last_sync
        summary = self.state.data.get("last_summary", "")
        self.last_sync_label.configure(
            text=f"Ultima sincronizacion: {last.replace('T', ' ')}" if last
            else "Todavia sin sincronizar")
        if summary and not self.log_text.get("1.0", "end").strip():
            self._append(f"Resumen anterior: {summary}")

    def _refresh_schedule(self) -> None:
        exists = scheduler.task_exists()
        self.schedule_button.configure(text="Desactivar" if exists else "Activar")
        self.schedule_label.configure(text=scheduler.task_info() if exists else "")

    # ------------------------------------------------------------------ acciones
    def _toggle_spotify(self) -> None:
        self._toggle_account("spotify")

    def _toggle_tidal(self) -> None:
        self._toggle_account("tidal")

    def _toggle_account(self, service: str) -> None:
        if self.tokens.has(service):
            self.tokens.clear(service)
            self.state.data.setdefault("names", {}).pop(service, None)
            self.state.save()
            self._append(f"Sesion de {service} cerrada.")
            self._refresh_accounts()
            return

        client_id = self.cfg.get(f"{service}_client_id")
        if not client_id:
            messagebox.showwarning(
                "Falta el Client ID",
                f"Pon el Client ID de {service} en la pestana Ajustes.\n\n"
                "Se crea gratis en el portal de desarrolladores del servicio.")
            return

        self._append(f"Abriendo el navegador para iniciar sesion en {service}...")
        self._set_busy(True)
        threading.Thread(target=self._login_worker, args=(service,),
                         daemon=True).start()

    def _login_worker(self, service: str) -> None:
        try:
            client = (SpotifyClient(self.cfg, self.tokens, self._q_log)
                      if service == "spotify"
                      else TidalClient(self.cfg, self.tokens, self._q_log))
            client.login()
            name = client.display_name
            self.queue.put(("name", (service, name)))
            self.queue.put(("log", f"[OK] {service} conectado como {name}"))
        except (OAuthError, ApiError) as exc:
            self.queue.put(("error", f"No se pudo conectar {service}: {exc}"))
        except Exception as exc:  # noqa: BLE001 - el hilo no debe morir en silencio
            self.queue.put(("error", f"Error inesperado en {service}: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_sync(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not (self.tokens.has("spotify") and self.tokens.has("tidal")):
            messagebox.showwarning("Faltan cuentas",
                                   "Conecta Spotify y TIDAL antes de sincronizar.")
            return
        if not self._save_settings(silent=True):
            messagebox.showwarning(
                "Revisa los ajustes",
                "No se puede sincronizar porque hay un ajuste no valido.\n\n"
                + (self._validate_settings() or ""))
            return
        self.stop_flag.clear()
        self._set_busy(True)
        self.stop_button.configure(state="normal")
        self._append("")
        self.worker = threading.Thread(target=self._sync_worker, daemon=True)
        self.worker.start()

    def _sync_worker(self) -> None:
        try:
            engine = SyncEngine(Config.load(), self._q_log, self.stop_flag.is_set)
            stats = engine.run()
            self.queue.put(("ok", stats.summary()))
        except (ApiError, OAuthError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _stop_sync(self) -> None:
        self.stop_flag.set()
        self._append("Deteniendo despues del paso actual...")

    def _toggle_schedule(self) -> None:
        if scheduler.task_exists():
            ok, msg = scheduler.delete_task()
        else:
            time_value = self.time_var.get().strip() or "03:00"
            if not _valid_time(time_value):
                messagebox.showwarning("Hora no valida",
                                       "Usa el formato HH:MM, por ejemplo 03:00.")
                return
            self.cfg.set("schedule_time", time_value)
            self.cfg.save()
            ok, msg = scheduler.create_task(time_value)
        self._append(msg, "ok" if ok else "err")
        if not ok:
            messagebox.showerror("Tarea programada", msg)
        self._refresh_schedule()

    def _save_settings(self, silent: bool = False) -> bool:
        """Valida y guarda. Devuelve True si se guardo correctamente."""
        problem = self._validate_settings()
        if problem:
            self._show_save_status(problem, ok=False)
            if not silent:
                messagebox.showwarning("Ajustes no guardados", problem)
            return False

        for key, var in self.vars.items():
            value = var.get()
            self.cfg.set(key, value.strip() if isinstance(value, str) else value)
        self.cfg.set("country_code", self.country_var.get().strip().upper() or "ES")
        for code, label in _DIRECTIONS.items():
            if label == self.direction_var.get():
                self.cfg.set("direction", code)
                break
        self.cfg.set("schedule_time", self.time_var.get().strip() or "03:00")

        try:
            self.cfg.save()
        except OSError as exc:
            message = f"No se pudieron guardar los ajustes: {exc}"
            self._show_save_status("Error al guardar", ok=False)
            self._append(message, "err")
            if not silent:
                messagebox.showerror("Error al guardar", message)
            return False

        self._show_save_status("Ajustes guardados", ok=True)
        self._append("Ajustes guardados.", "ok")
        return True

    def _validate_settings(self) -> str | None:
        """Devuelve el motivo por el que no se puede guardar, o None si todo bien."""
        country = self.country_var.get().strip()
        if country and not (len(country) == 2 and country.isalpha()):
            return ("El pais debe ser un codigo ISO de 2 letras "
                    "(por ejemplo ES, MX o US).")

        if not _valid_time(self.time_var.get().strip() or "03:00"):
            return "La hora de la sincronizacion automatica debe tener el formato HH:MM."

        for key, name in (("spotify_redirect_uri", "Spotify"),
                          ("tidal_redirect_uri", "TIDAL")):
            uri = str(self.vars[key].get()).strip()
            if not uri:
                return f"Falta el Redirect URI de {name}."
            if not uri.startswith(("http://127.0.0.1:", "http://localhost:")):
                return (f"El Redirect URI de {name} debe apuntar a tu equipo, "
                        "por ejemplo http://127.0.0.1:8898/callback")
            if not urlparse(uri).port:
                return f"Al Redirect URI de {name} le falta el puerto."

        sp_uri = str(self.vars["spotify_redirect_uri"].get()).strip()
        td_uri = str(self.vars["tidal_redirect_uri"].get()).strip()
        if urlparse(sp_uri).port == urlparse(td_uri).port:
            return ("Spotify y TIDAL no pueden usar el mismo puerto en el "
                    "Redirect URI. Cambia uno de los dos.")
        return None

    def _show_save_status(self, message: str, ok: bool) -> None:
        if self._save_status_job is not None:
            self.after_cancel(self._save_status_job)
        self.save_status.configure(
            text=("✓ " if ok else "✗ ") + message,
            foreground=SPOTIFY_GREEN if ok else "#ff6b6b",
        )
        self._save_status_job = self.after(6000, self._clear_save_status)

    def _clear_save_status(self) -> None:
        self._save_status_job = None
        self.save_status.configure(text="")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _direction_label(code: str) -> str:
        return _DIRECTIONS.get(code, _DIRECTIONS["both"])

    def _set_busy(self, busy: bool) -> None:
        self.sync_button.configure(state="disabled" if busy else "normal")
        self.sp_button.configure(state="disabled" if busy else "normal")
        self.td_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.stop_button.configure(state="disabled")

    def _q_log(self, message: str) -> None:
        self.queue.put(("log", message))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append(str(payload))
                elif kind == "ok":
                    self._append(str(payload), "ok")
                elif kind == "error":
                    self._append(str(payload), "err")
                    messagebox.showerror("Error", str(payload))
                elif kind == "name":
                    service, name = payload  # type: ignore[misc]
                    self.state.data.setdefault("names", {})[service] = name
                    self.state.save()
                elif kind == "done":
                    self._set_busy(False)
                    self.state = StateStore()
                    self._refresh_accounts()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _append(self, message: str, tag: str = "") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


_DIRECTIONS = {
    "both": "Bidireccional (union de las dos)",
    "spotify_to_tidal": "Solo Spotify -> TIDAL",
    "tidal_to_spotify": "Solo TIDAL -> Spotify",
}


def _valid_time(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    hour, minute = int(parts[0]), int(parts[1])
    return 0 <= hour <= 23 and 0 <= minute <= 59


def main() -> None:
    App().mainloop()
