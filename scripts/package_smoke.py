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
    from gi.repository import Adw, GLib, Gtk
    import ordverk
    assert not str(Path(ordverk.__file__).resolve()).startswith(str(Path.cwd() / "src"))
    assert __version__ == "0.4"
    app = Application()
    app.register(None)
    window = Window(app, settings=Settings(auto_update_resources=False), startup=False)
    window.present()
    for _ in range(30):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
    assert Gtk.IconTheme.get_for_display(window.get_display()).has_icon("io.github.yeager.Ordverk")
    def labels(widget):
        result = [widget.get_label().replace("_", "")] if isinstance(widget, Gtk.Label) else []
        child = widget.get_first_child()
        while child:
            result.extend(labels(child))
            child = child.get_next_sibling()
        return result
    picker_labels = labels(window.file_picker)
    assert "(Ingen)" in picker_labels and "Sök…" in picker_labels, picker_labels
    about = Adw.AboutWindow(application_name="Ordverk", license_type=Gtk.License.GPL_3_0)
    about_labels = labels(about)
    assert "Juridisk information" in about_labels and "Detaljer" in about_labels, about_labels
    about.close()
    window.shutdown()
    app.quit()
    print(f"Installerad Ordverk {__version__}: GTK-fönster, programikon och svenska standardtexter fungerar.")
