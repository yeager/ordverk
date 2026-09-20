"""File transfers through documented APIs. No credentials enter profile files or redirects."""
from __future__ import annotations

import base64
import difflib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field

from .catalog import Catalog, atomic_write, digest
from .importers import MAX_FILE_BYTES, check_cancel, download, validate_url
from .settings import data_dir

DEFAULT_URLS = {"github": "https://api.github.com", "weblate": "https://hosted.weblate.org/api",
                "transifex": "https://rest.api.transifex.com", "crowdin": "https://api.crowdin.com/api/v2"}
PROVIDERS = {"github": "GitHub", "weblate": "Weblate", "transifex": "Transifex", "crowdin": "Crowdin"}


def q(value):
    return urllib.parse.quote(str(value), safe="")


@dataclass
class Profile:
    provider: str = "github"
    name: str = ""
    base_url: str = "https://api.github.com"
    owner: str = ""
    project: str = ""
    resource: str = ""
    branch: str = "main"
    language: str = "sv"
    filename: str = "sv.po"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def credential_id(self):
        return "connection:" + self.id + ":" + self.base_url.rstrip("/")

    @property
    def description(self):
        path = "/".join(filter(None, [self.owner, self.project, self.resource, self.language]))
        return f"{PROVIDERS[self.provider]} · {self.base_url} · {path}" + (f" · gren {self.branch}" if self.provider == "github" else "")

    def validate(self):
        if self.provider not in PROVIDERS:
            raise ValueError("Välj en stödd tjänst.")
        url = validate_url(self.base_url, local_http=True)
        if url.query or url.fragment:
            raise ValueError("API-adressen ska inte ha frågeparametrar eller fragment.")
        if not self.project.strip() or not self.resource.strip():
            raise ValueError("Ange projekt och fil eller resurs.")
        if self.provider in {"github", "transifex"} and not self.owner.strip():
            raise ValueError("Ange ägare eller organisation.")
        if self.provider == "github" and (not self.branch.strip() or any(p in {"", ".", ".."} for p in self.resource.split("/"))):
            raise ValueError("Ange en gren och en relativ filsökväg i förrådet.")
        if self.provider == "crowdin" and (not self.project.isdecimal() or not self.resource.isdecimal()):
            raise ValueError("Crowdin behöver numeriska projekt- och fil-ID:n.")
        if self.language not in {"sv", "sv_SE", "sv-SE"}:
            raise ValueError("Ordverk arbetar med svenska. Använd sv, sv_SE eller sv-SE.")
        if not self.filename or "/" in self.filename or "\\" in self.filename:
            raise ValueError("Ange ett enkelt lokalt filnamn, exempelvis sv.po.")


def load_profiles():
    try:
        return [Profile(**item) for item in json.loads((data_dir() / "connections.json").read_text())]
    except (OSError, ValueError, TypeError):
        return []


def save_profile(profile):
    profile.validate()
    profiles = [p for p in load_profiles() if p.id != profile.id] + [profile]
    data_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(data_dir() / "connections.json", json.dumps([asdict(p) for p in profiles], ensure_ascii=False, indent=2).encode())


def multipart(fields, filename, data, file_field="file"):
    boundary = "ordverk-" + uuid.uuid4().hex
    safe_name = filename.replace('"', "_").replace("\r", "_").replace("\n", "_").replace("\\", "_")
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    parts += [data, f"\r\n--{boundary}--\r\n".encode()]
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


class RemoteError(ValueError):
    def __init__(self, status):
        self.status = status
        hint = {401: "Kontrollera API-nyckeln.", 403: "Kontrollera behörigheter och API-gränser.",
                404: "Kontrollera projekt, resurs, språk och behörighet.",
                409: "Fjärrfilen har ändrats. Importera den senaste versionen.",
                429: "Tjänstens anropsgräns nåddes. Försök senare."}.get(status, "Kontrollera anslutningen och projektets format.")
        super().__init__(f"Tjänsten svarade med HTTP {status}. {hint}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Client:
    def __init__(self, profile, token="", *, cancel=None, progress=lambda message, current=None, total=None: None):
        profile.validate()
        self.profile, self.token, self.cancel, self.progress = profile, token, cancel, progress

    def request(self, method, path, *, payload=None, data=None, content_type=None, headers=None, raw=False):
        check_cancel(self.cancel)
        url = self.profile.base_url.rstrip("/") + "/" + path.lstrip("/")
        outgoing = {"User-Agent": "Ordverk/0.2", "Accept": "application/json"}
        if self.token:
            outgoing["Authorization"] = ("Token " if self.profile.provider == "weblate" else "Bearer ") + self.token
        if payload is not None:
            data = json.dumps(payload).encode()
            content_type = "application/vnd.api+json" if self.profile.provider == "transifex" else "application/json"
        if content_type:
            outgoing["Content-Type"] = content_type
        outgoing.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=outgoing)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
                body = response.read(MAX_FILE_BYTES + 1)
                if len(body) > MAX_FILE_BYTES:
                    raise ValueError("Tjänstens svar är större än 32 MiB.")
                if raw:
                    return body
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            if exc.code in {302, 303} and method == "GET" and exc.headers.get("Location"):
                # Polling endpoints use a signed URL. Caller downloads it without any API credentials.
                return {"redirect": urllib.parse.urljoin(url, exc.headers["Location"])}
            raise RemoteError(exc.code) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError("Tjänsten kunde inte nås. Om detta var en export, kontrollera tjänsten innan du försöker igen.") from exc

    def wait(self):
        if self.cancel:
            self.cancel.wait(2)
        else:
            time.sleep(2)
        check_cancel(self.cancel)

    def poll_transifex(self, kind, response, *, importing):
        identity = response["data"]["id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self.progress("Väntar på Transifex…")
            if response.get("errors") or response.get("data", {}).get("attributes", {}).get("status") == "failed":
                raise ValueError("Transifex kunde inte bearbeta filen. Kontrollera resursens format i tjänsten.")
            if response.get("redirect"):
                return download(response["redirect"], cancel=self.cancel) if importing else {"status": "succeeded"}
            if not importing and response.get("data", {}).get("attributes", {}).get("status") == "succeeded":
                return response
            self.wait()
            response = self.request("GET", f"{kind}/{q(identity)}")
        raise ValueError("Transifex bearbetar fortfarande filen. Kontrollera resultatet i tjänsten innan du skickar igen.")

    def fetch(self):
        p = self.profile
        self.progress(f"Hämtar från {PROVIDERS[p.provider]}…")
        if p.provider == "github":
            path = f"repos/{q(p.owner)}/{q(p.project)}/contents/{q(p.resource)}?ref={q(p.branch)}"
            info = self.request("GET", path)
            if not isinstance(info, dict) or info.get("type") != "file":
                raise ValueError("Välj en fil i GitHub-förrådet.")
            if info.get("encoding") == "base64":
                data = base64.b64decode(info["content"])
            else:
                data = self.request("GET", path, headers={"Accept": "application/vnd.github.raw+json"}, raw=True)
            return data, info["sha"]
        if p.provider == "weblate":
            path = f"translations/{q(q(p.project))}/{q(q(p.resource))}/{q(p.language)}/file/"
            return self.request("GET", path, raw=True), None
        if p.provider == "transifex":
            kind = "resource_translations_async_downloads"
            response = self.request("POST", kind, payload={"data": {"type": kind,
                "attributes": {"content_encoding": "text", "mode": "translator"}, "relationships": {
                    "resource": {"data": {"type": "resources", "id": self.transifex_resource()}},
                    "language": {"data": {"type": "languages", "id": "l:" + p.language}}}}})
            return self.poll_transifex(kind, response, importing=True), None
        response = self.request("POST", f"projects/{p.project}/translations/builds/files/{p.resource}",
                                payload={"targetLanguageId": p.language, "skipUntranslatedStrings": False, "exportApprovedOnly": False})
        return download(response["data"]["url"], cancel=self.cancel), None

    def transifex_resource(self):
        p = self.profile
        return f"o:{p.owner}:p:{p.project}:r:{p.resource}"

    def import_catalog(self):
        data, _ = self.fetch()
        return Catalog(self.profile.filename, data, origin=self.profile.description + " · " + digest(data)[:12])

    def upload(self, data, *, expected_hash, revision=None, message="Uppdatera svensk översättning", method="translate"):
        p = self.profile
        if not self.token:
            raise ValueError("Ange en API-nyckel med skrivrättigheter för export.")
        # GitHub enforces the SHA atomically. The other APIs do not expose an equivalent file CAS.
        if p.provider != "github":
            current, _ = self.fetch()
            if digest(current) != expected_hash:
                raise ValueError("Fjärrfilen har ändrats sedan förhandsgranskningen. Importera och granska den nya versionen.")
        check_cancel(self.cancel)
        self.progress(f"Skickar till {PROVIDERS[p.provider]}…")
        if p.provider == "github":
            payload = {"message": message, "content": base64.b64encode(data).decode(), "branch": p.branch}
            if revision:
                payload["sha"] = revision
            response = self.request("PUT", f"repos/{q(p.owner)}/{q(p.project)}/contents/{q(p.resource)}", payload=payload)
            return "Exporten är klar. Commit: " + response["commit"]["sha"]
        if p.provider == "weblate":
            body, content_type = multipart({"method": method, "conflicts": "replace-translated" if method == "translate" else "ignore",
                                            "fuzzy": "process"}, p.filename, data)
            self.request("POST", f"translations/{q(q(p.project))}/{q(q(p.resource))}/{q(p.language)}/file/",
                         data=body, content_type=content_type)
        elif p.provider == "transifex":
            kind = "resource_translations_async_uploads"
            body, content_type = multipart({"resource": self.transifex_resource(), "language": "l:" + p.language,
                                            "file_type": "default"}, p.filename, data, "content")
            response = self.request("POST", kind, data=body, content_type=content_type)
            self.poll_transifex(kind, response, importing=False)
        else:
            storage = self.request("POST", "storages", data=data, content_type="application/octet-stream",
                                   headers={"Crowdin-API-FileName": p.filename})
            # Once accepted, a cancellation cannot undo the server's import.
            self.request("POST", f"projects/{p.project}/translations/{q(p.language)}", payload={
                "storageId": storage["data"]["id"], "fileId": int(p.resource), "autoApproveImported": False,
                "importEqSuggestions": False, "translateHidden": False, "addToTm": False})
        return "Tjänsten har tagit emot översättningen. Granska resultatet i projektet."


@dataclass(frozen=True)
class Transfer:
    data: bytes
    expected_hash: str
    revision: str | None
    preview: str
    issues: list


def prepare_export(client, catalog, quality):
    data = catalog.render()
    Catalog(catalog.name, data)
    issues = quality.catalog(catalog, cancel=client.cancel, progress=client.progress)
    if any(i.severity == "error" for i in issues):
        raise ValueError("Åtgärda kvalitetsfelen före export:\n" + "\n".join(i.message for i in issues if i.severity == "error")[:4000])
    try:
        before, revision = client.fetch()
    except RemoteError as exc:
        if exc.status != 404 or client.profile.provider != "github":
            raise
        before, revision = b"", None
    encoding = catalog.po.encoding if catalog.ext in {".po", ".pot"} else "utf-8"
    diff = "".join(difflib.unified_diff(before.decode(encoding, errors="replace").splitlines(True),
                                       data.decode(encoding, errors="replace").splitlines(True),
                                       fromfile="Fjärrfil", tofile="Din översättning"))
    summary = f"{client.profile.description}\nFil: {client.profile.filename}\n{len(data)} byte · {len(issues)} granskningsråd\n\n"
    return Transfer(data, digest(before), revision, summary + (diff or "Filerna har samma innehåll."), issues)
