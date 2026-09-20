import copy
import difflib
import json
import threading
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from ordverk.catalog import Catalog, Unit
from ordverk.delivery import prepare_mail, tp_defaults
from ordverk.diffs import parse_diff, propose_diff
from ordverk.quality import Issue, Quality
from ordverk.resources import ResourceStore
from ordverk.settings import Settings
from ordverk.workflow import apply_changes, batch_translate


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / "cache"))


def test_json_state_survives_reopen_and_external_changes_invalidate(tmp_path):
    path, source = tmp_path / "sv.json", tmp_path / "en.json"
    path.write_text('{"save":"Spara"}')
    source.write_text('{"save":"Save"}')
    catalog = Catalog.open(path)
    catalog.attach_reference(source)
    catalog.units[0].reviewed = True
    catalog.save()
    reopened = Catalog.open(path)
    assert reopened.units[0].reviewed and reopened.units[0].source == "Save"
    source.write_text('{"save":"Save file"}')
    changed = Catalog.open(path)
    assert not changed.units[0].reviewed and changed.units[0].source == "Save file"
    path.write_text('{"save":"Spara allt"}')
    assert not Catalog.open(path).units[0].reviewed


def test_source_json_copy_remembers_its_sources(tmp_path):
    catalog = Catalog("en.json", b'{"save":"Save"}').swedish_copy()
    catalog.units[0].edit(0, "Spara")
    path = tmp_path / "sv.json"
    catalog.save(path)
    assert Catalog.open(path).units[0].source == "Save"
    assert not Catalog.open(path).units[0].source_is_key


def test_pretranslation_selection_overwrite_and_term_conflicts(tmp_path):
    store = ResourceStore()
    terms = tmp_path / "terms.csv"
    terms.write_text('source,canonical,confidence\nSave,Spara,1\nCancel,Avbryt,1\nView,Visa,1\nView,Vy,1\n')
    store.import_terms(terms)
    catalog = Catalog("sv.json", b'[{"source":"Save","target":"Gammalt"},{"source":"Cancel","target":""},{"source":"View","target":""}]')
    quality = Quality(Settings(use_hunspell=False, use_aspell=False), store)
    selected = {(id(catalog), catalog.units[0].key)}
    assert batch_translate([catalog], store, quality, selected=selected)[0] == []
    changes, _ = batch_translate([catalog], store, quality, selected=selected, overwrite=True, method="resources")
    assert len(changes) == 1
    assert apply_changes(changes) == (1, 0)
    assert catalog.units[0].targets == ["Spara"]
    assert catalog.units[1].targets == [""]
    assert store.exact_translation("View") is None


def test_exact_conflicts_beyond_ui_limit_and_context_resolution():
    store = ResourceStore()
    for i in range(70):
        store.remember("Open", "Öppna", "verb", str(i))
    store.remember("Open", "Öppen", "adjective", "other")
    assert store.exact_translation("Open") is None
    assert store.exact_translation("Open", "adjective")[0] == "Öppen"


def test_ai_only_really_uses_ai_even_with_memory():
    store = ResourceStore()
    store.remember("Save", "Spara")
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""}]')
    translator = SimpleNamespace(suggest=lambda *_: SimpleNamespace(translation="Spara fil", model="test", issues=[]))
    changes, _ = batch_translate([catalog], store, None, translator, method="ai")
    assert changes[0].after == "Spara fil" and changes[0].origin == "AI · test"


def test_full_review_uses_language_and_both_spellers(monkeypatch):
    store = ResourceStore()
    quality = Quality(Settings(), store)
    calls = []
    def spelling(text, variant):
        calls.append(text)
        return [Issue("warning", name, "spelling", "Kontrollera stavningen: nogrann") for name in ("Hunspell", "Aspell")]
    monkeypatch.setattr(quality, "spelling", spelling)
    catalog = Catalog("sv.json", json.dumps([{"source":"Check the filename", "target":"Kontrollera fil namnet nogrann"}, {"source":"Save", "target":"Spara"}]).encode())
    issues = quality.catalog(catalog)
    assert {"svlang", "Hunspell", "Aspell"} <= {i.tool for i in issues}
    assert len(calls) == 1
    assert all(i.key == catalog.units[0].key for i in issues if i.tool in {"Hunspell", "Aspell"})


def patch_for(before, after):
    return parse_diff("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/sv.po", tofile="b/sv.po")))[0]


@pytest.mark.parametrize("reviewed", [False, True])
def test_diff_review_and_manual_edit_roundtrip(tmp_path, reviewed):
    before = 'msgid "Save"\nmsgstr "Spara"\n\nmsgid "Open"\nmsgstr ""\n'
    after = before.replace('msgstr ""', 'msgstr "Öppna"')
    path = tmp_path / "sv.po"
    path.write_text(before)
    catalog = Catalog.open(path)
    proposal = propose_diff(catalog, patch_for(before, after))
    assert len(proposal.changed) == 1
    proposal.changed[0].edit(0, "Öppna fil")
    proposal.changed[0].reviewed = reviewed
    proposal.apply()
    assert catalog.dirty and path.read_text() == before
    catalog.save()
    assert not catalog.dirty
    loaded = Catalog.open(path)
    assert loaded.units[1].targets == ["Öppna fil"] and loaded.units[1].reviewed == reviewed


def test_diff_refuses_conflicts_traversal_and_concurrent_edit():
    before = 'msgid "Save"\nmsgstr ""\n'
    after = before.replace('msgstr ""', 'msgstr "Spara"')
    catalog = Catalog("sv.po", before.encode())
    patch = patch_for(before, after)
    proposal = propose_diff(catalog, patch)
    catalog.units[0].edit(0, "Spara allt")
    with pytest.raises(ValueError, match="ändrats"):
        proposal.apply()
    with pytest.raises(ValueError, match="matchar inte"):
        propose_diff(catalog, patch)
    with pytest.raises(ValueError, match="filsökväg"):
        parse_diff('--- a/../../outside.po\n+++ b/../../outside.po\n@@ -1 +1 @@\n-x\n+y\n')


def test_diff_handles_no_newline_and_inserted_units():
    patch = parse_diff('--- a/sv.json\n+++ b/sv.json\n@@ -1 +1 @@\n-{"x":"Old"}\n\\ No newline at end of file\n+{"x":"New"}\n\\ No newline at end of file\n')[0]
    catalog = Catalog("sv.json", b'{"x":"Old"}')
    assert propose_diff(catalog, patch).changed[0].targets == ["New"]


def test_tp_mail_is_exact_attachment_with_filename_subject():
    raw = b'msgid "Save"\nmsgstr "Spara"\n'
    catalog = Catalog("example-1.2.sv.po", raw)
    recipient, subject = tp_defaults(catalog)
    assert recipient == "robot@translationproject.org" and subject == catalog.name
    draft, attachment = prepare_mail(catalog, recipient, subject)
    mail = BytesParser(policy=policy.default).parsebytes(draft.read_bytes())
    assert mail["To"] == recipient and mail["Subject"] == subject
    part = next(mail.iter_attachments())
    assert part.get_filename() == catalog.name and part.get_payload(decode=True) == raw
    assert attachment.read_bytes() == raw
    with pytest.raises(ValueError):
        prepare_mail(catalog, "some@host\nBcc: other@host", subject)


def test_replacing_json_reference_refreshes_sources_and_review(tmp_path):
    catalog = Catalog("sv.json", b'{"save":"Spara","cancel":"Avbryt"}')
    source = tmp_path / "en.json"
    source.write_text('{"save":"Save","cancel":"Cancel"}')
    catalog.attach_reference(source)
    catalog.units[0].reviewed = True
    source.write_text('{"save":"Save file"}')
    catalog.attach_reference(source)
    assert catalog.units[0].source == "Save file" and not catalog.units[0].reviewed
    assert catalog.units[1].source_is_key and catalog.units[1].targets == ["Avbryt"]


def test_pending_proposal_cannot_apply_to_replaced_catalog_units():
    store = ResourceStore()
    store.remember("Save", "Spara")
    before = 'msgid "Save"\nmsgstr ""\n'
    catalog = Catalog("sv.po", before.encode())
    quality = Quality(Settings(use_hunspell=False, use_aspell=False), store)
    changes, _ = batch_translate([catalog], store, quality)
    after = 'msgid "Close"\nmsgstr ""\n\n' + before
    propose_diff(catalog, patch_for(before, after)).apply()
    assert apply_changes(changes) == (0, 1)
    assert catalog.units[0].source == "Close" and catalog.units[0].targets == [""]


def test_cancelled_batch_does_not_copy_or_translate_any_units():
    class NoWork:
        def __deepcopy__(self, _memo):
            raise AssertionError("Cancelled work must not start snapshotting")
    catalog = Catalog("sv.po", b'msgid "Save"\nmsgstr ""\n')
    catalog.units[0].binding = NoWork()
    cancel = threading.Event()
    cancel.set()
    assert batch_translate([catalog], None, None, cancel=cancel)[0] == []
