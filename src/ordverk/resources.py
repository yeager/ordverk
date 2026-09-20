"""Indexed, attributed terminology and translation memories; private dictionary builds."""
from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import polib

from .catalog import XML_LANG, atomic_write, digest, localname, parse_xml, text_content
from .importers import check_cancel, download
from .settings import data_dir

REVISIONS = {
    "swedish-tm": "591ede0b3ad51735c2a7decff77efe55dd76f3e4",
    "swedish-foss-terminology": "063c2d9bcb8581ca26ace192e72730950218d143",
    "hunspell-sv": "e59c1cd177c870e4bfa830cd24efdbd5f88ee87d",
    "aspell-sv": "2323276d793061f77ad36efd5ecedc48be3cbfb9",
}
ECOSYSTEMS = ("gnome", "kde", "mozilla", "ubuntu", "fedora", "libreoffice", "xfce", "tp",
              "transifex", "weblate", "blender", "inkscape", "stellarium", "qgis", "scummvm")


@dataclass(frozen=True)
class Suggestion:
    source: str
    target: str
    origin: str
    score: float | None
    context: str = ""
    kind: str = "tm"


class ResourceStore:
    def __init__(self, directory=None):
        self.directory = (Path(directory) if directory else data_dir()).expanduser().absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "resources.sqlite3"
        self.versions_path = self.directory / "versions.json"
        self.versions = json.loads(self.versions_path.read_text()) if self.versions_path.exists() else {}
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY, source TEXT, target TEXT, origin TEXT, context TEXT,
                    UNIQUE(source, target, origin, context));
                CREATE INDEX IF NOT EXISTS memory_source ON memory(source);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(source, content=memory, content_rowid=id);
                CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memory BEGIN
                    INSERT INTO memory_fts(rowid, source) VALUES(new.id, new.source);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_delete AFTER DELETE ON memory BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, source) VALUES('delete', old.id, old.source);
                END;
                CREATE TABLE IF NOT EXISTS terms (
                    normalized TEXT, source TEXT, target TEXT, confidence REAL, origin TEXT,
                    PRIMARY KEY(normalized, source, target, origin));
                CREATE TABLE IF NOT EXISTS imports (identity TEXT PRIMARY KEY, digest TEXT, count INTEGER);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def counts(self):
        with self.connect() as db:
            return {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in ("memory", "terms")}

    def import_po(self, path, origin=None, cancel=None):
        path = Path(path)
        identity = origin or str(path.absolute())
        sha = digest(path.read_bytes())
        with self.connect() as db:
            old = db.execute("SELECT digest, count FROM imports WHERE identity=?", (identity,)).fetchone()
            if old and old["digest"] == sha:
                return old["count"]
        po = polib.pofile(str(path))
        language = po.metadata.get("Language", "").lower()
        if language and not language.startswith("sv"):
            raise ValueError("Översättningsminnet måste ha svenska som målspråk.")
        count = 0
        with self.connect() as db:
            db.execute("DELETE FROM memory WHERE origin=?", (identity,))
            for entry in po:
                check_cancel(cancel)
                if entry.obsolete or "fuzzy" in entry.flags or not entry.msgid or not entry.msgstr:
                    continue
                context = entry.msgctxt or entry.comment or ", ".join(p for p, _ in entry.occurrences)
                db.execute("INSERT OR IGNORE INTO memory(source,target,origin,context) VALUES(?,?,?,?)",
                           (entry.msgid, entry.msgstr, identity, context))
                count += 1
            db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?)", (identity, sha, count))
        return count

    def import_terms(self, path, origin=None, cancel=None):
        path = Path(path)
        identity = origin or str(path.absolute())
        sha = digest(path.read_bytes())
        with self.connect() as db:
            old = db.execute("SELECT digest, count FROM imports WHERE identity=?", (identity,)).fetchone()
            if old and old["digest"] == sha:
                return old["count"]
            count = 0
            db.execute("DELETE FROM terms WHERE origin=?", (identity,))
            for row in term_rows(path):
                check_cancel(cancel)
                confidence = float(row["confidence"]) if row["confidence"] is not None else None
                if confidence is not None and not 0 <= confidence <= 1:
                    raise ValueError("Termens konsensus ska vara mellan 0 och 1.")
                db.execute("INSERT OR REPLACE INTO terms VALUES(?,?,?,?,?)",
                           (row["source"].casefold(), row["source"], row["canonical"], confidence, identity))
                count += 1
            db.execute("INSERT OR REPLACE INTO imports VALUES(?,?,?)", (identity, sha, count))
        return count

    def remember(self, source, target, context="", origin="Egen granskning"):
        if not source or not target:
            return
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO memory(source,target,origin,context) VALUES(?,?,?,?)",
                       (source, target, origin, context))

    def memory(self, source, context="", limit=8):
        with self.connect() as db:
            rows = list(db.execute("SELECT * FROM memory WHERE source=? LIMIT 60", (source,)))
            tokens = list(dict.fromkeys(re.findall(r"\w+", source.casefold())))[:12]
            if tokens and len(rows) < limit:
                query = " OR ".join('"' + token + '"' for token in tokens)
                rows += list(db.execute("""SELECT memory.* FROM memory_fts JOIN memory ON memory.id=memory_fts.rowid
                    WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts) LIMIT 80""", (query,)))
        results, seen = [], set()
        for row in rows:
            identity = (row["source"], row["target"], row["origin"])
            if identity in seen:
                continue
            seen.add(identity)
            score = SequenceMatcher(None, source, row["source"], autojunk=False).ratio()
            if score >= 0.55:
                results.append(Suggestion(row["source"], row["target"], row["origin"], score, row["context"]))
        results.sort(key=lambda r: (r.score, bool(context and r.context == context),
                                    r.origin == "Egen granskning"), reverse=True)
        return results[:limit]

    def exact_translation(self, source, context=""):
        """Resolve the complete candidate set, never a truncated display list."""
        with self.connect() as db:
            rows = list(db.execute("SELECT target, origin, context FROM memory WHERE source=?", (source,)))
            contextual = [r for r in rows if context and r["context"] == context]
            rows = contextual or rows
            targets = {r["target"] for r in rows}
            if len(targets) == 1:
                return next(iter(targets)), rows[0]["origin"]
            if targets:
                return None  # Ambiguous memories need human judgment or AI context.
            rows = list(db.execute("SELECT target, origin FROM terms WHERE source=?", (source,)))
            targets = {r["target"] for r in rows}
            if len(targets) == 1:
                return next(iter(targets)), "Terminologi · " + rows[0]["origin"]
        return None

    def terminology(self, source, limit=12):
        words = list(re.finditer(r"[\w-]+", source))
        keys = {source.casefold()}
        # Extract source substrings so hyphens, punctuation and case retain their meaning.
        for start in range(len(words)):
            for end in range(start, min(start + 6, len(words))):
                keys.add(source[words[start].start():words[end].end()].casefold())
        keys = sorted(keys, key=len, reverse=True)[:300]
        if not keys:
            return []
        with self.connect() as db:
            rows = db.execute("SELECT * FROM terms WHERE normalized IN (" + ",".join("?" for _ in keys)
                              + ") ORDER BY length(source) DESC, confidence DESC LIMIT ?", (*keys, limit))
            return [Suggestion(r["source"], r["target"], r["origin"], r["confidence"], kind="term") for r in rows]

    def fetch(self, repo, filename, cancel=None):
        destination = self.directory / "upstream" / repo / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        version = self.versions.get(repo, {})
        revision = version.get("revision", REVISIONS[repo])
        blob = version.get("blobs", {}).get(filename)
        url = f"https://raw.githubusercontent.com/yeager/{repo}/{revision}/{filename}"
        manifest_path = self.directory / "provenance.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        saved = manifest.get(f"{repo}/{filename}", {})
        same = saved.get("url") == url or bool(blob and saved.get("blob") == blob)
        if destination.exists() and same and saved.get("sha256") == digest(destination.read_bytes()):
            return destination
        data = download(url, limit=96 * 1024 * 1024, cancel=cancel)
        atomic_write(destination, data)
        manifest[f"{repo}/{filename}"] = {"url": url, "sha256": digest(data), "revision": revision, "blob": blob}
        atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
        return destination

    def update(self, components, *, progress=lambda message, current=None, total=None: None, cancel=None):
        """Check mutable refs once per launch; pin downloads to the observed tree, retain offline cache."""
        errors = []
        repos = {"tm": "swedish-tm", "tm-small": "swedish-tm", "terms": "swedish-foss-terminology",
                 "hunspell": "hunspell-sv", "aspell": "aspell-sv"}
        checked = set()
        for index, component in enumerate(components):
            check_cancel(cancel)
            repo = repos[component]
            if repo not in checked:
                checked.add(repo)
                progress(f"Söker uppdateringar: {repo}", index, len(components))
                branch = "master" if repo == "aspell-sv" else "main"
                url = f"https://api.github.com/repos/yeager/{repo}/git/trees/{branch}?recursive=1"
                headers = {"User-Agent": "Ordverk/0.2", "Accept": "application/vnd.github+json"}
                old = self.versions.get(repo, {})
                if old.get("etag"):
                    headers["If-None-Match"] = old["etag"]
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
                        data = json.loads(response.read(8 * 1024 * 1024))
                        if data.get("truncated"):
                            raise ValueError("Ofullständig resurslista från GitHub.")
                        self.versions[repo] = {"revision": data["sha"], "etag": response.headers.get("ETag", ""),
                                               "blobs": {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "blob"}}
                    atomic_write(self.versions_path, json.dumps(self.versions, indent=2).encode())
                except urllib.error.HTTPError as exc:
                    if exc.code != 304:
                        errors.append(f"{repo}: HTTP {exc.code}; använder tillgänglig cache.")
                        continue
                except (OSError, ValueError) as exc:
                    errors.append(f"{repo}: {exc}; använder tillgänglig cache.")
                    continue
            try:
                self.install(component, progress=lambda text: progress(text, index, len(components)), cancel=cancel)
                progress(f"Resurs klar: {repo}", index + 1, len(components))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{component}: {exc}")
        return errors

    def install(self, component, *, progress=lambda message: None, cancel=None):
        if component in {"tm", "tm-small"}:
            for ecosystem in (ECOSYSTEMS if component == "tm" else ("xfce",)):
                progress(f"Hämtar och indexerar swedish-tm: {ecosystem}")
                path = self.fetch("swedish-tm", f"sv-{ecosystem}.po", cancel)
                self.import_po(path, f"swedish-tm/{ecosystem} · CC BY 4.0", cancel)
        elif component == "terms":
            progress("Hämtar och indexerar Swedish FOSS Terminology")
            path = self.fetch("swedish-foss-terminology", "termbank-flat.csv", cancel)
            self.import_terms(path, "Swedish FOSS Terminology · CC BY 4.0", cancel)
        elif component == "hunspell":
            self.preserve_dictionary("hunspell")
            for name in ("sv_SE.aff", "sv_SE.dic", "LICENSE"):
                progress(f"Hämtar hunspell-sv: {name}")
                self.fetch("hunspell-sv", name, cancel)
            folder = self.directory / "upstream/hunspell-sv"
            lines = (folder / "sv_SE.dic").read_bytes().splitlines()
            if not lines or not lines[0].strip().isdigit() or b"SET " not in (folder / "sv_SE.aff").read_bytes():
                raise ValueError("Hunspell-filerna har inte ett giltigt ordlisteformat. Föregående version används.")
            if shutil.which("hunspell"):
                result = subprocess.run(["hunspell", "-l", "-d", str(folder / "sv_SE")], input="ord\n",
                                        encoding="utf-8", capture_output=True, timeout=20)
                if result.returncode:
                    raise ValueError("Hunspell kunde inte läsa den nya ordlistan. Föregående version används.")
            check_cancel(cancel)
            self.activate_dictionary("hunspell", folder)
        elif component == "aspell":
            self.preserve_dictionary("aspell")
            if not shutil.which("aspell"):
                raise ValueError("Installera Aspell från din Linux-distribution först.")
            for name in ("sv.wl", "sv.dat", "sv.multi", "sv_phonet.dat", "COPYING", "COPYING.LESSER",
                         "COPYING.GPL2", "COPYING.aspell", "Copyright.aspell", "LICENSE.hunspell"):
                progress(f"Hämtar aspell-sv: {name}")
                self.fetch("aspell-sv", f"swedish/{name}", cancel)
            folder = self.directory / "upstream/aspell-sv/swedish"
            build_hash = digest(b"".join((folder / name).read_bytes() for name in ("sv.wl", "sv.dat", "sv_phonet.dat")))
            stamp = folder / "build.sha256"
            if (folder / "sv.rws").exists() and stamp.exists() and stamp.read_text() == build_hash:
                self.activate_dictionary("aspell", folder)
                return self.counts()
            progress("Bygger Aspells svenska ordlista för den här datorn")
            temporary = folder / "sv.build.rws"
            try:
                with (folder / "sv.wl").open("rb") as stream:
                    result = subprocess.run(["aspell", "--lang=sv", "--encoding=utf-8",
                                             f"--local-data-dir={folder}", f"--dict-dir={folder}",
                                             "create", "master", str(temporary)], stdin=stream,
                                            capture_output=True, timeout=120)
                if result.returncode:
                    raise ValueError("Aspell kunde inte bygga ordlistan: " + result.stderr.decode(errors="replace")[:500])
                check_cancel(cancel)
                temporary.replace(folder / "sv.rws")
                atomic_write(stamp, build_hash.encode())
                self.activate_dictionary("aspell", folder)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            raise ValueError(f"Okänd resurs: {component}")
        return self.counts()

    def export_memory(self, path):
        po = polib.POFile()
        po.metadata = {"Language": "sv", "Content-Type": "text/plain; charset=UTF-8",
                       "Plural-Forms": "nplurals=2; plural=(n != 1);"}
        seen = set()
        with self.connect() as db:
            for row in db.execute("SELECT * FROM memory WHERE origin='Egen granskning' ORDER BY id DESC"):
                identity = (row["source"], row["context"])
                if identity in seen:
                    continue
                seen.add(identity)
                po.append(polib.POEntry(msgid=row["source"], msgstr=row["target"], msgctxt=row["context"] or None))
        atomic_write(Path(path), str(po).encode(), 0o644)

    def dictionary_paths(self):
        active = self.active_dictionaries()
        return (self.directory / active.get("hunspell", "upstream/hunspell-sv") / "sv_SE",
                self.directory / active.get("aspell", "upstream/aspell-sv/swedish"))

    def active_dictionaries(self):
        try:
            return json.loads((self.directory / "dictionaries.json").read_text())
        except (OSError, ValueError):
            return {}

    def preserve_dictionary(self, engine):
        if engine in self.active_dictionaries():
            return
        hunspell, aspell = self.dictionary_paths()
        folder = hunspell.parent if engine == "hunspell" else aspell
        required = ("sv_SE.aff", "sv_SE.dic") if engine == "hunspell" else ("sv.rws", "sv.dat")
        if all((folder / name).is_file() for name in required):
            self.activate_dictionary(engine, folder)

    def activate_dictionary(self, engine, folder):
        """Switch a whole immutable dictionary generation in one atomic pointer update."""
        names = ("sv_SE.aff", "sv_SE.dic", "LICENSE") if engine == "hunspell" else (
            "sv.rws", "sv.dat", "sv.multi", "sv_phonet.dat", "COPYING", "COPYING.LESSER",
            "COPYING.GPL2", "COPYING.aspell", "Copyright.aspell", "LICENSE.hunspell")
        files = [(name, (folder / name).read_bytes()) for name in names if (folder / name).exists()]
        identity = digest(b"".join(name.encode() + b"\0" + data for name, data in files))
        generation = self.directory / "dictionaries" / engine / identity
        generation.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            if not (generation / name).exists():
                atomic_write(generation / name, data)
        active = self.active_dictionaries()
        active[engine] = str(generation.relative_to(self.directory))
        atomic_write(self.directory / "dictionaries.json", json.dumps(active).encode())


def term_rows(path):
    if Path(path).suffix.lower() == ".tbx":
        tree = parse_xml(Path(path).read_bytes())
        for entry in tree.getroot().iter():
            if localname(entry) not in {"termEntry", "conceptEntry"}:
                continue
            terms, confidence = {}, None
            for language in entry:
                code = language.get(XML_LANG, "").split("-")[0]
                values = [text_content(e) for e in language.iter() if localname(e) == "term"]
                if values:
                    terms[code] = values[0]
                for note in language.iter():
                    match = re.search(r"Konfidensgrad:\s*([\d.]+)%", text_content(note)) if localname(note) == "note" else None
                    if match:
                        confidence = float(match.group(1)) / 100
            if "en" in terms and "sv" in terms:
                yield {"source": terms["en"], "canonical": terms["sv"], "confidence": confidence}
    else:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"source", "canonical", "confidence"}.issubset(reader.fieldnames or []):
                raise ValueError("Termfilen ska ha kolumnerna source,canonical,confidence.")
            yield from reader
