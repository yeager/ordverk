import threading

import pytest

from ordverk.catalog import Catalog
from ordverk.consistency import parse_checked, verify_written
from ordverk.exporters import prepare, write_exports
from ordverk.importers import Cancelled
from ordverk.pot_merge import prepare_merge


PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: sv\\n"
"POT-Creation-Date: 2025-01-01 12:00+0000\\n"
"Last-Translator: Daniel Nylander <daniel@example.org>\\n"

#: old.c:1
msgid "Save the current document"
msgstr "Spara aktuellt dokument"

msgid "Keep"
msgstr "Behåll"

msgid "Completely obsolete action"
msgstr "Föråldrad åtgärd"
'''.encode()

POT = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"POT-Creation-Date: 2026-09-20 12:00+0000\\n"

#: new.c:23
msgid "Save the current document now"
msgstr ""

msgid "Keep"
msgstr ""

msgid "Unrelated brand new option"
msgstr ""
'''.encode()


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / "cache"))


def test_pot_merge_preserves_translations_updates_date_and_defers_writing(tmp_path):
    path = tmp_path / "sv.po"
    path.write_bytes(PO)
    catalog = Catalog.open(path)
    proposal = prepare_merge(catalog, "new.pot", POT)
    assert path.read_bytes() == PO and not catalog.dirty
    assert proposal.retained == 1 and proposal.added == 2 and proposal.removed == 2
    assert proposal.fuzzy == 1
    proposal.apply()
    assert catalog.dirty and path.read_bytes() == PO
    by_source = {unit.source: unit for unit in catalog.units}
    assert by_source["Keep"].targets == ["Behåll"] and by_source["Keep"].reviewed
    changed = by_source["Save the current document now"]
    assert changed.targets == ["Spara aktuellt dokument"] and not changed.reviewed
    assert changed.references == "new.c:23"
    assert catalog.po.metadata["POT-Creation-Date"] == "2026-09-20 12:00+0000"
    assert catalog.po.metadata["Last-Translator"] == "Daniel Nylander <daniel@example.org>"
    assert catalog.po.metadata["X-Generator"] == "Ordverk 0.2"
    assert any(entry.obsolete and entry.msgid == "Completely obsolete action" for entry in catalog.po)
    catalog.save()
    assert not catalog.dirty and catalog.import_raw == PO
    assert Catalog.open(path).po.metadata["POT-Creation-Date"] == "2026-09-20 12:00+0000"


def test_pot_merge_can_disable_fuzzy_and_rejects_concurrent_edits():
    catalog = Catalog("sv.po", PO)
    proposal = prepare_merge(catalog, "new.pot", POT, fuzzy=False)
    assert proposal.fuzzy == 0
    assert next(unit for unit in proposal.candidate.units if unit.source.endswith("now")).targets == [""]
    catalog.units[1].edit(0, "Behåll allt")
    with pytest.raises(ValueError, match="ändrats"):
        proposal.apply()
    assert catalog.units[1].targets == ["Behåll allt"]


def test_pot_merge_uses_unsaved_translations_and_handles_missing_date():
    catalog = Catalog("sv.po", PO)
    catalog.units[1].edit(0, "Behåll allt")
    proposal = prepare_merge(catalog, "new.pot", POT.replace(b'"POT-Creation-Date: 2026-09-20 12:00+0000\\n"\n', b""))
    proposal.apply()
    assert next(unit for unit in catalog.units if unit.source == "Keep").targets == ["Behåll allt"]
    assert catalog.po.metadata["POT-Creation-Date"] == "2025-01-01 12:00+0000"


def test_cancelled_pot_merge_does_not_change_catalog():
    catalog = Catalog("sv.po", PO)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        prepare_merge(catalog, "new.pot", POT, cancel=cancel)
    assert not catalog.dirty


def test_save_verifies_written_bytes_before_marking_work_saved(tmp_path, monkeypatch):
    import ordverk.catalog as module
    path = tmp_path / "sv.json"
    path.write_bytes(b'[{"source":"Save","target":""}]')
    catalog = Catalog.open(path)
    catalog.units[0].edit(0, "Spara")
    original_write = module.atomic_write
    def corrupted_write(destination, data, mode):
        original_write(destination, data, mode)
        if destination == path:
            destination.write_bytes(b"skadad fil")
    monkeypatch.setattr(module, "atomic_write", corrupted_write)
    with pytest.raises(ValueError, match="Konsistenskontrollen"):
        catalog.save()
    assert catalog.dirty and catalog.units[0].targets == ["Spara"]


def test_save_rejects_a_serializer_that_loses_strings(tmp_path):
    path = tmp_path / "sv.json"
    data = b'[{"source":"Save","target":""},{"source":"Open","target":""}]'
    path.write_bytes(data)
    catalog = Catalog.open(path)
    catalog.render = lambda: b'[{"source":"Save","target":""}]'
    with pytest.raises(ValueError, match="antal strängar"):
        catalog.save()
    assert path.read_bytes() == data


def test_export_reads_back_the_published_file(tmp_path, monkeypatch):
    import ordverk.exporters as module
    original_link = module.os.link
    def corrupt_link(source, destination):
        original_link(source, destination)
        destination.write_bytes(b"skadad export")
    monkeypatch.setattr(module.os, "link", corrupt_link)
    export = prepare(Catalog("sv.json", b'[{"source":"Save","target":"Spara"}]'))
    with pytest.raises(ValueError, match="Konsistenskontrollen"):
        write_exports([export], tmp_path)


def test_gettext_rejects_invalid_format_in_reviewed_translation():
    data = b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n#, c-format\nmsgid "Save %s"\nmsgstr "Spara %d"\n'
    with pytest.raises(ValueError, match="Gettexts konsistenskontroll"):
        parse_checked("sv.po", data)


def test_inline_reordering_survives_semantic_roundtrip_check(tmp_path):
    catalog = Catalog("sv.xlf", b'<xliff version="1.2"><file><body><trans-unit id="a"><source>A <x id="a"/> B <x id="b"/></source><target/></trans-unit></body></file></xliff>')
    catalog.units[0].edit(0, "B ⟦2⟧ A ⟦1⟧")
    catalog.save(tmp_path / "sv.xlf")
    assert not catalog.dirty
    verify_written(tmp_path / "sv.xlf", catalog.raw)
