"""Exercise the installed system package, without importing from the source checkout."""
import os
from pathlib import Path
import tempfile

# Packages must stay Swedish even on a machine with an English/C environment.
os.environ["LC_ALL"] = "C.UTF-8"
os.environ["LANGUAGE"] = "en"

with tempfile.TemporaryDirectory() as cache:
    os.environ["ORDVERK_HOME"] = cache
    from ordverk import __version__
    from ordverk.ui import Application, Window
    from ordverk.settings import Settings
    from gi.repository import GLib, Gtk
    import ordverk
    assert not str(Path(ordverk.__file__).resolve()).startswith(str(Path.cwd() / "src"))
    assert __version__ == "0.2"
    app = Application()
    app.register(None)
    window = Window(app, settings=Settings(auto_update_resources=False), startup=False)
    window.present()
    for _ in range(30):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
    assert Gtk.IconTheme.get_for_display(window.get_display()).has_icon("io.github.yeager.Ordverk")
    for domain, source, target in [
        ("gtk40", "Search", "Sök"),
        ("gtk40", "Copy", "Kopiera"),
        ("libadwaita", "Legal", "Juridisk information"),
        ("libadwaita", "Details", "Detaljer"),
    ]:
        assert GLib.dgettext(domain, source) == target, (domain, source)
    window.shutdown()
    app.quit()
    print("Installerad Ordverk 0.2: GTK-fönster, programikon och svenska standardtexter fungerar.")
