from collections import Counter
import threading

import pytest

from ordverk.catalog import Catalog
from ordverk.diffs import parse_diff, patch_text
from ordverk.exporters import FORMATS, prepare, write_exports
from ordverk.importers import Cancelled


SOURCES = {
    "sv.po": b'msgid "Save"\nmsgstr "Spara"\n\nmsgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] "%d fil"\nmsgstr[1] "%d filer"\n',
    "sv.ts": b'<TS language="sv_SE"><context><name>Menu</name><message><source>Save</source><translation>Spara</translation></message></context></TS>',
    "sv.xlf": b'<xliff version="1.2"><file source-language="en" target-language="sv"><body><trans-unit id="save"><source>Save</source><target>Spara</target></trans-unit></body></file></xliff>',
    "sv.xliff": b'<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="sv"><file id="f"><unit id="save"><segment><source>Save</source><target>Spara</target></segment></unit></file></xliff>',
    "sv.json": b'[{"source":"Save","target":"Spara","context":"Menu"}]',
}


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / "cache"))


@pytest.mark.parametrize("filename", SOURCES)
@pytest.mark.parametrize("kind", ["po", "ts", "xliff12", "xliff2", "json"])
def test_export_between_all_formats_preserves_every_source_and_translation(filename, kind):
    catalog = Catalog(filename, SOURCES[filename])
    original = catalog.render()
    result = prepare(catalog, kind)
    reopened = Catalog(result.name, result.data)
    def pairs(catalog):
        return Counter((unit.source_for(v), target) for unit in catalog.units for v, target in enumerate(unit.targets))
    assert pairs(catalog) == pairs(reopened)
    assert catalog.render() == original and not catalog.dirty


@pytest.mark.parametrize("filename", SOURCES)
def test_original_export_preserves_bytes_and_unsaved_edits(filename):
    catalog = Catalog(filename, SOURCES[filename])
    result = prepare(catalog)
    if filename.endswith(".po"):
        assert Catalog(result.name, result.data).po.metadata["X-Generator"] == "Ordverk 0.2"
        assert catalog.raw == SOURCES[filename] and not catalog.dirty
    else:
        assert result.data == SOURCES[filename]
    catalog.units[0].edit(0, "Spara nu")
    result = prepare(catalog)
    assert Catalog(result.name, result.data).units[0].targets == ["Spara nu"]
    assert catalog.dirty


def test_diff_uses_import_baseline_after_saving_and_can_be_applied(tmp_path):
    path = tmp_path / "sv.po"
    path.write_bytes(SOURCES["sv.po"])
    catalog = Catalog.open(path)
    catalog.units[0].edit(0, "Spara allt")
    catalog.save()
    result = prepare(catalog, "diff")
    patch = parse_diff(result.data.decode())[0]
    assert patch_text(SOURCES["sv.po"].decode(), patch) == catalog.render().decode()
    assert not catalog.dirty and result.name == "sv.po.diff"


def test_diff_with_no_final_newline_roundtrips():
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""}]')
    catalog.units[0].edit(0, "Spara")
    result = prepare(catalog, "diff")
    assert b"\\ No newline at end of file" in result.data
    patch = parse_diff(result.data.decode())[0]
    assert patch_text(catalog.import_raw.decode(), patch) == catalog.render().decode()


def test_export_never_replaces_original_files_or_duplicate_names(tmp_path):
    path = tmp_path / "sv.po"
    path.write_bytes(SOURCES["sv.po"])
    catalog = Catalog.open(path)
    catalog.units[0].edit(0, "Spara allt")
    export = prepare(catalog)
    written = write_exports([export, export], tmp_path)
    assert [path.name for path in written] == ["sv-2.po", "sv-3.po"]
    assert path.read_bytes() == SOURCES["sv.po"]
    assert all(p.read_bytes() == export.data for p in written)
    assert catalog.dirty
    assert not list(tmp_path.glob(".ordverk-export-*"))


def test_cancelled_export_writes_nothing(tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        write_exports([prepare(Catalog("sv.po", SOURCES["sv.po"]))], tmp_path, event)
    assert not list(tmp_path.glob("*.po"))


def test_conversion_rejects_unknown_json_source_and_invalid_inline_edits():
    with pytest.raises(ValueError, match="käll-JSON"):
        prepare(Catalog("sv.json", b'{"save":"Spara"}'), "po")
    catalog = Catalog("sv.xlf", b'<xliff version="1.2"><file><body><trans-unit id="a"><source>Save <x id="1"/></source><target/></trans-unit></body></file></xliff>')
    catalog.units[0].edit(0, "Spara utan kod")
    with pytest.raises(ValueError, match="inlinekoder"):
        prepare(catalog, "json")


def test_inline_codes_export_as_markup_text():
    catalog = Catalog("sv.xlf", b'<xliff version="1.2"><file><body><trans-unit id="a"><source>Save <x id="1"/></source><target>Spara <x id="1"/></target></trans-unit></body></file></xliff>')
    for kind, _, _ in FORMATS:
        if kind in {"original", "diff", "xliff12"}:
            continue
        result = prepare(catalog, kind)
        assert '<x id="1"/>' in Catalog(result.name, result.data).units[0].targets[0]


def test_converted_json_retains_review_and_notes_after_edit_and_save(tmp_path):
    catalog = Catalog("sv.po", b'#. Knapp i menyn\nmsgid "Save"\nmsgstr "Spara"\n')
    result = prepare(catalog, "json")
    path = write_exports([result], tmp_path)[0]
    reopened = Catalog.open(path)
    assert reopened.units[0].reviewed and "Knapp i menyn" in reopened.units[0].notes
    reopened.units[0].edit(0, "Spara allt")
    reopened.save()
    assert not Catalog(path.name, path.read_bytes()).units[0].reviewed


def test_pot_to_po_keeps_plural_structure():
    catalog = Catalog("template.pot", SOURCES["sv.po"])
    result = prepare(catalog, "po")
    assert result.name == "template.po"
    assert Catalog(result.name, result.data).units[1].source_plural == "%d files"


def test_foreign_targets_are_not_mislabeled_as_swedish():
    catalog = Catalog("de.ts", b'<TS language="de"><context><name>Menu</name><message><source>Save</source><translation>Speichern</translation></message></context></TS>')
    with pytest.raises(ValueError, match="svensk arbetskopia"):
        prepare(catalog, "po")
    assert prepare(catalog).data == catalog.raw


def test_diff_retains_original_filename_after_save_as(tmp_path):
    catalog = Catalog("original.po", SOURCES["sv.po"])
    catalog.units[0].edit(0, "Spara allt")
    catalog.save(tmp_path / "changed.po")
    patch = parse_diff(prepare(catalog, "diff").data.decode())[0]
    assert patch.before == "original.po" and patch.after == "changed.po"
