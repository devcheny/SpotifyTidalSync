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
from .convert import (DONE_DIR, OBJETIVOS_NOMBRE, POR_DEFECTO, ConvertError,
                      FlacConverter, informe_fichero, tamano_legible)
from .convert import diagnose as flac_diagnose
from .http import ApiError
from .itunes import ITunesError, complete_artists_by_isrc, complete_tags
from .itunes import ITunesLibrary
from .artwork import check_artwork, fix_one_file
from .normalize import (downsample_library, normalize_library,
                        refresh_info)
from .publish import publish_playlists
from .itunes import diagnose as itunes_diagnose
from .oauth import OAuthError
from .paths import app_dir, latest_report
from .spotify import SpotifyClient
from .store import StateStore, TokenStore
from .sync import SyncEngine
from .tidal import TidalClient
from .updates import UpdateError, apply_release, check, current_version

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
        self.minsize(560, 360)
        self.configure(bg=BG)

        self.cfg = Config.load()
        self.tokens = TokenStore()
        self.state = StateStore()
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.vars: dict[str, tk.Variable] = {}
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self._release = None
        self._save_status_job: str | None = None
        # Recuadros que se pueden desplazar con la rueda, de fuera a dentro.
        self._scroll_canvas: list[tk.Canvas] = []

        self._build_styles()
        self._build_ui()
        self.bind_all("<MouseWheel>", self._on_wheel)
        self._refresh_accounts()
        self._refresh_schedule()
        self.after(100, self._drain_queue)
        self.after(1500, self._auto_check_updates)

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
    # -- pestanas con barra de desplazamiento --------------------------------
    def _scrollable(self, notebook: ttk.Notebook) -> tuple[ttk.Frame, ttk.Frame]:
        """Una pestana que se desplaza cuando su contenido no cabe.

        Devuelve (lo que se anade al notebook, donde va el contenido).

        Mientras quepa todo, el contenido ocupa el alto entero de la pestana,
        asi que lo que estuviera puesto para estirarse -el registro de la
        pestana principal- se sigue estirando y la barra ni aparece. En cuanto
        no cabe, manda su alto natural y sale la barra: asi el boton de
        instalar la actualizacion siempre se puede alcanzar, este la ventana
        como este.
        """
        marco = ttk.Frame(notebook)
        canvas = tk.Canvas(marco, bg=BG, highlightthickness=0)
        barra = ttk.Scrollbar(marco, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=barra.set)
        # La barra se empaqueta antes para que se quede a la derecha aunque se
        # esconda y se vuelva a poner.
        barra.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        dentro = ttk.Frame(canvas, padding=14)
        ventana = canvas.create_window((0, 0), window=dentro, anchor="nw")
        ultimo: dict[str, tuple[int, int]] = {}

        def encajar(_event: tk.Event | None = None) -> None:
            ancho, alto = canvas.winfo_width(), canvas.winfo_height()
            if ancho <= 1 or alto <= 1:
                return          # todavia no se ha dibujado
            necesita = dentro.winfo_reqheight()
            medida = (ancho, max(necesita, alto))
            # Sin esto, cambiar el tamano dispara otro <Configure> y se
            # entraria en bucle.
            if ultimo.get("medida") == medida:
                return
            ultimo["medida"] = medida
            canvas.itemconfigure(ventana, width=medida[0], height=medida[1])
            canvas.configure(scrollregion=(0, 0, medida[0], medida[1]))
            if necesita > alto:
                if not barra.winfo_ismapped():
                    barra.pack(side="right", fill="y", before=canvas)
            elif barra.winfo_ismapped():
                barra.pack_forget()

        canvas.bind("<Configure>", encajar)
        dentro.bind("<Configure>", encajar)
        self._scroll_canvas.append(canvas)
        return marco, dentro

    def _on_wheel(self, event: tk.Event) -> None:
        """Desplaza el recuadro que esta debajo del raton, no siempre el mismo.

        Las listas de playlists son recuadros con scroll dentro de una pestana
        que tambien lo tiene. Subiendo por los padres del widget senalado, el
        de dentro gana; y si ese ya no tiene nada que desplazar, la rueda pasa
        al de fuera en vez de quedarse muerta.
        """
        # Tk manda la rueda al widget con el foco, no al que hay debajo del
        # raton, asi que hay que preguntar por las coordenadas. Si no contesta
        # (el puntero esta sobre otro programa) se usa el que la recibio.
        destino = self.winfo_containing(event.x_root, event.y_root) \
            or getattr(event, "widget", None)
        while destino is not None:
            if destino in self._scroll_canvas:
                primero, ultimo = destino.yview()
                if (primero, ultimo) != (0.0, 1.0):
                    destino.yview_scroll(-1 if event.delta > 0 else 1, "units")
                    return
            destino = getattr(destino, "master", None)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Spotify  <->  TIDAL", style="Title.TLabel").pack(side="left")
        self.last_sync_label = ttk.Label(header, text="", style="TLabel",
                                         foreground=MUTED)
        self.last_sync_label.pack(side="right")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        pagina_main, self.tab_main = self._scrollable(notebook)
        pagina_itunes, self.tab_itunes = self._scrollable(notebook)
        pagina_flac, self.tab_flac = self._scrollable(notebook)
        pagina_settings, self.tab_settings = self._scrollable(notebook)
        # Las dos pestanas de iTunes solo estorban en un equipo sin iTunes, asi
        # que solo se ensenan si esta instalado. Los controles se crean igual
        # (el resto de la ventana cuenta con ellos), pero no se anaden.
        pagina_publish, self.tab_publish = self._scrollable(notebook)
        self.itunes_ok = itunes_diagnose()[0]
        notebook.add(pagina_main, text="Sincronizacion")
        if self.itunes_ok:
            notebook.add(pagina_itunes, text="iTunes")
            notebook.add(pagina_publish, text="Publicar")
            notebook.add(pagina_flac, text="Convertir a ALAC")
        notebook.add(pagina_settings, text="Ajustes")

        self._build_main_tab()
        self._build_itunes_tab()
        self._build_publish_tab()
        self._build_flac_tab()
        self._build_settings_tab()

    # -- pestana Publicar: de iTunes hacia fuera -----------------------------
    def _build_publish_tab(self) -> None:
        intro = ttk.Frame(self.tab_publish, style="Card.TFrame", padding=14)
        intro.pack(fill="x")
        ttk.Label(intro, text="Publicar tus listas de iTunes",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(intro, text="Al reves que la pestana iTunes: coge tus listas "
                              "locales y crea la misma en Spotify o en TIDAL, "
                              "buscando alli cada cancion. Tu eliges cuales se "
                              "llevan y cuales quedan publicas; por defecto "
                              "ninguna lo es.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 8))
        tk.Label(intro, text="Cada lista puede ir en un sentido o en los dos: "
                             "'llevar' la copia a Spotify, 'traer' mete en la "
                             "lista de iTunes lo que hayas anadido en Spotify y "
                             "ya tengas en la biblioteca. Marcando las dos, se "
                             "mantiene igual en los dos sitios.",
                 bg=CARD, fg=MUTED, wraplength=740,
                 justify="left").pack(anchor="w", pady=(0, 8))
        tk.Label(intro, text="En TIDAL solo se pueden enlazar las canciones cuyo "
                             "ISRC venga en las etiquetas del fichero: su API no "
                             "sabe buscar por titulo, y por eso tampoco se puede "
                             "traer de TIDAL. En Spotify no hay ese problema.",
                 bg=CARD, fg=MUTED, wraplength=740,
                 justify="left").pack(anchor="w")

        destinos = ttk.Frame(self.tab_publish, style="Card.TFrame", padding=14)
        destinos.pack(fill="x", pady=(12, 0))
        ttk.Label(destinos, text="Adonde", style="Card.TLabel").pack(anchor="w")
        for clave, texto in (("publish_to_spotify", "Spotify"),
                             ("publish_to_tidal", "TIDAL (solo con ISRC)")):
            var = tk.BooleanVar(value=bool(self.cfg.get(clave)))
            self.vars[clave] = var
            ttk.Checkbutton(destinos, text=texto, variable=var).pack(anchor="w",
                                                                    pady=1)
        falta = tk.BooleanVar(value=bool(self.cfg.get("publish_missing_playlist",
                                                      True)))
        self.vars["publish_missing_playlist"] = falta
        ttk.Checkbutton(destinos, variable=falta,
                        text="Al traer, dejar en Spotify una lista "
                             "'... - Faltantes en iTunes' con lo que no tengas "
                             "(privada, y no se copia a TIDAL)").pack(
            anchor="w", pady=(6, 1))

        picker = ttk.Frame(self.tab_publish, style="Card.TFrame", padding=14)
        picker.pack(fill="both", expand=True, pady=(12, 0))
        head = ttk.Frame(picker, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Que listas, en que sentido, y cuales publicas",
                  style="Card.TLabel").pack(side="left")
        self.pub_load_button = ttk.Button(head, text="Cargar de iTunes",
                                          command=self._load_itunes_lists)
        self.pub_load_button.pack(side="right")

        cabecera = ttk.Frame(picker, style="Card.TFrame")
        cabecera.pack(fill="x", pady=(6, 2))
        ttk.Label(cabecera, text="llevar", style="Muted.TLabel",
                  width=8).pack(side="left")
        ttk.Label(cabecera, text="traer", style="Muted.TLabel",
                  width=8).pack(side="left")
        ttk.Label(cabecera, text="publica", style="Muted.TLabel",
                  width=9).pack(side="left")

        caja = ttk.Frame(picker, style="Card.TFrame")
        caja.pack(fill="both", expand=True)
        self.pub_canvas = tk.Canvas(caja, bg=CARD, highlightthickness=0, height=150)
        scroll = ttk.Scrollbar(caja, command=self.pub_canvas.yview)
        self.pub_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.pub_canvas.pack(side="left", fill="both", expand=True)
        self.pub_items = ttk.Frame(self.pub_canvas, style="Card.TFrame")
        self.pub_canvas.create_window((0, 0), window=self.pub_items, anchor="nw")
        self.pub_items.bind("<Configure>", lambda _e: self.pub_canvas.configure(
            scrollregion=self.pub_canvas.bbox("all")))
        self._scroll_canvas.append(self.pub_canvas)

        self.pub_selection: dict[str, tk.BooleanVar] = {}
        self.pub_import: dict[str, tk.BooleanVar] = {}
        self.pub_public: dict[str, tk.BooleanVar] = {}
        self.pub_hint = ttk.Label(picker, text="", style="Muted.TLabel")
        self.pub_hint.pack(anchor="w", pady=(6, 0))
        guardadas = sorted(set(self.cfg.get("publish_playlists") or [])
                           | set(self.cfg.get("publish_import") or []),
                           key=str.casefold)
        self._set_publish_lists(guardadas)

        row = ttk.Frame(self.tab_publish)
        row.pack(fill="x", pady=14)
        self.publish_button = ttk.Button(row, text="Publicar ahora",
                                         style="Accent.TButton",
                                         command=self._start_publish)
        self.publish_button.pack(side="left")
        self.try_publish_button = ttk.Button(
            row, text="Probar", width=8,
            command=lambda: self._start_publish(simular=True))
        self.try_publish_button.pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Guardar ajustes",
                   command=self._save_settings).pack(side="left", padx=(8, 0))

    def _set_publish_lists(self, nombres: list) -> None:
        """Repinta la lista conservando lo que estuviera marcado.

        Acepta nombres sueltos (los que vienen de la configuracion) o pares
        (nombre, se_puede_editar) recien leidos de iTunes: las inteligentes no
        admiten canciones, asi que a esas se les apaga la casilla de 'traer'.
        """
        marcadas = {n for n, v in self.pub_selection.items() if v.get()} or \
            set(self.cfg.get("publish_playlists") or [])
        traidas = {n for n, v in self.pub_import.items() if v.get()} or \
            set(self.cfg.get("publish_import") or [])
        publicas = {n for n, v in self.pub_public.items() if v.get()} or \
            set(self.cfg.get("publish_public") or [])
        for hijo in self.pub_items.winfo_children():
            hijo.destroy()

        editables = {}
        for entrada in nombres:
            if isinstance(entrada, (tuple, list)):
                editables[str(entrada[0])] = bool(entrada[1])
            else:
                editables.setdefault(str(entrada), True)

        self.pub_selection, self.pub_import, self.pub_public = {}, {}, {}
        for nombre in sorted(editables, key=str.casefold):
            editable = editables[nombre]
            fila = ttk.Frame(self.pub_items, style="Card.TFrame")
            fila.pack(anchor="w", fill="x")
            llevar = tk.BooleanVar(value=nombre in marcadas)
            traer = tk.BooleanVar(value=editable and nombre in traidas)
            publica = tk.BooleanVar(value=nombre in publicas)
            self.pub_selection[nombre] = llevar
            self.pub_import[nombre] = traer
            self.pub_public[nombre] = publica
            ttk.Checkbutton(fila, variable=llevar, width=6).pack(side="left")
            ttk.Checkbutton(fila, variable=traer, width=6,
                            state="normal" if editable else "disabled").pack(
                side="left")
            ttk.Checkbutton(fila, variable=publica, width=7).pack(side="left")
            texto = nombre if editable else f"{nombre}   (inteligente)"
            ttk.Label(fila, text=texto, style="Card.TLabel").pack(side="left")
        self.pub_hint.configure(
            text=f"{len(editables)} listas. Las inteligentes solo se pueden "
                 "llevar: iTunes no deja anadirles canciones." if editables
            else "Pulsa 'Cargar de iTunes' para elegir.")

    def _load_itunes_lists(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._set_busy(True)
        self._append("Leyendo las playlists de iTunes...")
        self.worker = threading.Thread(target=self._itunes_lists_reader,
                                       daemon=True)
        self.worker.start()

    def _itunes_lists_reader(self) -> None:
        try:
            library = ITunesLibrary(self._q_log)
            library.connect()
            try:
                nombres = [(str(p.Name), library.is_writable(p))
                           for p in library.user_playlists()]
            finally:
                library.close()
            self.queue.put(("itunes_lists", nombres))
        except ITunesError as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_publish(self, simular: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            return
        elegidas = [n for n, v in self.pub_selection.items() if v.get()]
        traidas = [n for n, v in self.pub_import.items() if v.get()]
        if not elegidas and not traidas:
            messagebox.showwarning("Nada que publicar",
                                   "Marca al menos una lista en la columna "
                                   "'llevar' o en la de 'traer'.")
            return
        if traidas and not self.vars["publish_to_spotify"].get():
            messagebox.showwarning(
                "Falta Spotify",
                "Solo se puede traer desde Spotify, asi que marca Spotify en "
                "'Adonde' o quita las marcas de la columna 'traer'.")
            return
        publicas = [n for n in elegidas if self.pub_public[n].get()]
        if not simular and not self.cfg.dry_run and publicas and \
                not messagebox.askyesno(
                    "Listas publicas",
                    "Estas listas quedaran visibles para cualquiera:\n\n  "
                    + "\n  ".join(publicas[:10]) + "\n\n¿Seguimos?"):
            return
        self._lanzar(self._publish_worker, simular)

    def _publish_worker(self, simular: bool = False) -> None:
        try:
            stats = publish_playlists(self._config_para(simular), self.tokens,
                                      self._q_log, self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ApiError, ITunesError, OAuthError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

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
        self.try_sync_button = ttk.Button(
            row, text="Probar sin tocar nada",
            command=lambda: self._start_sync(simular=True))
        self.try_sync_button.pack(side="left", padx=(8, 0))
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
                                font=("Consolas", 9), wrap="word", height=10,
                                padx=10, pady=8,
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
        self.try_itunes_button = ttk.Button(
            row, text="Probar", width=8,
            command=lambda: self._start_itunes(simular=True))
        self.try_itunes_button.pack(side="left", padx=(8, 0))
        self.fix_button = ttk.Button(row, text="Completar datos",
                                     command=self._start_fix_artists)
        self.fix_button.pack(side="left", padx=(8, 0))
        self.isrc_button = ttk.Button(row, text="Artistas por ISRC",
                                      command=self._start_isrc)
        self.isrc_button.pack(side="left", padx=(8, 0))
        self.try_fix_button = ttk.Button(
            row, text="Probar", width=8,
            command=lambda: self._start_fix_artists(simular=True))
        self.try_fix_button.pack(side="left", padx=(8, 0))
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
        # La rueda la reparte _on_wheel: estando dentro de una pestana que
        # tambien se desplaza, gana el recuadro de dentro.
        self._scroll_canvas.append(self.itunes_canvas)

        self.itunes_selection: dict[str, tk.BooleanVar] = {}
        self.itunes_hint = ttk.Label(picker, text="", style="Muted.TLabel",
                                     wraplength=700, justify="left")
        self.itunes_hint.pack(anchor="w", pady=(6, 0))
        self._set_itunes_playlists(saved, checked=set(saved))

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

    # -- pestana Convertir a ALAC -------------------------------------------------
    def _build_flac_tab(self) -> None:
        intro = ttk.Frame(self.tab_flac, style="Card.TFrame", padding=14)
        intro.pack(fill="x")
        ttk.Label(intro, text="Convertir a ALAC",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(intro, text="iTunes no sabe leer FLAC, y un WAV si lo lee "
                              "pero ocupa el triple. Esto busca en la carpeta de "
                              "auto-anadir todo lo que sea sin perdida (FLAC, "
                              "WAV, AIFF...), lo convierte a ALAC y deja el .m4a "
                              "en la raiz, que es donde iTunes lo recoge solo. "
                              "Los MP3 no se tocan: pasarlos a ALAC no les "
                              "devolveria la calidad que ya perdieron.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 10))
        self.flac_status = tk.Label(intro, text="", bg=CARD, fg=MUTED,
                                    wraplength=740, justify="left")
        self.flac_status.pack(anchor="w")

        paths = ttk.Frame(self.tab_flac, style="Card.TFrame", padding=14)
        paths.pack(fill="x", pady=(12, 0))
        paths.columnconfigure(1, weight=1)
        for row, (key, label, browse) in enumerate((
            ("flac_folder", "Carpeta", self._browse_flac_folder),
            ("ffmpeg_path", "ffmpeg (opcional)", self._browse_ffmpeg),
        )):
            ttk.Label(paths, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = var
            ttk.Entry(paths, textvariable=var).grid(row=row, column=1,
                                                    sticky="ew", pady=3)
            ttk.Button(paths, text="Examinar...", width=13,
                       command=browse).grid(row=row, column=2, padx=(8, 0))
        ttk.Label(paths, text="Deja ffmpeg vacio si ya esta en el PATH.",
                  style="Muted.TLabel").grid(row=2, column=1, sticky="w",
                                             pady=(4, 0))

        options = ttk.Frame(self.tab_flac, style="Card.TFrame", padding=14)
        options.pack(fill="x", pady=(12, 0))
        for key, text in (
            ("flac_after_sync",
             "Convertir al terminar la sincronizacion, lo ultimo de la cola "
             "(y en la tarea programada)"),
            ("flac_normalize",
             "Normalizar el volumen (loudnorm, como el flac2alac.bat de siempre)"),
            ("flac_two_pass",
             "Medir el volumen antes de normalizar: tarda el doble pero clava "
             "el nivel en vez de quedarse cerca"),
            ("flac_complete_tags",
             "Completar el artista y el titulo que falten, sacandolos del "
             "nombre del fichero ('Artista - Titulo.flac')"),
            ("flac_keep_artwork", "Conservar la caratula si el FLAC la trae"),
            ("flac_delete_source",
             f"Borrar el FLAC al convertirlo (si no, se mueve a '{DONE_DIR}')"),
        ):
            var = tk.BooleanVar(value=bool(self.cfg.get(key)))
            self.vars[key] = var
            ttk.Checkbutton(options, text=text, variable=var).pack(anchor="w",
                                                                   pady=2)

        calidad = ttk.Frame(options, style="Card.TFrame")
        calidad.pack(anchor="w", pady=(12, 0))
        ttk.Label(calidad, text="Hasta que calidad se graba",
                  style="Card.TLabel").pack(anchor="w")
        tk.Label(calidad, text="Es un techo, no un objetivo: lo que ya venga "
                               "por debajo se queda como esta. Vale para el "
                               "conversor y para las dos pasadas de la "
                               "biblioteca.",
                 bg=CARD, fg=MUTED, wraplength=700,
                 justify="left").pack(anchor="w", pady=(2, 6))
        self.calidad_var = tk.StringVar(
            value=str(self.cfg.get("quality_target", POR_DEFECTO)))
        for clave, texto in (
            ("48k", "24 bits / 48 kHz  (2304 kbps) - el equilibrio, y lo mas "
                    "alto que un .m4a puede declarar"),
            ("cd", "16 bits / 44,1 kHz  (1411 kbps) - calidad CD, lo que menos "
                   "ocupa"),
        ):
            ttk.Radiobutton(calidad, text=texto, value=clave,
                            variable=self.calidad_var).pack(anchor="w", pady=1)

        sched = ttk.Frame(options, style="Card.TFrame")
        sched.pack(fill="x", pady=(12, 0))
        ttk.Label(sched, text="O por su cuenta, revisando cada 24 h a las",
                  style="Card.TLabel").pack(side="left")
        self.flac_time_var = tk.StringVar(
            value=str(self.cfg.get("flac_schedule_time", "04:00")))
        ttk.Entry(sched, textvariable=self.flac_time_var,
                  width=7).pack(side="left", padx=8)
        self.flac_schedule_button = ttk.Button(
            sched, text="Activar", command=self._toggle_flac_schedule)
        self.flac_schedule_button.pack(side="left")
        self.flac_schedule_label = ttk.Label(sched, text="", style="Muted.TLabel")
        self.flac_schedule_label.pack(side="left", padx=(12, 0))

        row = ttk.Frame(self.tab_flac)
        row.pack(fill="x", pady=14)
        self.flac_button = ttk.Button(row, text="Convertir ahora",
                                      style="Accent.TButton",
                                      command=self._start_flac)
        self.flac_button.pack(side="left")
        self.try_flac_button = ttk.Button(
            row, text="Probar", width=8,
            command=lambda: self._start_flac(simular=True))
        self.try_flac_button.pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Guardar ajustes",
                   command=self._save_settings).pack(side="left", padx=(8, 0))

        self._build_library_box()
        self._refresh_flac_status()
        self._refresh_flac_schedule()

    # -- repaso de la biblioteca (cosa de una vez) ----------------------------
    def _build_library_box(self) -> None:
        caja = ttk.Frame(self.tab_flac, style="Card.TFrame", padding=14)
        caja.pack(fill="x")
        ttk.Label(caja, text="Repasar toda la biblioteca  (se hace una vez)",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(caja, text="Mide cancion por cancion y arregla solo las que lo "
                             "necesitan: las que se salen del volumen y, si arriba "
                             "esta marcada la calidad CD, las que esten grabadas "
                             "por encima. Lo que ya esta bien no se toca. Con una "
                             "biblioteca grande tarda un buen rato.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 8))

        alac = tk.BooleanVar(value=bool(self.cfg.get("library_to_alac", True)))
        self.vars["library_to_alac"] = alac
        ttk.Checkbutton(caja, variable=alac,
                        text="Pasar a ALAC los WAV y FLAC que ya esten en la "
                             "biblioteca (mismo sonido, la mitad de espacio)"
                        ).pack(anchor="w")

        var = tk.BooleanVar(value=bool(self.cfg.get("library_include_lossy")))
        self.vars["library_include_lossy"] = var
        ttk.Checkbutton(caja, variable=var,
                        text="Incluir tambien MP3 y demas formatos con perdida "
                             "(hay que recomprimirlos y pierden algo de calidad)"
                        ).pack(anchor="w")

        # Al reves que las otras: la casilla dice lo que hay que hacer, no lo
        # que se guarda. Marcarla es pedir que se repase todo otra vez.
        self.rehacer_var = tk.BooleanVar(
            value=not self.cfg.get("library_skip_done", True))
        ttk.Checkbutton(caja, variable=self.rehacer_var,
                        text="Repasar TODO otra vez, incluso lo ya repasado "
                             "(mas lento, pero no se salta nada)").pack(anchor="w")

        fila = ttk.Frame(caja, style="Card.TFrame")
        fila.pack(anchor="w", pady=(10, 0))
        self.library_button = ttk.Button(fila, text="Repasar la biblioteca",
                                         command=self._start_library)
        self.library_button.pack(side="left")
        self.try_library_button = ttk.Button(
            fila, text="Probar", width=8,
            command=lambda: self._start_library(simular=True))
        self.try_library_button.pack(side="left", padx=(8, 0))

        self._build_artwork_box()

    # -- caratulas y datos de iTunes -----------------------------------------
    def _build_artwork_box(self) -> None:
        caja = ttk.Frame(self.tab_flac, style="Card.TFrame", padding=14)
        caja.pack(fill="x", pady=(12, 0))
        ttk.Label(caja, text="Arreglar caratulas y refrescar iTunes",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(caja, text="Un FLAC suele traer la portada en PNG, y dentro de "
                             "un .m4a eso esta fuera de norma: iTunes la ensena "
                             "igual, pero hay programas (rekordbox, por ejemplo) "
                             "que se cierran sin decir nada al cargar la cancion. "
                             "Esto la pasa a JPEG copiando el audio tal cual, sin "
                             "recodificarlo: no se pierde ni un bit y va rapido.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 8))

        fila_cd = ttk.Frame(caja, style="Card.TFrame")
        fila_cd.pack(anchor="w", pady=(0, 10))
        self.down_button = ttk.Button(fila_cd, text="Bajar la calidad de lo alto",
                                      style="Accent.TButton",
                                      command=self._start_downsample)
        self.down_button.pack(side="left")
        self.try_down_button = ttk.Button(
            fila_cd, text="Probar", width=8,
            command=lambda: self._start_downsample(simular=True))
        self.try_down_button.pack(side="left", padx=(8, 0))
        self.down_label = tk.Label(
            caja, text="", bg=CARD, fg=MUTED, wraplength=740, justify="left")
        self.down_label.pack(anchor="w", pady=(0, 12))
        self._refresh_calidad()

        quitar = tk.BooleanVar(value=bool(self.cfg.get("artwork_remove", False)))
        self.vars["artwork_remove"] = quitar
        ttk.Checkbutton(caja, variable=quitar,
                        text="Quitar la portada en vez de convertirla (por si "
                             "aun asi da problemas)").pack(anchor="w")

        fila = ttk.Frame(caja, style="Card.TFrame")
        fila.pack(anchor="w", pady=(10, 0))
        self.art_button = ttk.Button(fila, text="Repasar caratulas",
                                     command=self._start_artwork)
        self.art_button.pack(side="left")
        self.try_art_button = ttk.Button(
            fila, text="Probar", width=8,
            command=lambda: self._start_artwork(simular=True))
        self.try_art_button.pack(side="left", padx=(8, 0))
        self.refresh_button = ttk.Button(fila, text="Releer datos en iTunes",
                                         command=self._start_refresh)
        self.refresh_button.pack(side="left", padx=(20, 0))
        self.inspect_button = ttk.Button(fila, text="Examinar un fichero...",
                                         command=self._start_inspect)
        self.inspect_button.pack(side="left", padx=(8, 0))
        self.fixone_button = ttk.Button(fila, text="Convertir/arreglar uno...",
                                        command=self._start_fix_one)
        self.fixone_button.pack(side="left", padx=(8, 0))

        ttk.Label(caja, text="'Releer datos' es para cuando iTunes sigue diciendo "
                             "9216 kbps de una cancion que ya esta en 1411: se "
                             "queda con lo que anoto el dia que la importo, y "
                             "esto le obliga a releer el fichero.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(8, 0))
        ttk.Label(caja, text="'Examinar un fichero' cuenta todo lo que se sabe de "
                             "una cancion: contenedor, streams, etiquetas, si el "
                             "indice va al principio, si el fichero llega entero "
                             "y si el audio se decodifica sin errores. Cuando un "
                             "reproductor se cierra sin decir por que, la via es "
                             "examinar la que falla y una que funcione y comparar.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(6, 0))

    def _start_artwork(self, simular: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            return
        if not simular and not self.cfg.dry_run and not messagebox.askyesno(
                "Repasar caratulas",
                "Se van a reescribir las canciones cuya portada no valga para "
                "un .m4a.\n\n"
                "El audio se copia tal cual, asi que no pierde calidad, pero "
                "son tus ficheros.\n\n"
                "Con el boton Probar ves antes cuantas son y que formato traen. "
                "¿Seguimos?"):
            return
        self._lanzar(self._artwork_worker, simular)

    def _artwork_worker(self, simular: bool = False) -> None:
        try:
            stats = check_artwork(self._config_para(simular), self._q_log,
                                  self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ConvertError, ITunesError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _refresh_calidad(self) -> None:
        """El texto del boton cuenta el techo que hay elegido ahora mismo."""
        objetivo = self.calidad_var.get()
        self.down_label.configure(
            text="Una cancion de 24 bits y 192 kHz no se puede declarar en un "
                 ".m4a: ese campo de la cabecera solo llega a 65535 Hz, asi "
                 "que se queda a CERO. iTunes tira igual, pero rekordbox se "
                 "cierra al analizarla. Esto reescribe lo que pase del techo "
                 f"que tengas puesto ({OBJETIVOS_NOMBRE.get(objetivo, objetivo)}"
                 "), y de paso ocupa bastante menos. No se mide el volumen: "
                 "solo cambia la calidad, asi que va rapido.")

    def _start_downsample(self, simular: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            return
        if not simular and not self.cfg.dry_run and not messagebox.askyesno(
                "Bajar la calidad de lo alto",
                "Se van a reescribir las canciones que pasen del techo que "
                "tienes elegido arriba.\n\n"
                "El volumen se queda como esta: aqui solo cambia la "
                "calidad.\n\n"
                "Con el boton Probar ves antes cuantas son y cuanto ocupan. "
                "¿Seguimos?"):
            return
        self._lanzar(self._downsample_worker, simular)

    def _downsample_worker(self, simular: bool = False) -> None:
        try:
            stats = downsample_library(self._config_para(simular), self._q_log,
                                       self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ConvertError, ITunesError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _elegir_cancion(self, titulo: str) -> Path | None:
        if self.worker and self.worker.is_alive():
            return None
        if not self._save_settings(silent=True):
            return None
        inicial = str(self.cfg.get("flac_folder", "")) or ""
        elegido = filedialog.askopenfilename(
            parent=self, title=titulo,
            initialdir=inicial if Path(inicial).is_dir() else None,
            filetypes=[("Musica", "*.m4a *.mp3 *.flac *.wav *.aif *.aiff *.m4b"),
                       ("Todos", "*.*")])
        return Path(elegido) if elegido else None

    def _start_fix_one(self) -> None:
        """Probar el arreglo en una sola cancion antes de soltarlo en 7000."""
        ruta = self._elegir_cancion("Elige la cancion que quieres arreglar")
        if ruta is None:
            return
        aviso = ("Se va a trabajar sobre esta cancion, y solo sobre ella:\n\n"
                 + ruta.name
                 + "\n\nSi es un FLAC, un WAV o similar, se convierte a ALAC "
                   "al lado y el original se queda donde esta. Si ya es un "
                   ".m4a, se le baja la calidad si pasa del techo y se le pasa "
                   "la portada a JPEG si hace falta.\n\nAl terminar veras como "
                   "estaba y como ha quedado.\n\n¿Seguimos?")
        if not self.cfg.dry_run and \
                not messagebox.askyesno("Convertir o arreglar un solo fichero", aviso):
            return
        self._set_busy(True)
        self.worker = threading.Thread(target=self._fix_one_worker,
                                       args=(ruta,), daemon=True)
        self.worker.start()

    def _fix_one_worker(self, ruta: Path) -> None:
        try:
            texto = fix_one_file(self.cfg, ruta, lambda _m: None)
            self.queue.put(("informe", (ruta.name, texto)))
        except (ConvertError, ITunesError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_inspect(self) -> None:
        ruta = self._elegir_cancion("Elige la cancion que quieres examinar")
        if ruta is None:
            return
        self._set_busy(True)
        self._append(f"Examinando {ruta.name}...")
        self.worker = threading.Thread(target=self._inspect_worker, args=(ruta,),
                                       daemon=True)
        self.worker.start()

    def _inspect_worker(self, ruta: Path) -> None:
        try:
            self.queue.put(("informe", (ruta.name, informe_fichero(self.cfg, ruta))))
        except ConvertError as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_refresh(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            return
        self._lanzar(self._refresh_worker, False)

    def _refresh_worker(self, simular: bool = False) -> None:
        try:
            stats = refresh_info(self._config_para(simular), self._q_log,
                                 self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except ITunesError as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_library(self, simular: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            return
        if not simular and not self.cfg.dry_run and not messagebox.askyesno(
                "Repasar la biblioteca",
                "Se van a reescribir las canciones que hagan falta de tu "
                "biblioteca de iTunes.\n\n"
                "Cada una se convierte aparte y solo se sustituye si sale bien, "
                "pero es una pasada larga y sobre tus ficheros.\n\n"
                "¿Has probado antes con el boton Probar?"):
            return
        self._lanzar(self._library_worker, simular)

    def _library_worker(self, simular: bool = False) -> None:
        try:
            stats = normalize_library(self._config_para(simular), self._q_log,
                                      self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ConvertError, ITunesError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _refresh_flac_schedule(self) -> None:
        exists = scheduler.task_exists(scheduler.FLAC)
        self.flac_schedule_button.configure(
            text="Desactivar" if exists else "Activar")
        self.flac_schedule_label.configure(
            text=scheduler.task_info(scheduler.FLAC) if exists else "")

    def _toggle_flac_schedule(self) -> None:
        if scheduler.task_exists(scheduler.FLAC):
            ok, msg = scheduler.delete_task(scheduler.FLAC)
        else:
            time_value = self.flac_time_var.get().strip() or "04:00"
            if not _valid_time(time_value):
                messagebox.showwarning("Hora no valida",
                                       "Usa el formato HH:MM, por ejemplo 04:00.")
                return
            self.cfg.set("flac_schedule_time", time_value)
            self.cfg.save()
            ok, msg = scheduler.create_task(time_value, scheduler.FLAC)
        self._append(msg, "ok" if ok else "err")
        if not ok:
            messagebox.showerror("Tarea programada", msg)
        self._refresh_flac_schedule()

    def _refresh_flac_status(self) -> None:
        ok, reason = flac_diagnose(self.cfg)
        self.flac_status.configure(text=("✓ " if ok else "✗ ") + reason,
                                   fg=SPOTIFY_GREEN if ok else "#ff6b6b")

    def _browse_flac_folder(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self, title="Carpeta con los FLAC",
            initialdir=str(self.vars["flac_folder"].get() or "C:\\"))
        if chosen:
            self.vars["flac_folder"].set(os.path.normpath(chosen))

    def _browse_ffmpeg(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self, title="Elige ffmpeg.exe",
            filetypes=[("ffmpeg", "ffmpeg.exe"), ("Programas", "*.exe")])
        if chosen:
            self.vars["ffmpeg_path"].set(os.path.normpath(chosen))

    def _start_flac(self, simular: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._save_settings(silent=True):
            messagebox.showwarning(
                "Revisa los ajustes",
                "No se puede continuar porque hay un ajuste no valido.\n\n"
                + (self._validate_settings() or ""))
            return
        self._lanzar(self._flac_worker, simular)

    def _flac_worker(self, simular: bool = False) -> None:
        try:
            converter = FlacConverter(self._config_para(simular), self._q_log,
                                      self.stop_flag.is_set)
            self.queue.put(("ok", converter.run().summary()))
        except ConvertError as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

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

        self._build_match_box()
        self._build_updates_box()

        save_row = ttk.Frame(self.tab_settings)
        save_row.pack(fill="x", pady=14)
        ttk.Button(save_row, text="Guardar ajustes", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")
        self.save_status = ttk.Label(save_row, text="", style="TLabel")
        self.save_status.pack(side="left", padx=12)

    # -- validar que es la misma grabacion ------------------------------------
    def _build_match_box(self) -> None:
        caja = ttk.Frame(self.tab_settings, style="Card.TFrame", padding=14)
        caja.pack(fill="x", pady=(12, 0))
        ttk.Label(caja, text="Que sea la misma cancion, no solo el mismo titulo",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(caja, text="El audio no se puede comparar: de Spotify y TIDAL "
                             "solo llegan datos, nunca el fichero. Lo que si se "
                             "puede es exigir que coincida el ISRC (que identifica "
                             "la grabacion exacta) o, cuando no lo hay, que dure "
                             "mas o menos lo mismo. Asi no se cuela el directo, el "
                             "radio edit ni la version de otro artista.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").pack(anchor="w", pady=(2, 10))

        var = tk.BooleanVar(value=bool(self.cfg.get("match_check_duration", True)))
        self.vars["match_check_duration"] = var
        ttk.Checkbutton(caja, variable=var,
                        text="Descartar lo que dure muy distinto").pack(anchor="w")

        fila = ttk.Frame(caja, style="Card.TFrame")
        fila.pack(anchor="w", pady=(8, 0))
        ttk.Label(fila, text="Margen", style="Card.TLabel").pack(side="left",
                                                                 padx=(0, 10))
        self.tolerance_var = tk.StringVar(
            value=str(self.cfg.get("match_duration_tolerance", 7)))
        ttk.Spinbox(fila, textvariable=self.tolerance_var, from_=1, to=120,
                    width=5).pack(side="left")
        ttk.Label(fila, text="segundos. Lo descartado sale en el informe "
                             "diciendo cuanto dura cada una.",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

    # -- actualizaciones ------------------------------------------------------
    def _build_updates_box(self) -> None:
        caja = ttk.Frame(self.tab_settings, style="Card.TFrame", padding=14)
        caja.pack(fill="x", pady=(12, 0))
        caja.columnconfigure(1, weight=1)

        ttk.Label(caja, text=f"Actualizaciones  ({current_version()} instalada)",
                  style="Head.TLabel").grid(row=0, column=0, columnspan=3,
                                            sticky="w")
        ttk.Label(caja, text="Proyecto de GitHub del que bajarlas. Tus cuentas y "
                             "ajustes no se tocan al actualizar: viven fuera de "
                             "la carpeta del programa.",
                  style="Muted.TLabel", wraplength=740,
                  justify="left").grid(row=1, column=0, columnspan=3, sticky="w",
                                       pady=(2, 10))

        ttk.Label(caja, text="usuario/proyecto",
                  style="Card.TLabel").grid(row=2, column=0, sticky="w",
                                            padx=(0, 10))
        var = tk.StringVar(value=self.cfg.repo())
        self.vars["github_repo"] = var
        ttk.Entry(caja, textvariable=var).grid(row=2, column=1, sticky="ew")
        self.update_button = ttk.Button(caja, text="Buscar ahora", width=15,
                                        command=self._check_updates)
        self.update_button.grid(row=2, column=2, padx=(8, 0))

        aviso = tk.BooleanVar(value=bool(self.cfg.get("update_check", True)))
        self.vars["update_check"] = aviso
        ttk.Checkbutton(caja, text="Avisarme al abrir si hay una version nueva",
                        variable=aviso).grid(row=3, column=0, columnspan=3,
                                             sticky="w", pady=(8, 0))
        self.update_status = tk.Label(caja, text="", bg=CARD, fg=MUTED,
                                      wraplength=740, justify="left")
        self.update_status.grid(row=4, column=0, columnspan=3, sticky="w",
                                pady=(8, 0))

    def _auto_check_updates(self) -> None:
        """Al abrir, en segundo plano: no debe retrasar la ventana."""
        if not self.cfg.get("update_check", True) or not self.cfg.repo():
            return
        threading.Thread(target=self._update_worker, args=(False,),
                         daemon=True).start()

    def _check_updates(self) -> None:
        self.cfg.set("github_repo", str(self.vars["github_repo"].get()).strip())
        self.update_status.configure(text="Consultando GitHub...", fg=MUTED)
        self.update_button.configure(state="disabled")
        threading.Thread(target=self._update_worker, args=(False,),
                         daemon=True).start()

    def _install_update(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not messagebox.askyesno(
                "Actualizar",
                f"Se instalara la version {self._release.version}.\n\n"
                "Tus cuentas y tus ajustes no se tocan. Al terminar hay que "
                "cerrar y volver a abrir la aplicacion.\n\n¿Seguimos?"):
            return
        self._set_busy(True)
        self._append("")
        self.worker = threading.Thread(target=self._update_worker, args=(True,),
                                       daemon=True)
        self.worker.start()

    def _update_worker(self, instalar: bool) -> None:
        try:
            if instalar:
                apply_release(self._release, self._q_log)
                self.queue.put(("update_done", self._release))
            else:
                self.queue.put(("update", check(self.cfg.repo())))
        except UpdateError as exc:
            self.queue.put(("update_error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("update_error", f"Error inesperado: {exc}"))
        finally:
            if instalar:
                self.queue.put(("done", None))

    def _on_update_checked(self, hay: bool, release) -> None:
        self._release = release
        self.update_button.configure(state="normal")
        if not hay:
            self.update_status.configure(
                text=f"✓ Estas al dia ({release.version} es la ultima).",
                fg=SPOTIFY_GREEN)
            return
        self.update_status.configure(
            text=f"Hay una version nueva: {release.version}", fg=TIDAL_BLUE)
        if not hasattr(self, "install_button"):
            self.install_button = ttk.Button(
                self.update_status.master, text="Instalar",
                style="Accent.TButton", command=self._install_update)
            self.install_button.grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._append(f"Hay una version nueva en GitHub: {release.version}", "ok")

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

    def _start_sync(self, simular: bool = False) -> None:
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
        self._lanzar(self._sync_worker, simular)

    def _lanzar(self, worker, simular: bool) -> None:
        """Arranca un trabajo en su hilo, en serio o de mentira."""
        self.stop_flag.clear()
        self._set_busy(True)
        self.stop_button.configure(state="normal")
        self._append("")
        self.worker = threading.Thread(target=worker, args=(simular,),
                                       daemon=True)
        self.worker.start()

    def _config_para(self, simular: bool) -> Config:
        """La configuracion de siempre; en simulacion, solo para esta vez.

        No se guarda: asi se puede probar cualquier accion sin tener que ir a
        Ajustes a marcar la casilla y acordarse luego de desmarcarla.
        """
        cfg = Config.load()
        if simular:
            cfg.set("dry_run", True)
            self._append("MODO SIMULACION: no se va a escribir nada.")
        return cfg

    def _sync_worker(self, simular: bool = False) -> None:
        try:
            engine = SyncEngine(self._config_para(simular), self._q_log,
                                self.stop_flag.is_set)
            stats = engine.run()
            self.queue.put(("ok", stats.summary()))
        except (ApiError, OAuthError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _start_itunes(self, simular: bool = False) -> None:
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
        self._lanzar(self._itunes_worker, simular)

    def _start_fix_artists(self, simular: bool = False) -> None:
        """Rellena en iTunes los datos que faltan, con lo que sabe TIDAL."""
        if self.worker and self.worker.is_alive():
            return
        if not self.tokens.has("tidal"):
            messagebox.showwarning("Falta TIDAL",
                                   "Conecta TIDAL: de ahi salen los datos.")
            return
        if not self._save_settings(silent=True):
            return
        # Probando no se toca nada, asi que no hay nada que confirmar.
        if not simular and not self.cfg.dry_run and not messagebox.askyesno(
                "Completar datos",
                "Se van a cambiar etiquetas de tu biblioteca de iTunes.\n\n"
                "Solo se rellena lo que falta: los artistas que no esten (nunca "
                "se sustituye uno por otro ni se quita ninguno) y el año de las "
                "canciones que no tengan ninguno.\n\n¿Seguimos?"):
            return
        self._lanzar(self._fix_worker, simular)

    def _start_isrc(self) -> None:
        """Completa los artistas de las que figuran a nombre de uno solo."""
        if self.worker and self.worker.is_alive():
            return
        if not (self.tokens.has("spotify") or self.tokens.has("tidal")):
            messagebox.showwarning(
                "Falta una cuenta",
                "Conecta Spotify (o TIDAL): de ahi sale la lista de "
                "interpretes de cada grabacion.")
            return
        if not self._save_settings(silent=True):
            return
        if not self.cfg.dry_run and not messagebox.askyesno(
                "Completar artistas por ISRC",
                "Se recorre la biblioteca buscando las canciones que figuran "
                "a nombre de un solo artista y, por su ISRC, se les anaden "
                "los demas interpretes.\n\n"
                "El ISRC identifica esa grabacion exacta, asi que la lista es "
                "la del sello y no una adivinanza. Solo se anade: a un artista "
                "ya escrito no se le toca.\n\n"
                "Con una biblioteca grande tarda un rato.\n\n¿Seguimos?"):
            return
        self._lanzar(self._isrc_worker, False)

    def _isrc_worker(self, simular: bool = False) -> None:
        try:
            cfg = self._config_para(simular)
            cliente = (SpotifyClient(cfg, self.tokens, self._q_log)
                       if self.tokens.has("spotify")
                       else TidalClient(cfg, self.tokens, self._q_log))
            stats = complete_artists_by_isrc(cfg, cliente, self._q_log,
                                             self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ApiError, ITunesError, ConvertError, OAuthError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _fix_worker(self, simular: bool = False) -> None:
        try:
            cfg = self._config_para(simular)
            stats = complete_tags(
                cfg, TidalClient(cfg, self.tokens, self._q_log),
                self._q_log, self.stop_flag.is_set)
            self.queue.put(("ok", stats.summary()))
        except (ITunesError, ApiError, OAuthError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", f"Error inesperado: {exc}"))
        finally:
            self.queue.put(("done", None))

    def _itunes_worker(self, simular: bool = False) -> None:
        try:
            engine = SyncEngine(self._config_para(simular), self._q_log,
                                self.stop_flag.is_set)
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
        self.cfg.set("library_skip_done", not self.rehacer_var.get())
        self.cfg.set("quality_target", self.calidad_var.get())
        self.cfg.set("match_duration_tolerance",
                     int(self.tolerance_var.get().strip() or 7))
        # El prefijo se guarda tal cual: "TIDAL - " acaba en espacio a proposito.
        self.cfg.set("itunes_playlist_prefix", self.itunes_prefix_var.get())
        self.cfg.set("itunes_playlists", self._itunes_chosen())
        elegidas = [n for n, v in self.pub_selection.items() if v.get()]
        self.cfg.set("publish_playlists", elegidas)
        self.cfg.set("publish_import",
                     [n for n, v in self.pub_import.items() if v.get()])
        self.cfg.set("publish_public",
                     [n for n in elegidas if self.pub_public[n].get()])
        for code, label in _DIRECTIONS.items():
            if label == self.direction_var.get():
                self.cfg.set("direction", code)
                break
        self.cfg.set("schedule_time", self.time_var.get().strip() or "03:00")
        self.cfg.set("flac_schedule_time",
                     self.flac_time_var.get().strip() or "04:00")

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
        self._refresh_flac_status()   # la ruta de ffmpeg puede haber cambiado
        self._refresh_calidad()
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

        margen = self.tolerance_var.get().strip()
        if not margen.isdigit() or not 1 <= int(margen) <= 120:
            return ("El margen de duracion debe ser un numero de segundos "
                    "entre 1 y 120.")

        if not _valid_time(self.time_var.get().strip() or "03:00"):
            return "La hora de la sincronizacion automatica debe tener el formato HH:MM."

        if not _valid_time(self.flac_time_var.get().strip() or "04:00"):
            return "La hora del repaso de FLAC debe tener el formato HH:MM."

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
        for boton in (self.try_sync_button, self.try_itunes_button,
                      self.try_fix_button, self.try_flac_button,
                      self.try_library_button, self.try_publish_button,
                      self.try_art_button, self.try_down_button,
                      self.fixone_button):
            boton.configure(state="disabled" if busy else "normal")
        self.sync_button.configure(state="disabled" if busy else "normal")
        self.itunes_button.configure(state="disabled" if busy else "normal")
        self.itunes_load_button.configure(state="disabled" if busy else "normal")
        self.fix_button.configure(state="disabled" if busy else "normal")
        self.isrc_button.configure(state="disabled" if busy else "normal")
        self.flac_button.configure(state="disabled" if busy else "normal")
        self.library_button.configure(state="disabled" if busy else "normal")
        self.art_button.configure(state="disabled" if busy else "normal")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self.inspect_button.configure(state="disabled" if busy else "normal")
        self.down_button.configure(state="disabled" if busy else "normal")
        self.publish_button.configure(state="disabled" if busy else "normal")
        self.pub_load_button.configure(state="disabled" if busy else "normal")
        self.update_button.configure(state="disabled" if busy else "normal")
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
                elif kind == "update":
                    hay, release = payload  # type: ignore[misc]
                    self._on_update_checked(hay, release)
                elif kind == "update_done":
                    self.update_status.configure(
                        text=f"Actualizado a {payload}. Cierra y vuelve a abrir "
                             "la aplicacion.", fg=SPOTIFY_GREEN)
                    self._append(f"Actualizado a {payload}. Reinicia la "
                                 "aplicacion.", "ok")
                elif kind == "update_error":
                    self.update_button.configure(state="normal")
                    self.update_status.configure(text=str(payload), fg="#ff6b6b")
                elif kind == "informe":
                    nombre, texto = payload  # type: ignore[misc]
                    self._append(texto)
                    TextWindow(self, Path(nombre), contenido=texto)
                elif kind == "itunes_lists":
                    self._set_publish_lists(list(payload))  # type: ignore[arg-type]
                    self._append(f"{len(payload)} playlists leidas de iTunes.", "ok")
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
                tamano_legible(info.st_size),
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

    def __init__(self, app: "App", path: Path,
                 contenido: str | None = None) -> None:
        super().__init__(app)
        self.title(path.name)
        self.geometry("820x560")
        self.configure(bg=BG)

        if contenido is not None:
            raw = contenido        # un informe recien hecho, no un fichero
        else:
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
        self.texto = aviso + raw

        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(row, text="Copiar todo", command=self._copiar).pack(side="left")
        self.aviso_copia = ttk.Label(row, text="", style="TLabel")
        self.aviso_copia.pack(side="left", padx=12)
        ttk.Button(row, text="Cerrar", command=self.destroy).pack(side="right")
        self.transient(app)
        self.bind("<Control-c>", lambda _e: self._copiar())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _copiar(self) -> None:
        """Todo el texto al portapapeles, que es para lo que se suele abrir."""
        self.clipboard_clear()
        self.clipboard_append(self.texto)
        self.aviso_copia.configure(text="Copiado.", foreground=SPOTIFY_GREEN)
        self.after(2500, lambda: self.aviso_copia.configure(text=""))


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
