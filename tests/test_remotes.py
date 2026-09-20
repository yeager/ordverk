import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ordverk.catalog import digest
from ordverk.remotes import Client, Profile, RemoteError, load_profiles, save_profile


@pytest.fixture
def api():
    state = {"requests": [], "responses": []}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def do_PUT(self):
            self.respond()
        def respond(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path, dict(self.headers), data))
            status, body, headers = state["responses"].pop(0)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


def profile(api, provider):
    return Profile(provider=provider, base_url=api["base"], owner="owner", project="123" if provider == "crowdin" else "project",
                   resource="42" if provider == "crowdin" else "file" if provider != "github" else "po/sv.po")


def test_github_import_export_sha_contract(api):
    data = b'msgid "Save"\nmsgstr "Spara"\n'
    api["responses"] = [(200, {"type":"file", "encoding":"base64", "content":base64.b64encode(data).decode(), "sha":"v1"}, {}),
                        (200, {"commit":{"sha":"v2"}}, {})]
    client = Client(profile(api, "github"), "fake-test-token")
    catalog = client.import_catalog()
    assert catalog.units[0].targets == ["Spara"]
    assert "v2" in client.upload(data, expected_hash=digest(data), revision="v1")
    method, path, headers, body = api["requests"][1]
    assert method == "PUT" and path.endswith("po%2Fsv.po")
    payload = json.loads(body)
    assert payload["sha"] == "v1" and payload["branch"] == "main"
    assert base64.b64decode(payload["content"]) == data
    assert headers["Authorization"] == "Bearer fake-test-token"


def test_weblate_multipart_and_remote_change_protection(api):
    data = b'msgid "Save"\nmsgstr "Spara"\n'
    api["responses"] = [(200, data, {}), (200, {"result": True}, {}), (200, b"changed", {})]
    client = Client(profile(api, "weblate"), "fake-test-token")
    client.upload(data, expected_hash=digest(data))
    method, path, headers, body = api["requests"][1]
    assert method == "POST" and path == "/translations/project/file/sv/file/"
    assert headers["Authorization"].startswith("Token ")
    assert b'replace-translated' in body and data in body
    with pytest.raises(ValueError, match="ändrats"):
        client.upload(data, expected_hash=digest(data))
    assert len(api["requests"]) == 3


def test_transifex_polling_and_upload_contract(api, monkeypatch):
    data = b'msgid "Save"\nmsgstr "Spara"\n'
    api["responses"] = [
        (202, {"data":{"id":"download-id"}}, {}),
        (303, b"", {"Location":"https://files.example.org/signed-po"}),
        (202, {"data":{"id":"download-id2"}}, {}),
        (303, b"", {"Location":"https://files.example.org/signed-po"}),
        (202, {"data":{"id":"upload-id", "attributes":{"status":"pending"}}}, {}),
        (200, {"data":{"id":"upload-id", "attributes":{"status":"succeeded"}}}, {})]
    downloads = []
    def signed_download(url, **kwargs):
        downloads.append((url, kwargs))
        return data
    monkeypatch.setattr("ordverk.remotes.download", signed_download)
    client = Client(profile(api, "transifex"), "fake-test-token")
    monkeypatch.setattr(client, "wait", lambda: None)
    assert client.fetch()[0] == data
    client.upload(data, expected_hash=digest(data))
    payload = json.loads(api["requests"][0][3])["data"]
    assert payload["relationships"]["resource"]["data"]["id"] == "o:owner:p:project:r:file"
    assert payload["relationships"]["language"]["data"]["id"] == "l:sv"
    assert b'name="content"' in api["requests"][4][3]
    assert b'o:owner:p:project:r:file' in api["requests"][4][3]
    assert len(downloads) == 2 and all("headers" not in kw for _, kw in downloads)


def test_crowdin_download_storage_and_translation_upload(api, monkeypatch):
    data = b'msgid "Save"\nmsgstr "Spara"\n'
    api["responses"] = [(200, {"data":{"url":"https://example.org/translation"}}, {}),
                        (200, {"data":{"url":"https://example.org/translation"}}, {}),
                        (201, {"data":{"id":77}}, {}), (200, {"data":{"fileId":42}}, {})]
    monkeypatch.setattr("ordverk.remotes.download", lambda *a, **k: data)
    client = Client(profile(api, "crowdin"), "fake-test-token")
    assert client.fetch()[0] == data
    client.upload(data, expected_hash=digest(data))
    assert api["requests"][0][1] == "/projects/123/translations/builds/files/42"
    assert json.loads(api["requests"][0][3])["targetLanguageId"] == "sv"
    assert api["requests"][2][1] == "/storages" and api["requests"][2][3] == data
    payload = json.loads(api["requests"][3][3])
    assert payload["storageId"] == 77 and payload["fileId"] == 42
    assert payload["autoApproveImported"] is False


def test_redirect_never_forwards_credentials_and_write_redirect_rejected(api):
    api["responses"] = [(302, b"", {"Location":"https://different.example/"}),
                        (307, b"", {"Location":"https://different.example/"})]
    client = Client(profile(api, "github"), "fake-test-token")
    assert client.request("GET", "poll")["redirect"] == "https://different.example/"
    with pytest.raises(RemoteError):
        client.request("POST", "send", payload={})
    assert len(api["requests"]) == 2


def test_profiles_have_no_secret_field(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path))
    p = Profile(owner="owner", project="project", resource="sv.po")
    save_profile(p)
    assert load_profiles()[0] == p
    assert "token" not in (tmp_path / "connections.json").read_text()
    original = p.credential_id
    p.base_url = "https://elsewhere.example"
    assert p.credential_id != original
