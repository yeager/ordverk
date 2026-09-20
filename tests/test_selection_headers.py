import difflib
import json

import pytest

from ordverk.catalog import Catalog, Unit
from ordverk.diffs import parse_diff, propose_diff
from ordverk.po_header import stamp_translator, translator_identity, update_header
from ordverk.selection import FILTERS, matches
from ordverk.settings import Settings


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / ".ordverk"))


def test_all_selection_presets_and_partially_translated_forms():
    unit = Unit("x", "%n file", ["En fil", ""], variants=["Ental", "Flertal"])
    unit.edit(0, "En vald fil")
    expected = {"all", "untranslated", "partial", "changed", "unsaved", "plural", "marked"}
    assert {key for key, _ in FILTERS if matches(unit, key, marked=True)} == expected
    unit.edit(1, "Flera filer")
    assert matches(unit, "translated") and matches(unit, "needs-review")
    unit.reviewed = True
    assert matches(unit, "reviewed") and not matches(unit, "needs-review")


def test_import_baseline_survives_save_and_resets_on_reimport(tmp_path):
    path = tmp_path / "sv.json"
    path.write_text('[{"source":"Save","target":""}]')
    catalog = Catalog.open(path)
    original = catalog.import_raw
    catalog.units[0].edit(0, "Spara")
    catalog.units[0].reviewed = True
    catalog.save()
    assert catalog.import_raw == original
    assert not catalog.dirty and not matches(catalog.units[0], "unsaved")
    assert matches(catalog.units[0], "changed")
    reopened = Catalog.open(path)
    assert reopened.units[0].reviewed
    assert matches(reopened.units[0], "unchanged")


def test_undo_to_import_value_and_new_swedish_copy_baseline():
    unit = Unit("x", "Save", [""])
    unit.edit(0, "Spara")
    unit.edit(0, "")
    assert not unit.changed_since_import
    copy = Catalog("en.json", b'{"save":"Save"}').swedish_copy()
    assert all(not unit.changed_since_import for unit in copy.units)


def test_diff_insertion_does_not_mark_shifted_po_units_as_changed(tmp_path):
    original = 'msgid "Save"\nmsgstr ""\n'
    after = 'msgid "Close"\nmsgstr ""\n\n' + original
    catalog = Catalog("sv.po", original.encode())
    patch = parse_diff("".join(difflib.unified_diff(original.splitlines(True), after.splitlines(True),
                        fromfile="a/sv.po", tofile="b/sv.po")))[0]
    propose_diff(catalog, patch).apply()
    assert matches(catalog.units[0], "new") and matches(catalog.units[0], "changed")
    assert matches(catalog.units[1], "unchanged")
    catalog.save(tmp_path / "sv.po")
    assert matches(catalog.units[0], "new")
    assert catalog.import_raw == original.encode()


def test_diff_preserves_duplicate_source_baselines():
    original = '<TS><context><name>Test</name><message><source>Save</source><translation>A</translation></message><message><source>Save</source><translation>B</translation></message></context></TS>\n'
    after = original.replace('>B<', '>C<')
    catalog = Catalog("sv.ts", original.encode())
    patch = parse_diff("".join(difflib.unified_diff(original.splitlines(True), after.splitlines(True),
                       fromfile="a/sv.ts", tofile="b/sv.ts")))[0]
    proposal = propose_diff(catalog, patch)
    assert len(proposal.changed) == 1
    proposal.apply()
    assert not catalog.units[0].changed_since_import
    assert catalog.units[1].changed_since_import
    assert catalog.units[1].imported[2] == ("B",)


def test_header_edit_marks_file_dirty_without_editing_strings(tmp_path):
    path = tmp_path / "sv.po"
    path.write_text('msgid ""\nmsgstr ""\n"Language: sv\\n"\n"X-Custom: bevara\\n"\n\nmsgid "Save"\nmsgstr "Spara"\n')
    catalog = Catalog.open(path)
    metadata = {**catalog.po.metadata, "Project-Id-Version": "Projekt 2.0", "Last-Translator": "Daniel Nylander"}
    update_header(catalog, metadata, "Svensk översättning.")
    assert catalog.dirty and not any(unit.changed for unit in catalog.units)
    assert b"Projekt 2.0" not in path.read_bytes()
    catalog.save()
    reopened = Catalog.open(path)
    assert reopened.po.metadata["X-Custom"] == "bevara"
    assert reopened.po.metadata["Last-Translator"] == "Daniel Nylander"
    assert reopened.po.header == "Svensk översättning."


def test_translator_settings_saved_in_ordverk_and_used_in_po_header(tmp_path):
    settings = Settings(translator_name="Daniel Nylander", translator_email="daniel@example.org")
    settings.save()
    data = json.loads((tmp_path / ".ordverk/settings.json").read_text())
    assert data["translator_name"] == "Daniel Nylander"
    assert Settings.load().translator_email == "daniel@example.org"
    catalog = Catalog("sv.po", b'msgid "Save"\nmsgstr ""\n')
    stamp_translator(catalog, settings)
    assert catalog.po.metadata["Last-Translator"] == "Daniel Nylander <daniel@example.org>"
    assert catalog.po.metadata["PO-Revision-Date"]
    before = catalog.render()
    settings.update_po_header = False
    settings.translator_name = "Annan person"
    stamp_translator(catalog, settings)
    assert catalog.render() == before


@pytest.mark.parametrize("name,email", [("Daniel\nLanguage: de", ""), ("Daniel", "bad address"), ("Daniel", "x@y\nZ: v")])
def test_invalid_translator_fields_are_rejected(name, email):
    with pytest.raises(ValueError):
        translator_identity(name, email)


def test_multiline_header_values_are_rejected_without_changes():
    catalog = Catalog("sv.po", b'msgid "Save"\nmsgstr ""\n')
    with pytest.raises(ValueError):
        update_header(catalog, {"Last-Translator": "Name\nLanguage: de"}, "")
    assert not catalog.dirty


def test_save_stamp_updates_revision_but_keeps_template_date_and_unknown_identity():
    catalog = Catalog("sv.po", b'msgid ""\nmsgstr ""\n"PO-Revision-Date: gammalt\\n"\n"POT-Creation-Date: 2025-01-01 12:00+0000\\n"\n"Last-Translator: Befintlig person\\n"\n\nmsgid "Save"\nmsgstr ""\n')
    stamp_translator(catalog, Settings())
    assert catalog.po.metadata["PO-Revision-Date"] != "gammalt"
    assert catalog.po.metadata["POT-Creation-Date"] == "2025-01-01 12:00+0000"
    assert catalog.po.metadata["Last-Translator"] == "Befintlig person"


def test_json_reference_changes_count_as_unsaved_work(tmp_path):
    catalog = Catalog("sv.json", b'{"save":"Spara"}')
    source = tmp_path / "en.json"
    source.write_text('{"save":"Save"}')
    assert not catalog.dirty
    catalog.attach_reference(source)
    assert catalog.dirty and catalog.units[0].changed
    catalog.save(tmp_path / "sv.json")
    assert not catalog.dirty
    assert not Catalog.open(tmp_path / "sv.json").dirty


def test_dirty_hint_handles_undo_and_replaced_units():
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""},{"source":"Open","target":""}]')
    catalog.units[1].edit(0, "Spara")
    assert catalog.dirty and catalog.dirty
    catalog.units[1].edit(0, "")
    assert not catalog.dirty
    catalog.units[1].edit(0, "Spara")
    assert catalog.dirty
    catalog.units[1] = Unit("replacement", "Close", [""])
    assert not catalog.dirty
