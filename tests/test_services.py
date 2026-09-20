import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import polib
import pytest

from ordverk.catalog import Catalog, Unit
from ordverk.importers import import_sources, validate_url
from ordverk.llm import Translator
from ordverk.quality import Quality
from ordverk.resources import ResourceStore
from ordverk.settings import Settings
from ordverk.workflow import apply_changes, batch_translate


@pytest.fixture
def store(tmp_path):
    return ResourceStore(tmp_path / "cache")


@pytest.fixture
def quality(store):
    return Quality(Settings(use_hunspell=False, use_aspell=False), store)


def test_tm_context_fuzzy_and_import_is_idempotent(store, tmp_path):
    path = tmp_path / "tm.po"
    po = polib.POFile()
    po.metadata = {"Language": "sv"}
    po.extend([polib.POEntry(msgid="Save", msgstr="Spara", msgctxt="verb"),
               polib.POEntry(msgid="Open file", msgstr="Öppna fil"),
               polib.POEntry(msgid="Bad", msgstr="Fel", flags=["fuzzy"])])
    po.save(str(path))
    assert store.import_po(path) == 2
    assert store.import_po(path) == 2
    assert store.counts()["memory"] == 2
    assert store.memory("Save", "verb")[0].context == "verb"
    assert store.memory("Open a file")[0].target == "Öppna fil"
    assert not store.memory("Bad")


def test_tm_update_removes_old_values_and_updates_fts(store, tmp_path):
    path = tmp_path / "tm.po"
    path.write_text('msgid "Save"\nmsgstr "Spara"\n')
    store.import_po(path)
    path.write_text('msgid "Cancel"\nmsgstr "Avbryt"\n')
    store.import_po(path)
    assert store.counts()["memory"] == 1
    assert not store.memory("Save")
    assert store.memory("Cancel")[0].target == "Avbryt"


def test_terms_preserve_case_variants_and_rollback_bad_import(store, tmp_path):
    path = tmp_path / "terms.csv"
    path.write_text('source,canonical,confidence\nSave,Spara,1.0\nsave,spara,0.9\nfile name,filnamn,0.8\n')
    assert store.import_terms(path) == 3
    assert store.counts()["terms"] == 3
    assert len(store.terminology("Save")) == 2
    assert store.terminology("Choose a file name")[0].target == "filnamn"
    path.write_text('source,canonical,confidence\nSave,Dåligt,9.0\n')
    with pytest.raises(ValueError):
        store.import_terms(path)
    assert store.counts()["terms"] == 3


def test_tbx_import(store, tmp_path):
    path = tmp_path / "terms.tbx"
    path.write_text('''<martif><text><body><termEntry id="1"><langSet xml:lang="en"><tig><term>Save</term></tig></langSet><langSet xml:lang="sv"><tig><term>Spara</term><note>Konfidensgrad: 80.0%</note></tig></langSet></termEntry></body></text></martif>''')
    assert store.import_terms(path) == 1
    assert store.terminology("Save")[0].score == 0.8


def test_quality_checks_placeholders_and_swedish_compounds(quality):
    unit = Unit("x", "Hello %s", ["Hej"])
    issues = quality.check(unit)
    assert any(i.severity == "error" and "placeholder" in i.rule for i in issues)
    unit = Unit("x", "Check the filename", ["Kontrollera fil namnet"])
    issues = quality.check(unit)
    assert any(i.tool == "svlang" and "filnamnet" in i.message for i in issues)


def test_unconfigured_spelling_reports_unavailability(store, monkeypatch):
    monkeypatch.setattr("ordverk.quality.shutil.which", lambda _: None)
    quality = Quality(Settings(), store)
    issues = quality.spelling("Spara", 0)
    assert {i.tool for i in issues} == {"Hunspell", "Aspell"}
    assert all(i.rule == "unavailable" for i in issues)


def test_private_dictionaries_take_precedence(store, monkeypatch):
    hunspell, aspell = store.dictionary_paths()
    hunspell.parent.mkdir(parents=True)
    hunspell.with_suffix(".dic").write_text("0\n")
    aspell.mkdir(parents=True)
    (aspell / "sv.rws").write_bytes(b"test")
    commands = []
    monkeypatch.setattr("ordverk.quality.shutil.which", lambda binary: "/usr/bin/" + binary)
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr("ordverk.quality.subprocess.run", run)
    Quality(Settings(), store).spelling("Spara", 0)
    assert str(hunspell) in commands[0]
    assert f"--dict-dir={aspell}" in commands[1]


def test_batch_fills_only_empty_unambiguous_valid_strings(store, quality):
    store.remember("Save", "Spara")
    store.remember("View", "Visa")
    store.remember("View", "Vy")
    store.remember("Hello %s", "Hej")
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""},{"source":"View","target":""},{"source":"Hello %s","target":""},{"source":"Save","target":"Behall"}]')
    changes, messages = batch_translate([catalog], store, quality)
    assert len(changes) == 1
    assert apply_changes(changes) == (1, 0)
    assert catalog.units[0].targets == ["Spara"]
    assert not catalog.units[0].reviewed
    assert catalog.units[3].targets == ["Behall"]
    assert messages


def test_batch_does_not_overwrite_concurrent_edits(store, quality):
    store.remember("Save", "Spara")
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""}]')
    changes, _ = batch_translate([catalog], store, quality)
    catalog.units[0].edit(0, "Spara nu")
    assert apply_changes(changes) == (0, 1)
    assert catalog.units[0].targets == ["Spara nu"]


def test_batch_limit_and_cancel(store, quality):
    catalog = Catalog("sv.json", b'[{"source":"Save","target":""},{"source":"Cancel","target":""}]')
    store.remember("Save", "Spara")
    store.remember("Cancel", "Avbryt")
    changes, messages = batch_translate([catalog], store, quality, limit=1)
    assert len(changes) == 1 and messages
    cancel = threading.Event()
    cancel.set()
    assert not batch_translate([catalog], store, quality, cancel=cancel)[0]


def test_recursive_import_dedup_skip_invalid_and_symlink(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "sub/sv.po").write_text('msgid "Save"\nmsgstr ""\n')
    (tmp_path / "bad.ts").write_text("not XML")
    (tmp_path / "node_modules/skip.po").write_text('msgid "Skip"\nmsgstr ""\n')
    (tmp_path / "link.po").symlink_to(tmp_path / "sub/sv.po")
    result = import_sources([tmp_path, tmp_path / "sub/sv.po"])
    assert len(result.catalogs) == 1
    assert len(result.errors) == 1


def test_url_import_and_github_snapshot(monkeypatch):
    responses = {
        "https://example.org/sv.po": b'msgid "Save"\nmsgstr ""\n',
        "https://api.github.com/repos/owner/repo": json.dumps({"default_branch":"main"}).encode(),
        "https://api.github.com/repos/owner/repo/git/trees/main?recursive=1": json.dumps({"sha":"snapshot", "tree":[{"type":"blob", "path":"po/sv.po"}]}).encode(),
        "https://raw.githubusercontent.com/owner/repo/snapshot/po/sv.po": b'msgid "Cancel"\nmsgstr ""\n',
    }
    monkeypatch.setattr("ordverk.importers.download", lambda url, **_: responses[url])
    direct = import_sources(["https://example.org/sv.po"])
    assert direct.catalogs[0].path is None
    repo = import_sources(["https://github.com/owner/repo"])
    assert repo.catalogs[0].units[0].source == "Cancel"
    assert "snapshot" in repo.catalogs[0].origin


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/file", "https://user:secret@example.org/f", "http://example.org/"])
def test_disallow_invalid_urls(url):
    with pytest.raises(ValueError):
        validate_url(url, local_http=True)


def test_llm_local_http_contract_context_and_error_handling(store, quality):
    state = {"mode": "good"}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["path"] = self.path
            state["payload"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if state["mode"] == "429":
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b"secret must not be exposed")
                return
            output = {"translation":"Spara", "explanation":"Verb i en meny."}
            if state["mode"] == "bad":
                output = {"wrong": "bad"}
            response = {"model":"local-test", "choices":[{"finish_reason":"stop", "message":{"content":json.dumps(output)}}], "usage":{"total_tokens":10}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test-model",
                        project_context="Bildredigerare")
    translator = Translator(settings, store, quality, session_key="test-session-key")
    unit = Unit("save", "Save", [""], context="menu", notes="Keep short")
    store.remember("Save", "Spara", "menu")
    try:
        proposal = translator.suggest(unit)
        assert proposal.translation == "Spara"
        assert unit.targets == [""]
        assert state["path"] == "/v1/chat/completions"
        prompt = json.loads(state["payload"]["messages"][1]["content"])
        assert prompt["project_context"] == "Bildredigerare"
        assert prompt["translation_memory"][0]["target"] == "Spara"
        state["mode"] = "bad"
        with pytest.raises(ValueError):
            translator.suggest(unit)
        state["mode"] = "429"
        with pytest.raises(ValueError, match="gräns") as error:
            translator.suggest(unit)
        assert "secret" not in str(error)
    finally:
        server.shutdown()
        server.server_close()


def test_key_is_never_serialized(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / ".ordverk"))
    monkeypatch.setenv("ORDVERK_API_KEY", "test-secret")
    settings = Settings(base_url="https://example.org/v1")
    settings.save()
    assert settings.key() == "test-secret"
    assert "test-secret" not in (tmp_path / ".ordverk/settings.json").read_text()
    assert Settings.load().base_url == settings.base_url


def test_cache_unchanged_blob_does_not_download_again(store, monkeypatch):
    calls = []
    def download(url, **_):
        calls.append(url)
        return b"test data"
    monkeypatch.setattr("ordverk.resources.download", download)
    store.versions["hunspell-sv"] = {"revision":"v1", "blobs":{"sv_SE.dic":"blob1"}}
    first = store.fetch("hunspell-sv", "sv_SE.dic")
    store.versions["hunspell-sv"]["revision"] = "v2"
    assert store.fetch("hunspell-sv", "sv_SE.dic") == first
    assert len(calls) == 1
    store.versions["hunspell-sv"]["blobs"]["sv_SE.dic"] = "blob2"
    store.fetch("hunspell-sv", "sv_SE.dic")
    assert len(calls) == 2


def test_cache_offline_retains_data(store, monkeypatch):
    import urllib.error
    store.remember("Save", "Spara")
    monkeypatch.setattr("ordverk.resources.urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    errors = store.update(["tm", "hunspell", "aspell"])
    assert len(errors) == 3
    assert store.memory("Save")[0].target == "Spara"
