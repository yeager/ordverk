"""Run with xvfb-run -a .venv/bin/python tests/gtk_smoke.py. No network or paid API calls."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
temporary = tempfile.TemporaryDirectory()
os.environ["ORDVERK_HOME"] = temporary.name
os.environ.setdefault("GSK_RENDERER", "cairo")

from ordverk.ui import Application, Window
from ordverk.catalog import Catalog
from ordverk.dialogs import Preferences, ImportGuide, PretranslateDialog
from ordverk.remote_dialogs import Connections, MailDialog
from ordverk.diff_dialog import DiffDialog
from ordverk.diffs import propose_diff, parse_diff
import difflib
from ordverk.importers import ImportResult
from ordverk.progress import ProgressState
from ordverk.settings import Settings
from gi.repository import GLib, Gtk


def pump_until(condition, timeout=12):
    end = time.monotonic() + timeout
    while not condition():
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if time.monotonic() > end:
            raise AssertionError("GTK operation timed out")
        time.sleep(0.01)
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)


app = Application()
app.register(None)
settings = Settings(auto_update_resources=False, show_import_guide=False, use_hunspell=False, use_aspell=False)
window = Window(app, settings=settings, startup=False)
errors = []
window.error = lambda text, **kwargs: errors.append(text)
window.present()


def widget_labels(widget):
    labels = [widget.get_label().replace("_", "")] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child:
        labels.extend(widget_labels(child))
        child = child.get_next_sibling()
    return labels


# Exercise actual GTK templates, including hidden dropdown and About pages.
assert "(Ingen)" in widget_labels(window.file_picker)
assert "Sök…" in widget_labels(window.file_picker)
about = window.about()
about_labels = widget_labels(about)
assert "Juridisk information" in about_labels
assert "Detaljer" in about_labels
assert not {"Legal", "Details", "Search…", "(None)"}.intersection(about_labels)
assert about.get_developer_name() == "Daniel Nylander"
assert about.get_copyright() == "© 2026 Daniel Nylander"
assert "Språkverktyg och språkresurser" in about_labels
for tool in ("swedish-tm", "swedish-foss-terminology", "l10n-lint", "svlang", "hunspell-sv", "aspell-sv"):
    assert any(tool in text for text in about_labels), tool
about.close()

example = Path(__file__).resolve().parents[1] / "examples/sv.po"
path = Path(temporary.name) / "sv.po"
path.write_bytes(example.read_bytes())
window.import_paths([str(path)])
pump_until(lambda: not window.busy)
assert window.unit.source == "Save"
window.selection.set_selected(1)
window.set_target("Öppna en fil")
assert window.unit.targets == ["Öppna en fil"]
assert window.catalog.dirty
window.save()
pump_until(lambda: not window.busy)
assert Catalog.open(path).units[1].targets == ["Öppna en fil"]
assert not window.catalog.dirty
assert window.unit.source == "Open a file"
window.mark_reviewed()
pump_until(lambda: not window.busy)
assert window.unit.reviewed
window.update_statistics()
assert window.statistics_labels["total"].get_label() == "8"
assert window.statistics_labels["translated"].get_label() == "7"
window.job_meter = ProgressState(started=time.monotonic() - 7.1)
window.job_meter.update(2, 5)
window.tick_progress()
assert window.job_bar.get_visible() and window.job_bar.get_fraction() == .4
window.job_meter = None
window.tick_progress()
assert not window.job_bar.get_visible()
preferences = Preferences(window)
preferences.present()
assert preferences.guide.get_active() is False
preferences.close()
guide = ImportGuide(window, [window.catalog])
guide.present()
guide.start()
assert not window.busy
window.show_statistics()
window.mark_visible(True)
assert len(window.marked) == 8
pretranslate = PretranslateDialog(window)
pretranslate.present()
assert pretranslate.scope.get_selected() == 2
pretranslate.close()
window.mark_visible(False)
assert not window.marked
connections = Connections(window)
connections.present()
for provider in range(4):
    connections.provider.set_selected(provider)
assert connections.project.get_title() == "Projekt-ID"
connections.close()
mail = MailDialog(window)
mail.present()
assert mail.to.get_text() == "robot@translationproject.org"
assert mail.subject.get_text() == window.catalog.name
mail.close()
before = window.catalog.render().decode()
after = before.replace('msgstr "Öppna en fil"', 'msgstr "Öppna vald fil"')
patch = parse_diff("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/sv.po", tofile="b/sv.po")))[0]
diff = DiffDialog(window, propose_diff(window.catalog, patch))
diff.present()
assert diff.unit.targets == ["Öppna vald fil"]
diff.text.get_buffer().set_text("Öppna valfri fil")
diff.apply()
pump_until(lambda: not window.busy)
assert window.catalog.units[1].targets == ["Öppna valfri fil"]
assert not window.catalog.units[1].reviewed
# A slow worker must not block the GTK loop; cancelling is cooperative.
gate = threading.Event()
window.job("Testar långsamt arbete", lambda cancel: gate.wait(0.2), lambda _: None)
pump_until(lambda: not window.busy)
assert not errors, errors
window.shutdown()
app.quit()
temporary.cleanup()
print("GTK: import, redigering, sparning, granskning, statistik, förlopp, diff, föröversättning, anslutningar och TP godkända.")
