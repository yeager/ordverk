"""Run with xvfb-run -a .venv/bin/python tests/gtk_smoke.py. No network or paid API calls."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
from ordverk.ai_providers import PROVIDERS
from ordverk.selection import FILTERS
from ordverk.exporters import FORMATS
from ordverk.pot_dialog import load_template
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
previous_hook = sys.excepthook
sys.excepthook = lambda *exc: errors.append(str(exc[1]))
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
preferences.translator_name.set_text("Daniel Nylander")
preferences.translator_email.set_text("daniel@example.org")
identity_settings = preferences.collect()
assert identity_settings.translator_name == "Daniel Nylander"
assert identity_settings.translator_email == "daniel@example.org"
for index, provider in enumerate(PROVIDERS[1:], 1):
    preferences.key.set_text("must-not-follow-provider-change")
    preferences.provider.set_selected(index)
    selected_settings = preferences.collect()
    assert selected_settings.ai_provider == provider.id
    assert selected_settings.base_url == provider.base_url
    assert selected_settings.model == provider.model
    assert selected_settings.api_key_env == provider.key_env
    assert preferences.key.get_text() == ""
    assert preferences.model.get_sensitive() == (provider.protocol != "deepl")
preferences.close()
window.search.set_text("Open a file")
window.filter_units()
assert window.unit_store.get_n_items() == 1 and window.unit_store.get_item(0).number == 2
window.search.set_text("")
window.filter_units()
assert window.unit_store.get_item(7).number == 8
window.filter.set_selected(next(i for i, option in enumerate(FILTERS) if option[0] == "changed"))
assert window.unit_store.get_n_items() == 1 and window.unit_store.get_item(0).number == 2
selected_scope = PretranslateDialog(window)
selected_scope.scope.set_selected(next(i for i, option in enumerate(selected_scope.scopes) if option[0] == "changed"))
assert selected_scope.selected_keys() == {(id(window.catalog), window.unit.key)}
assert "1 av 8" in selected_scope.scope_count.get_subtitle()
selected_scope.close()
window.filter.set_selected(0)
window.preview_ai_context()
pump_until(lambda: not window.busy)

# A delayed proposal, or its button retained after editing, cannot overwrite new work.
previous_translator, previous_settings = window.translator, window.settings
window.settings = Settings(base_url="http://localhost:1234/v1", model="test")
contexts = []
def suggest(_unit, _variant, _cancel, context):
    contexts.append(context)
    return SimpleNamespace(translation="AI-förslag", explanation="Test", model="test", issues=[])
window.translator = SimpleNamespace(suggest=suggest)
window.request_ai()
pump_until(lambda: not window.busy)
assert contexts[0]["file"] == window.catalog.name
child = window.proposal_box.get_first_child()
while child and not (isinstance(child, Gtk.Button) and child.get_label() == "Använd AI-förslag"):
    child = child.get_next_sibling()
assert child is not None
window.set_target("Min manuella ändring")
child.emit("clicked")
assert window.unit.targets[0] == "Min manuella ändring"
window.set_target("Öppna en fil")
window.translator, window.settings = previous_translator, previous_settings
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
header = window.edit_po_header()
header.fields["Project-Id-Version"].set_text("GTK-test 0.2")
header.comment.get_buffer().set_text("Svensk översättning av Daniel Nylander.")
header.other.get_buffer().set_text("X-Test-Field: Bevara detta")
header.apply()
assert window.catalog.po.metadata["Project-Id-Version"] == "GTK-test 0.2"
assert window.catalog.dirty

# Apply a template to the existing, unsaved PO work copy through the GTK workflow.
template_path = Path(temporary.name) / "new.pot"
template = Catalog("template.pot", window.catalog.render())
template.po.metadata["POT-Creation-Date"] = "2026-09-20 15:00+0200"
template_path.write_text(str(template.po), encoding=template.po.encoding)
load_template(window, window.catalog, template_path, fuzzy=False)
pump_until(lambda: not window.busy)
assert window.pot_preview.get_heading() == "Granska POT-uppdateringen"
window.pot_preview.response("apply")
assert window.catalog.po.metadata["POT-Creation-Date"] == "2026-09-20 15:00+0200"
assert window.catalog.units[1].targets == ["Öppna valfri fil"]
assert window.catalog.dirty

def find_button(widget, caption):
    if isinstance(widget, Gtk.Button) and widget.get_label() == caption:
        return widget
    child = widget.get_first_child()
    while child:
        found = find_button(child, caption)
        if found:
            return found
        child = child.get_next_sibling()

export = window.export_files()
assert export.format.get_model().get_n_items() == len(FORMATS)
directory = Path(temporary.name) / "export"
original_file_dialog = window.file_dialog
window.file_dialog = lambda _title, callback, **_options: callback(str(directory))
export.format.set_selected(next(i for i, option in enumerate(FORMATS) if option[0] == "ts"))
export.start()
pump_until(lambda: not window.busy)
find_button(export.preview, "Exportera filer").emit("clicked")
pump_until(lambda: not window.busy)
assert Catalog.open(directory / "sv.ts").units[1].targets == ["Öppna valfri fil"]
assert window.catalog.dirty  # Export is a copy; it must not acknowledge the working file as saved.
window.file_dialog = original_file_dialog
# A slow worker must not block the GTK loop; cancelling is cooperative.
gate = threading.Event()
window.job("Testar långsamt arbete", lambda cancel: gate.wait(0.2), lambda _: None)
pump_until(lambda: not window.busy)
assert not errors, errors

# All exit paths protect dirty files, including header-only changes and failed saves.
window.lookup_action("quit").activate(None)
dialog = window.close_dialog
assert dialog.get_default_response() == dialog.get_close_response() == "cancel"
assert dialog.get_response_label("save") == "Spara alla och avsluta"
window.on_close()
assert window.close_dialog is dialog
dialog.response("cancel")
assert not window.closed and window.catalog.dirty and window.close_dialog is None

# A cancelled destination chooser must leave the unsaved remote copy open.
remote = Catalog("remote.json", b'[{"source":"Save","target":""}]')
remote.units[0].edit(0, "Spara")
window.catalogs.append(remote)
window.refresh_files()
window.file_dialog = lambda *_args, **_kwargs: None
window.on_close()
window.close_dialog.response("save")
pump_until(lambda: not window.busy)
assert not window.closed and remote.dirty
assert not window.catalogs[0].dirty
assert Catalog.open(path).po.metadata["X-Test-Field"] == "Bevara detta"
window.file_dialog = original_file_dialog

# An external file change must stop Save All and keep remaining work in memory.
local = window.catalogs[0]
local.units[0].edit(0, "Spara igen")
saved_bytes = path.read_bytes()
path.write_bytes(b"Extern filversion")
window.on_close()
window.close_dialog.response("save")
pump_until(lambda: not window.busy)
assert errors and "utanför Ordverk" in errors[-1]
errors.clear()
assert not window.closed and local.dirty and remote.dirty
assert path.read_bytes() == b"Extern filversion"
path.write_bytes(saved_bytes)
remote_path = Path(temporary.name) / "remote.json"
window.file_dialog = lambda _title, callback, **_kwargs: callback(str(remote_path))
window.on_close()
window.close_dialog.response("save")
pump_until(lambda: window.closed)
assert Catalog.open(path).units[0].targets == ["Spara igen"]
assert Catalog.open(remote_path).units[0].targets == ["Spara"]

discard = Window(app, settings=settings, startup=False)
discard.catalogs = [Catalog.open(path)]
discard.catalogs[0].units[0].edit(0, "Ska kastas")
unchanged = path.read_bytes()
discard.on_close()
discard.close_dialog.response("discard")
assert discard.closed and path.read_bytes() == unchanged

busy = Window(app, settings=settings, startup=False)
busy.job("Pågående jobb", lambda _: time.sleep(.1), lambda _: None)
busy.on_close()
assert busy.close_dialog.get_heading() == "Arbete pågår" and not busy.closed
busy.close_dialog.response("cancel")
pump_until(lambda: not busy.busy)
busy.on_close()
assert busy.closed
# Import popup stays live; quality filters use exact keys and discard old results.
from ordverk.quality import Issue
quality_window = Window(app, settings=settings, startup=False)
quality_window.present()
quality_catalog = Catalog("quality.json", '[{"source":"Save %s","target":"nogrann"},{"source":"open","target":"Öppna"},{"source":"Close","target":"Stäng"}]'.encode())
quality_window.quality.spelling = lambda text, variant: ([Issue("warning", engine, "spelling", "Kontrollera stavningen: nogrann", variant) for engine in ("Hunspell", "Aspell")] if "nogrann" in text else [])
original_review = quality_window.quality.catalog
review_gate = threading.Event()
def delayed_review(*args, **kwargs):
    assert review_gate.wait(12)
    return original_review(*args, **kwargs)
quality_window.quality.catalog = delayed_review
second_quality_catalog = Catalog("another.json", b'[ {"source":"Save","target":""} ]')
quality_window.finish_import(ImportResult(catalogs=[quality_catalog, second_quality_catalog]))
assert quality_window.quality_running is quality_catalog
popup = next(w for w in Gtk.Window.get_toplevels() if w.get_title() == "Importstatistik" and w.get_transient_for() is quality_window)
assert len(quality_window.statistics_views) == 1
assert any("2 filer · 4 strängar" in text for text in widget_labels(popup))
assert any("Kvalitetsgranskning pågår" in text for text in widget_labels(popup))
quality_window.quality_meter.started -= 8
quality_window.tick_progress()
assert quality_window.quality_bar.get_visible()
assert "Avbryt kvalitetsgranskning" in widget_labels(popup)
review_gate.set()
pump_until(lambda: quality_window.quality_running is None)
assert any("3 kvalitetskontrollerade" in text and "1 med stavfel" in text and "2 med fel skiftläge" in text for text in widget_labels(popup))
quality_window.filter.set_selected(next(i for i, (key, _) in enumerate(FILTERS) if key == "quality-case"))
assert [row.number for row in quality_window.unit_store] == [1, 2]
quality_scope = PretranslateDialog(quality_window)
quality_scope.scope.set_selected(next(i for i, (key, _) in enumerate(quality_scope.scopes) if key == "quality-case"))
assert len(quality_scope.selected_keys()) == 2
quality_scope.close()
quality_window.set_target("Spara %s")
pump_until(lambda: quality_window.quality_index(quality_catalog).known(quality_catalog.units[0]))
assert [row.number for row in quality_window.unit_store] == [2]
assert any("0 med stavfel" in text and "1 med fel skiftläge" in text for text in widget_labels(popup))
popup.close()
assert not quality_window.statistics_views
# Cancelling leaves an explicit incomplete result in the popup.
def cancelled_review(*args, **kwargs):
    assert kwargs["cancel"].wait(12)
    return original_review(*args, **kwargs)
quality_window.quality.catalog = cancelled_review
cancel_catalog = Catalog("cancelled.json", b'[{"source":"Open","target":""}]')
quality_window.finish_import(ImportResult(catalogs=[cancel_catalog]))
quality_window.cancel_quality()
pump_until(lambda: quality_window.quality_running is None)
assert "avbröts" in quality_window.quality_index(cancel_catalog).state
quality_window.shutdown()
assert not errors, errors
sys.excepthook = previous_hook
app.quit()
temporary.cleanup()
print("GTK: import, redigering, sparning, granskning, statistik, förlopp, diff, urval, AI-kontext, PO-huvud, export och skydd vid avslut godkända.")
