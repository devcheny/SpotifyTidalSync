"""Interfaz grafica (tkinter): inicio de sesion, sincronizacion manual y ajustes."""
from __future__ import annotations

import csv
import datetime as dt
import os
import queue
import shutil
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse

from . import scheduler
from .config import Config
from .http import ApiError
from .itunes import diagnose as itunes_diagnose
from .oauth import OAuthError
from .paths import app_dir, latest_report
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
        self.vars: dict[str, tk.Variable] = {}
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
        style.configure("Treeview", background="#0e0f16", fieldbackground="#0e0f16",
                        foreground="#cfd0dd", borderwidth=0, rowheight=22)
        style.configure("Treeview.Heading", background="#22242e", foreground=FG,
                        borderwidth=0, padding=(8, 6))
        style.map("Treeview", background=[("selected", "#2b2e3c")],
                  foreground=[("selected", FG)])
        style.map("Treeview.Heading", background=[("active", "#2b2e3c")])

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
        self.tab_itunes = ttk.Frame(notebook, padding=14)
        self.tab_settings = ttk.Frame(notebook, padding=14)
        notebook.add(self.tab_main, text="Sincronizacion")
        notebook.add(self.tab_itunes, text="iTunes")
        notebook.add(self.tab_settings, text="Ajustes")

        self._build_main_tab()
        self._build_itunes_tab()
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
                   command=self._open_data_dir).pack(side="right")
        ttk.Button(row, text="Ver informe",
                   command=self._open_report).pack(side="right", padx=(0, 8))

        # La ruta a la vista: %APPDATA% cambia con el usuario, y si la app se
        # abre elevada los datos acaban en el perfil de otra cuenta.
        path_row = ttk.Frame(actions, style="Card.TFrame")
        path_row.pack(fill="x", pady=(10, 0))
        self.data_path = tk.Label(path_row, text=f"Datos en: {app_dir()}",
                                  bg=CARD, fg=MUTED, cursor="hand2",
                                  font=("Segoe UI", 8), anchor="w")
        self.data_path.pack(side="left")
        self.data_path.bind("<Button-1>", self._copy_data_path)

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

    def _build_itunes_tab(self) -> None:
        intro = ttk.Frame(self.tab_itunes, style="Card.TFrame", padding=14)
        intro.pack(fill="x")
        ttk.Label(intro, text="Playlists de TIDAL en iTunes",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(intro, text="Copia cada playlist de TIDAL a una playlist de iTunes "
                              "buscando las canciones en la biblioteca que ya tienes "
                              "en este equipo. No descarga musica: lo que no tengas "
                              "se apunta en el informe.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 10))
        ok, reason = itunes_diagnose()
        tk.Label(intro, text=("✓ " if ok else "✗ ") + reason, bg=CARD,
                 fg=SPOTIFY_GREEN if ok else "#ff6b6b", wraplength=740,
                 justify="left").pack(anchor="w")

        options = ttk.Frame(self.tab_itunes, style="Card.TFrame", padding=14)
        options.pack(fill="x", pady=(12, 0))
        for key, text in [
            ("itunes_enabled",
             "Volcar en iTunes tambien al sincronizar (y en la tarea programada)"),
            ("itunes_remove_extra",
             "Quitar de la playlist de iTunes lo que ya no este en la de TIDAL"),
            ("itunes_missing_playlist",
             "Dejar en TIDAL una playlist '<nombre> - Faltantes en iTunes'"),
        ]:
            var = tk.BooleanVar(value=bool(self.cfg.get(key)))
            self.vars[key] = var
            ttk.Checkbutton(options, text=text, variable=var).pack(anchor="w", pady=2)

        prefix_row = ttk.Frame(options, style="Card.TFrame")
        prefix_row.pack(anchor="w", pady=(10, 0))
        ttk.Label(prefix_row, text="Nombre en iTunes",
                  style="Card.TLabel").pack(side="left", padx=(0, 10))
        # Sin strip al guardar: el espacio final de "TIDAL - " es intencionado.
        self.itunes_prefix_var = tk.StringVar(
            value=str(self.cfg.get("itunes_playlist_prefix", "")))
        ttk.Entry(prefix_row, textvariable=self.itunes_prefix_var,
                  width=14).pack(side="left")
        ttk.Label(prefix_row, text="+ nombre de la playlist de TIDAL",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        self._build_itunes_picker()

        row = ttk.Frame(self.tab_itunes)
        row.pack(fill="x", pady=14)
        self.itunes_button = ttk.Button(row, text="Volcar en iTunes ahora",
                                        style="Accent.TButton",
                                        command=self._start_itunes)
        self.itunes_button.pack(side="left")
        ttk.Button(row, text="Guardar ajustes",
                   command=self._save_settings).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Ver que falta en iTunes",
                   command=self._open_report).pack(side="right")

    def _open_report(self) -> None:
        """Muestra el ultimo informe dentro de la aplicacion.

        Se lee aqui, con el mismo proceso que lo escribio, en vez de delegar en
        el Explorador: asi funciona aunque la carpeta de datos este en el perfil
        de otro usuario de Windows.
        """
        path = latest_report()
        if path is None:
            messagebox.showinfo(
                "Todavia no hay informe",
                "El informe se crea al terminar una sincronizacion en la que "
                "alguna cancion no tenga equivalencia, o no este en tu "
                "biblioteca de iTunes.\n\nSe guardaria en:\n"
                f"{app_dir()}\\reports")
            return
        self._show_csv(path)

    def _show_csv(self, path: Path) -> None:
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
        except OSError as exc:
            self._append(f"No se pudo leer {path}: {exc}", "err")
            messagebox.showerror(
                "No se pudo leer el informe",
                f"{path}\n\n{exc}\n\nSi lo tienes abierto en Excel, cierralo.")
            return
        ReportWindow(self, path, rows)

    def _open_data_dir(self) -> None:
        """Enseña la carpeta desde dentro de la aplicacion.

        El Explorador corre con la cuenta de tu sesion; si la app va con otra
        (elevada, por ejemplo), no puede entrar en esa carpeta aunque exista.
        Aqui la leemos con el proceso que si tiene acceso.
        """
        DataFolderWindow(self, app_dir())

    def _open_path(self, path: Path, what: str) -> None:
        """Abre una ruta con el programa asociado, explicando si no se puede."""
        try:
            os.startfile(str(path))
        except OSError as exc:
            self._append(f"No se pudo abrir {path}: {exc}", "err")
            messagebox.showerror(
                f"No se pudo abrir {what}",
                f"{path}\n\n{exc}\n\n"
                "Si abriste la aplicacion como administrador, tus datos van al "
                "perfil de ESE usuario y el Explorador de tu sesion no puede "
                "entrar ahi. Cierra la aplicacion y abrela con tu usuario normal.")

    def _copy_data_path(self, _event=None) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(app_dir()))
        self._append(f"Ruta copiada al portapapeles: {app_dir()}", "ok")

    # -- selector de playlists ----------------------------------------------
    def _build_itunes_picker(self) -> None:
        """Lista de playlists con casillas, rellenada desde TIDAL."""
        picker = ttk.Frame(self.tab_itunes, style="Card.TFrame", padding=14)
        picker.pack(fill="both", expand=True, pady=(12, 0))

        head = ttk.Frame(picker, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Que playlists se mantienen en iTunes",
                  style="Card.TLabel").pack(side="left")
        self.itunes_load_button = ttk.Button(head, text="Cargar de TIDAL",
                                             command=self._load_itunes_playlists)
        self.itunes_load_button.pack(side="right")

        # Con "todas" marcado, itunes_playlists se guarda vacio: asi entran
        # tambien las playlists que crees en TIDAL mas adelante.
        saved = list(self.cfg.get("itunes_playlists", []) or [])
        self.itunes_all_var = tk.BooleanVar(value=not saved)
        ttk.Checkbutton(picker, variable=self.itunes_all_var,
                        text="Todas, incluidas las que cree mas adelante en TIDAL",
                        command=self._refresh_itunes_picker).pack(anchor="w",
                                                                  pady=(6, 4))

        box = ttk.Frame(picker, style="Card.TFrame")
        box.pack(fill="both", expand=True)
        self.itunes_canvas = tk.Canvas(box, bg=CARD, highlightthickness=0, height=140)
        scroll = ttk.Scrollbar(box, command=self.itunes_canvas.yview)
        self.itunes_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.itunes_canvas.pack(side="left", fill="both", expand=True)

        self.itunes_items = ttk.Frame(self.itunes_canvas, style="Card.TFrame")
        self.itunes_canvas.create_window((0, 0), window=self.itunes_items,
                                         anchor="nw")
        self.itunes_items.bind(
            "<Configure>",
            lambda _e: self.itunes_canvas.configure(
                scrollregion=self.itunes_canvas.bbox("all")))
        # La rueda solo se apodera del scroll mientras el raton esta encima.
        self.itunes_canvas.bind(
            "<Enter>", lambda _e: self.itunes_canvas.bind_all("<MouseWheel>",
                                                              self._scroll_itunes))
        self.itunes_canvas.bind(
            "<Leave>", lambda _e: self.itunes_canvas.unbind_all("<MouseWheel>"))

        self.itunes_selection: dict[str, tk.BooleanVar] = {}
        self.itunes_hint = ttk.Label(picker, text="", style="Muted.TLabel",
                                     wraplength=700, justify="left")
        self.itunes_hint.pack(anchor="w", pady=(6, 0))
        self._set_itunes_playlists(saved, checked=set(saved))

    def _scroll_itunes(self, event: tk.Event) -> None:
        self.itunes_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _set_itunes_playlists(self, names: list[str],
                              checked: set[str] | None = None) -> None:
        """Repinta las casillas conservando lo que ya estuviera marcado."""
        if checked is None:
            checked = {n for n, var in self.itunes_selection.items() if var.get()}
        for child in self.itunes_items.winfo_children():
            child.destroy()

        self.itunes_selection = {}
        for name in sorted(names, key=str.casefold):
            var = tk.BooleanVar(value=name in checked)
            self.itunes_selection[name] = var
            ttk.Checkbutton(self.itunes_items, text=name,
                            variable=var).pack(anchor="w", pady=1)

        if not names:
            self.itunes_hint.configure(
                text="Pulsa 'Cargar de TIDAL' para elegir playlists una a una.")
        else:
            self.itunes_hint.configure(text=f"{len(names)} playlists.")
        self._refresh_itunes_picker()

    def _refresh_itunes_picker(self) -> None:
        """Con 'todas' marcado, las casillas de abajo no pintan nada."""
        state = "disabled" if self.itunes_all_var.get() else "normal"
        for child in self.itunes_items.winfo_children():
            child.configure(state=state)

    def _load_itunes_playlists(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.tokens.has("tidal"):
            messagebox.showwarning("Falta TIDAL",
                                   "Conecta TIDAL para poder leer tus playlists.")
            return
        self._set_busy(True)
        self._append("Leyendo las playlists de TIDAL...")
        self.worker = threading.Thread(target=self._itunes_lists_worker,
                                       daemon=True)
        self.worker.start()

    def _itunes_lists_worker(self) -> None:
        try:
            client = TidalClient(Config.load(), self.tokens, self._q_log)
            names = [(p.get("attributes") or {}).get("name", "")
                     for p in client.my_playlists()]
            self.queue.put(("playlists", [n for n in names if n]))
        except (ApiError, OAuthError) as exc:
            self.queue.put(("error", f"No se pudieron leer las playlists: {exc}"))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _on_itunes_playlists(self, names: list[str]) -> None:
        marked = {n for n, var in self.itunes_selection.items() if var.get()}
        # Se conservan las guardadas que ya no esten en TIDAL: si una se borro
        # alli, que el usuario lo vea y la quite, no que desaparezca en silencio.
        every = set(names) | set(self.itunes_selection)
        # Primera carga sin nada elegido: marcar todo es lo menos sorprendente.
        self._set_itunes_playlists(sorted(every), checked=marked or set(names))
        self._append(f"{len(names)} playlists leidas de TIDAL.", "ok")

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

    def _start_itunes(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.tokens.has("tidal"):
            messagebox.showwarning("Falta TIDAL",
                                   "Conecta TIDAL antes de volcar en iTunes.")
            return
        if not self._save_settings(silent=True):
            messagebox.showwarning(
                "Revisa los ajustes",
                "No se puede continuar porque hay un ajuste no valido.\n\n"
                + (self._validate_settings() or ""))
            return
        self.stop_flag.clear()
        self._set_busy(True)
        self.stop_button.configure(state="normal")
        self._append("")
        self.worker = threading.Thread(target=self._itunes_worker, daemon=True)
        self.worker.start()

    def _itunes_worker(self) -> None:
        try:
            engine = SyncEngine(Config.load(), self._q_log, self.stop_flag.is_set)
            stats = engine.run_itunes()
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
        # El prefijo se guarda tal cual: "TIDAL - " acaba en espacio a proposito.
        self.cfg.set("itunes_playlist_prefix", self.itunes_prefix_var.get())
        self.cfg.set("itunes_playlists", self._itunes_chosen())
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

    def _itunes_chosen(self) -> list[str]:
        """Playlists elegidas; lista vacia significa 'todas' para el motor."""
        if self.itunes_all_var.get():
            return []
        return [name for name, var in self.itunes_selection.items() if var.get()]

    def _validate_settings(self) -> str | None:
        """Devuelve el motivo por el que no se puede guardar, o None si todo bien."""
        if self.vars["itunes_enabled"].get() and not self.itunes_all_var.get() \
                and not self._itunes_chosen():
            return ("Has activado el volcado a iTunes pero no hay ninguna playlist "
                    "marcada. Elige alguna o marca 'Todas'.")

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
        self.itunes_button.configure(state="disabled" if busy else "normal")
        self.itunes_load_button.configure(state="disabled" if busy else "normal")
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
                elif kind == "playlists":
                    self._on_itunes_playlists(list(payload))  # type: ignore[arg-type]
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


class DataFolderWindow(tk.Toplevel):
    """Contenido de la carpeta de datos, leido por la propia aplicacion."""

    TEXTO = {".log", ".json", ".txt", ".yml", ".csv"}

    def __init__(self, app: "App", folder: Path) -> None:
        super().__init__(app)
        self.app = app
        self.folder = folder
        self.title("Carpeta de datos")
        self.geometry("820x520")
        self.minsize(560, 340)
        self.configure(bg=BG)
        self.files: list[Path] = []

        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Carpeta de datos", style="Title.TLabel").pack(anchor="w")
        tk.Label(top, text=str(folder), bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(anchor="w", pady=(2, 0))

        table = ttk.Frame(self, padding=(14, 0))
        table.pack(fill="both", expand=True)
        columns = ("nombre", "tamano", "modificado")
        self.tree = ttk.Treeview(table, columns=columns, show="headings")
        for name, width, stretch in (("nombre", 420, True), ("tamano", 110, False),
                                     ("modificado", 160, False)):
            self.tree.heading(name, text=name.capitalize())
            self.tree.column(name, width=width, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._view())

        row = ttk.Frame(self, padding=14)
        row.pack(fill="x")
        ttk.Button(row, text="Ver", style="Accent.TButton",
                   command=self._view).pack(side="left")
        ttk.Button(row, text="Guardar copia...",
                   command=self._save_copy).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Probar con el Explorador",
                   command=lambda: app._open_path(folder, "la carpeta de datos")
                   ).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Cerrar", command=self.destroy).pack(side="right")

        self._fill()
        self.transient(app)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _fill(self) -> None:
        for path in sorted(self.folder.rglob("*")):
            if not path.is_file():
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            self.files.append(path)
            self.tree.insert("", "end", values=(
                str(path.relative_to(self.folder)),
                _human_size(info.st_size),
                dt.datetime.fromtimestamp(info.st_mtime).strftime("%d/%m/%Y %H:%M"),
            ))
        if not self.files:
            self.tree.insert("", "end", values=("(carpeta vacia)", "", ""))

    def _selected(self) -> Path | None:
        items = self.tree.selection() or self.tree.get_children()[:1]
        if not items or not self.files:
            return None
        index = self.tree.index(items[0])
        return self.files[index] if index < len(self.files) else None

    def _view(self) -> None:
        path = self._selected()
        if path is None:
            return
        if path.suffix.lower() == ".csv":
            self.app._show_csv(path)
        elif path.suffix.lower() in self.TEXTO:
            TextWindow(self.app, path)
        else:
            # tokens.dat esta cifrado: no hay nada legible que enseñar.
            self.app._open_path(path, path.name)

    def _save_copy(self) -> None:
        """Saca una copia a donde tu si llegues (Escritorio, Documentos...)."""
        path = self._selected()
        if path is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Guardar una copia", initialfile=path.name,
            defaultextension=path.suffix)
        if not destination:
            return
        try:
            shutil.copyfile(path, destination)
        except OSError as exc:
            messagebox.showerror("No se pudo copiar", f"{destination}\n\n{exc}",
                                 parent=self)
            return
        self.app._append(f"Copia guardada en {destination}", "ok")
        messagebox.showinfo("Copia guardada", destination, parent=self)


class TextWindow(tk.Toplevel):
    """Visor de solo lectura para los registros y la configuracion."""

    MAX_BYTES = 400_000

    def __init__(self, app: "App", path: Path) -> None:
        super().__init__(app)
        self.title(path.name)
        self.geometry("820x560")
        self.configure(bg=BG)

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raw = f"No se pudo leer el fichero:\n{exc}"
        aviso = ""
        if len(raw) > self.MAX_BYTES:
            # Un registro puede llegar a 2 MB: interesa el final, no el principio.
            raw = raw[-self.MAX_BYTES:]
            aviso = "(fichero largo: se muestra solo el final)\n\n"

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, bg="#0e0f16", fg="#cfd0dd", bd=0, wrap="word",
                       font=("Consolas", 9), padx=10, pady=8, insertbackground=FG)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", aviso + raw)
        text.see("end")
        text.configure(state="disabled")

        ttk.Button(self, text="Cerrar", command=self.destroy).pack(pady=(0, 12))
        self.transient(app)
        self.bind("<Escape>", lambda _e: self.destroy())


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} MB"


class ReportWindow(tk.Toplevel):
    """Tabla con el informe de canciones que no se pudieron enlazar."""

    def __init__(self, master: "App", path: Path, rows: list[list[str]]) -> None:
        super().__init__(master)
        self.title(f"Sin equivalencia - {path.name}")
        self.geometry("820x560")
        self.minsize(560, 360)
        self.configure(bg=BG)
        self.path = path

        header, data = _split_report(rows)

        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        ttk.Label(top, text=f"{len(data)} canciones sin equivalencia",
                  style="Title.TLabel").pack(anchor="w")
        tk.Label(top, text=str(path), bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(anchor="w", pady=(2, 0))

        table = ttk.Frame(self, padding=(14, 0))
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=header, show="headings",
                                 selectmode="extended")
        for i, name in enumerate(header):
            self.tree.heading(name, text=name.capitalize())
            # La primera columna es el destino: corta. La cancion se lleva el resto.
            self.tree.column(name, width=180 if i == 0 else 560,
                             stretch=i > 0, anchor="w")
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        for row in data:
            self.tree.insert("", "end", values=_pad(row, len(header)))
        if not data:
            self.tree.insert("", "end", values=_pad(["(vacio)"], len(header)))

        row = ttk.Frame(self, padding=14)
        row.pack(fill="x")
        ttk.Button(row, text="Copiar seleccion",
                   command=self._copy).pack(side="left")
        ttk.Button(row, text="Abrir en Excel",
                   command=lambda: master._open_path(path, "el informe")).pack(
            side="left", padx=(8, 0))
        ttk.Button(row, text="Cerrar", command=self.destroy).pack(side="right")

        self.transient(master)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _copy(self) -> None:
        """Al portapapeles, para pegarlo en una lista de la compra o un correo."""
        chosen = self.tree.selection() or self.tree.get_children()
        lines = ["\t".join(str(v) for v in self.tree.item(item, "values"))
                 for item in chosen]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))


def _split_report(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Separa la cabecera del CSV de sus filas, tolerando un fichero vacio."""
    if not rows:
        return ["destino", "cancion"], []
    return rows[0], rows[1:]


def _pad(row: list[str], size: int) -> list[str]:
    return (row + [""] * size)[:size]


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
