"""Que las pestanas se desplacen cuando el contenido no cabe en la ventana.

Abre la ventana de verdad, pero casi transparente y solo un instante: hace
falta que este dibujada para que tkinter de medidas reales, y para que
winfo_containing sepa que hay debajo de un punto.

No toca la configuracion: solo la lee, como al abrir la aplicacion.
"""
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from stsync.gui import App, TextWindow

app = App()
app.wm_attributes("-alpha", 0.01)


def pestana(tab):
    """Ensena esa pestana y devuelve (su canvas, su barra)."""
    canvas = tab.master
    pagina = canvas.master
    pagina.master.select(pagina)      # sin ensenarla, tkinter no la mide
    app.update()
    for hijo in pagina.winfo_children():
        if hijo.winfo_class() == "TScrollbar":
            return canvas, hijo
    raise SystemExit("ERROR: esa pestana no tiene barra de desplazamiento")


def rueda(canvas, dentro=20, delta=-120):
    app._on_wheel(type("Ev", (), {
        "x_root": canvas.winfo_rootx() + dentro,
        "y_root": canvas.winfo_rooty() + dentro,
        "delta": delta, "widget": canvas})())
    app.update()


try:
    # --- 1. ventana pequena: Ajustes se desplaza --------------------------
    app.geometry("700x400")
    app.update()
    ajustes, barra = pestana(app.tab_settings)
    print("1. Ajustes a 700x400 -> yview:", ajustes.yview(),
          "| barra:", bool(barra.winfo_ismapped()))
    assert ajustes.yview() != (0.0, 1.0), "no se puede desplazar"
    assert barra.winfo_ismapped(), "la barra no se ve"

    # --- 2. y el boton de actualizar acaba siendo alcanzable --------------
    alto = ajustes.winfo_height()
    ajustes.yview_moveto(0.0)
    app.update()
    arriba = app.update_button.winfo_rooty() - ajustes.winfo_rooty()
    ajustes.yview_moveto(1.0)
    app.update()
    abajo = app.update_button.winfo_rooty() - ajustes.winfo_rooty()
    print(f"2. boton 'Buscar ahora': arriba y={arriba}, abajo y={abajo}, "
          f"alto visible={alto}")
    assert arriba > alto, "sin esto la prueba no probaria nada: ya se veia"
    assert abajo < alto, "sigue sin verse aunque bajes del todo"

    # --- 3. con sitio de sobra, ni barra ni registro encogido -------------
    app.geometry("1000x1000")
    app.update()
    principal, barra_principal = pestana(app.tab_main)
    print("3. Sincronizacion a 1000x1000 -> barra:",
          bool(barra_principal.winfo_ismapped()),
          "| alto del registro:", app.log_text.winfo_height())
    assert not barra_principal.winfo_ismapped(), "no hacia falta barra"
    assert app.log_text.winfo_height() > 300, \
        "con sitio de sobra el registro deberia seguir estirandose"

    # --- 4. la rueda mueve la pestana que hay debajo del raton ------------
    app.geometry("700x400")
    app.update()
    ajustes, _ = pestana(app.tab_settings)
    ajustes.yview_moveto(0.0)
    app.update()
    rueda(ajustes)
    print("4. tras una vuelta de rueda:", ajustes.yview())
    assert ajustes.yview()[0] > 0.0, "la rueda no ha desplazado nada"

    # --- 5. dentro de una lista larga, gana la lista ----------------------
    # Las playlists viven en un recuadro con scroll propio, dentro de una
    # pestana que tambien lo tiene: la de dentro es la que debe moverse.
    if not app.itunes_ok:
        # Sin iTunes esa pestana no se anade, asi que aqui no hay nada que
        # mirar. Esta parte solo corre en el equipo donde se usa la app.
        print("5. sin iTunes en este equipo: la pestana Publicar no esta")
    else:
        publicar, _ = pestana(app.tab_publish)
        app._set_publish_lists([f"Lista {i:02d}" for i in range(30)])
        app.update()
        publicar.yview_moveto(0.0)
        app.pub_canvas.yview_moveto(0.0)
        app.update()
        dentro = app.pub_canvas.winfo_rooty() - publicar.winfo_rooty() + 20
        app._on_wheel(type("Ev", (), {
            "x_root": app.pub_canvas.winfo_rootx() + 20,
            "y_root": publicar.winfo_rooty() + dentro,
            "delta": -120, "widget": app.pub_canvas})())
        app.update()
        print("5. rueda sobre la lista -> lista:", app.pub_canvas.yview(),
              "| pestana:", publicar.yview())
        assert app.pub_canvas.yview()[0] > 0.0, "la lista no se ha movido"
        assert publicar.yview()[0] == 0.0, "se ha movido la pestana en su lugar"

    # --- 6. el visor deja copiar el informe entero de una vez -------------
    # Es como se pasa el resultado de "Examinar un fichero" a otro sitio.
    informe = "\n".join(["ANTES", "=" * 20, "Stream 0  audio  alac", ""])
    visor = TextWindow(app, Path("prueba.m4a"), contenido=informe)
    app.update()
    visor._copiar()
    app.update()
    print("6. copiado al portapapeles:", repr(app.clipboard_get()[:30]), "...")
    assert app.clipboard_get() == informe, "no ha copiado el informe entero"
    assert visor.aviso_copia.cget("text") == "Copiado."
    visor.destroy()

    print()
    print("VENTANA OK")
finally:
    app.destroy()
