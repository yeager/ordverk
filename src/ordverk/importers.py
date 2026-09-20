"""Local folders, direct URLs and GitHub repositories share one import result."""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, EXTENSIONS

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_FILES = 2000
EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", "vendor", "build", "dist"}


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled("Åtgärden avbröts.")


def validate_url(url, *, local_http=False):
    parsed = urllib.parse.urlsplit(url)
    allowed = parsed.scheme == "https" or (
        local_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    if not allowed or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Använd en HTTPS-adress utan inbäddade inloggningsuppgifter (HTTP tillåts lokalt).")
    return parsed


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, *, limit=MAX_FILE_BYTES, cancel=None):
    validate_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Ordverk/0.2", "Accept": "*/*"})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=25) as response:
            parts, size = [], 0
            while True:
                check_cancel(cancel)
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"Filen är större än gränsen {limit // 1024 // 1024} MiB.")
                parts.append(chunk)
            return b"".join(parts)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Hämtningen misslyckades: HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Kunde inte ansluta: {exc.reason}") from exc


@dataclass
class ImportResult:
    catalogs: list[Catalog] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def discover(path):
    path = Path(path).expanduser().absolute()
    if path.is_file():
        yield path
    elif path.is_dir():
        for root, directories, filenames in os.walk(path, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in EXCLUDED and not d.startswith("."))
            for filename in sorted(filenames):
                candidate = Path(root) / filename
                if candidate.suffix.lower() in EXTENSIONS and not candidate.is_symlink():
                    yield candidate
    else:
        raise ValueError(f"Sökvägen finns inte: {path}")


def github_files(url, cancel=None):
    parsed = validate_url(url)
    parts = [urllib.parse.unquote(p) for p in parsed.path.strip("/").split("/")]
    if len(parts) < 2:
        raise ValueError("Ange ett GitHub-förråd: https://github.com/ägare/förråd")
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    base = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
    info = json.loads(download(base, cancel=cancel))
    revision, prefix = info["default_branch"], ""
    if len(parts) > 2:
        if parts[2] not in {"tree", "blob"} or len(parts) < 4:
            raise ValueError("Ange förrådets startsida eller en tree/blob-adress.")
        # Resolve branches containing slashes against real repository refs, longest first.
        found = False
        for end in range(len(parts), 3, -1):
            candidate = "/".join(parts[3:end])
            try:
                commit = json.loads(download(base + "/commits/" + urllib.parse.quote(candidate, safe=""), cancel=cancel))
            except ValueError:
                continue
            revision, prefix, found = commit["sha"], "/".join(parts[end:]), True
            break
        if not found:
            raise ValueError("GitHub-revisionen kunde inte hittas.")
    tree = json.loads(download(base + "/git/trees/" + urllib.parse.quote(revision, safe="") + "?recursive=1",
                               limit=64 * 1024 * 1024, cancel=cancel))
    if tree.get("truncated"):
        raise ValueError("GitHub returnerade en ofullständig fillista. Klona förrådet och importera mappen.")
    # The tree SHA pins every file in this import to one snapshot.
    for entry in tree["tree"]:
        path = entry["path"]
        if entry["type"] != "blob" or entry.get("mode") == "120000":
            continue
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        if Path(path).suffix.lower() not in EXTENSIONS or any(part in EXCLUDED for part in Path(path).parts):
            continue
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{tree['sha']}/{urllib.parse.quote(path)}"
        yield path, raw


def import_sources(sources, *, progress=lambda message: None, cancel: threading.Event | None = None):
    result = ImportResult()
    seen = set()

    def load(name, data, path=None, origin=""):
        identity = str(path or origin)
        if identity in seen:
            return
        seen.add(identity)
        if len(seen) > MAX_FILES:
            raise ValueError(f"Högst {MAX_FILES} filer per import. Välj en mindre undermapp.")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name}: filen är för stor.")
        progress(f"Läser {name}")
        try:
            catalog = Catalog(name, data, path=path, origin=origin)
            from .state import restore_state
            restore_state(catalog)
            result.catalogs.append(catalog)
        except (ValueError, OSError, SyntaxError, UnicodeError) as exc:
            result.errors.append(f"{name}: {exc}")

    try:
        for source in sources:
            check_cancel(cancel)
            try:
                if str(source).startswith(("https://", "http://")):
                    parsed = validate_url(str(source))
                    if parsed.hostname == "github.com":
                        for name, url in github_files(str(source), cancel):
                            check_cancel(cancel)
                            try:
                                load(name, download(url, cancel=cancel), origin=url)
                            except (ValueError, OSError) as exc:
                                result.errors.append(f"{name}: {exc}")
                    else:
                        name = Path(urllib.parse.unquote(parsed.path)).name
                        load(name, download(str(source), cancel=cancel), origin=str(source))
                else:
                    for path in discover(source):
                        check_cancel(cancel)
                        try:
                            if path.stat().st_size > MAX_FILE_BYTES:
                                raise ValueError("Filen är större än 32 MiB.")
                            load(path.name, path.read_bytes(), path=path)
                        except (ValueError, OSError) as exc:
                            result.errors.append(f"{path}: {exc}")
            except (ValueError, OSError) as exc:
                result.errors.append(f"{source}: {exc}")
        if not result.catalogs and not result.errors:
            result.errors.append("Inga PO-, TS-, XLIFF- eller JSON-filer hittades.")
    except Cancelled:
        result.cancelled = True
    return result
