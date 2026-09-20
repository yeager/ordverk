"""Local export copies and unified diffs against the imported file."""
from dataclasses import dataclass
from collections import Counter
import copy
import difflib
import json
import os
from pathlib import Path
import re
import tempfile

from lxml import etree as ET
import polib

from .catalog import SV_PLURALS
from .ai_context import catalog_context
from .consistency import check_roundtrip, parse_checked, verify_written
from .po_header import stamp_generator
from . import __version__
from .importers import check_cancel

FORMATS = (
    ("original", "Behåll originalformatet", ""),
    ("po", "Gettext PO", ".po"),
    ("ts", "Qt TS", ".ts"),
    ("xliff12", "XLIFF 1.2", ".xlf"),
    ("xliff2", "XLIFF 2.0", ".xliff"),
    ("json", "JSON", ".json"),
    ("diff", "Diff mot originalfilen vid import", ".diff"),
)


@dataclass(frozen=True)
class Export:
    name: str
    data: bytes
    source_path: Path | None


def unified_diff(catalog):
    encoding = catalog.po.encoding if hasattr(catalog, "po") else (
        catalog.tree.docinfo.encoding or "utf-8") if hasattr(catalog, "tree") else "utf-8-sig"
    before, after = catalog.import_raw.decode(catalog.import_encoding), catalog.render().decode(encoding)
    name, original_name = Path(catalog.name).name, Path(catalog.import_name).name
    if any(character in name + original_name for character in "\n\r\t"):
        raise ValueError("Filnamnet innehåller tecken som inte kan användas i en diff. Byt filnamn först.")
    lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile="a/" + original_name, tofile="b/" + name)
    # Unified diff needs a marker after content lines lacking a final newline.
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in lines).encode("utf-8")


def _text(unit, text, variant):
    if not unit.codecs:
        return text
    codec = unit.codecs[min(variant, len(unit.codecs) - 1)]
    return re.sub(r"⟦/?\d+⟧", lambda match: codec.tokens.get(match.group(), match.group()), text)


def _records(catalog, cancel):
    for number, unit in enumerate(catalog.units, 1):
        check_cancel(cancel)
        if unit.source_is_key:
            raise ValueError("Koppla en käll-JSON innan du exporterar till ett annat filformat.")
        for variant, target in enumerate(unit.targets):
            context = unit.context
            if len(unit.targets) > 1:
                context = " · ".join(filter(None, [context, unit.variants[variant]]))
            notes = "\n".join(filter(None, [unit.notes, "Källkod: " + unit.references if unit.references else ""]))
            yield {"id": f"u{number}-v{variant + 1}", "source": _text(unit, unit.source_for(variant), variant),
                   "target": _text(unit, target, variant), "context": context, "notes": notes,
                   "reviewed": bool(unit.reviewed and all(unit.targets))}


def _convert(catalog, kind, cancel):
    if catalog.language and not catalog.language.lower().startswith("sv"):
        raise ValueError("Skapa en svensk arbetskopia innan du byter exportformat.")
    source_language = catalog_context(catalog, catalog.units[0], 0, index=0)["source_language"] or "und"
    records = _records(catalog, cancel)
    if kind == "po":
        po = polib.POFile()
        po.metadata = {"Project-Id-Version": Path(catalog.name).stem, "Language": "sv",
                       "MIME-Version": "1.0", "Content-Type": "text/plain; charset=UTF-8",
                       "Content-Transfer-Encoding": "8bit", "Plural-Forms": SV_PLURALS}
        po.metadata["X-Generator"] = f"Ordverk {__version__}"
        used = set()
        for record in records:
            context = record["context"]
            if (context, record["source"]) in used:
                context = f"{context} · {record['id']}"
            while (context, record["source"]) in used:
                context += " · " + record["id"]
            used.add((context, record["source"]))
            po.append(polib.POEntry(msgid=record["source"], msgstr=record["target"],
                                   msgctxt=context or None, tcomment=record["notes"],
                                   flags=[] if record["reviewed"] else ["fuzzy"]))
        return str(po).encode("utf-8")
    if kind == "json":
        return (json.dumps({"@locale": "sv", "strings": list(records)}, ensure_ascii=False, indent=2) + "\n").encode()
    if kind == "ts":
        root = ET.Element("TS", version="2.1", language="sv_SE", sourcelanguage=source_language)
        contexts = {}
        for record in records:
            name = record["context"] or "Ordverk"
            if name not in contexts:
                contexts[name] = ET.SubElement(root, "context")
                ET.SubElement(contexts[name], "name").text = name
            message = ET.SubElement(contexts[name], "message", id=record["id"])
            ET.SubElement(message, "source").text = record["source"]
            if record["notes"]:
                ET.SubElement(message, "extracomment").text = record["notes"]
            target = ET.SubElement(message, "translation")
            target.text = record["target"]
            if not record["reviewed"]:
                target.set("type", "unfinished")
    else:
        version = "1.2" if kind == "xliff12" else "2.0"
        namespace = "urn:oasis:names:tc:xliff:document:" + version
        root = ET.Element("xliff", nsmap={None: namespace}, version=version)
        if kind == "xliff12":
            file = ET.SubElement(root, "file", {"original": catalog.name, "source-language": source_language,
                                               "target-language": "sv", "datatype": "plaintext"})
            body = ET.SubElement(file, "body")
        else:
            root.set("srcLang", source_language)
            root.set("trgLang", "sv")
            body = ET.SubElement(root, "file", id="f1", original=catalog.name)
        for record in records:
            if kind == "xliff12":
                unit = ET.SubElement(body, "trans-unit", id=record["id"], resname=record["context"] or record["id"])
                segment = unit
            else:
                unit = ET.SubElement(body, "unit", id=record["id"], name=record["context"] or record["id"])
                if record["notes"] or record["context"]:
                    notes = ET.SubElement(unit, "notes")
                    ET.SubElement(notes, "note").text = "\n".join(filter(None, [record["context"], record["notes"]]))
                segment = ET.SubElement(unit, "segment", state="final" if record["reviewed"] else
                                        "translated" if record["target"] else "initial")
            ET.SubElement(segment, "source").text = record["source"]
            target = ET.SubElement(segment, "target")
            target.text = record["target"]
            if kind == "xliff12":
                target.set("state", "final" if record["reviewed"] else "translated" if record["target"] else "new")
                if record["notes"]:
                    ET.SubElement(unit, "note").text = record["notes"]
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)


def prepare(catalog, kind="original", cancel=None):
    check_cancel(cancel)
    option = next((option for option in FORMATS if option[0] == kind), None)
    if option is None:
        raise ValueError("Välj ett exportformat.")
    if kind == "diff":
        from .diffs import parse_diff, patch_text
        data = unified_diff(catalog)
        if data:
            patch = parse_diff(data.decode("utf-8"))[0]
            patched = patch_text(catalog.import_raw.decode(catalog.import_encoding), patch)
            encoding = catalog.po.encoding if hasattr(catalog, "po") else (
                catalog.tree.docinfo.encoding or "utf-8") if hasattr(catalog, "tree") else "utf-8-sig"
            if patched != catalog.render().decode(encoding):
                raise ValueError("Diffens konsistenskontroll misslyckades.")
        return Export(Path(catalog.name).name + ".diff", data, catalog.path)
    same = kind == "original" or kind == catalog.ext.lstrip(".") or (kind == "po" and catalog.ext == ".pot") or (
        catalog.ext in {".xlf", ".xliff"} and kind == ("xliff2" if catalog.xliff2 else "xliff12"))
    name = Path(catalog.name).name if kind == "original" else Path(catalog.name).stem + option[2]
    # Render first so invalid inline edits cannot escape validation by changing format.
    working = catalog
    if same and catalog.ext in {".po", ".pot"} and (not catalog.language or catalog.language.lower().startswith("sv")):
        working = copy.deepcopy(catalog)
        stamp_generator(working)
    rendered = working.render()
    data = rendered if same else _convert(catalog, kind, cancel)
    reopened = parse_checked(name, data)
    if same:
        check_roundtrip(catalog, reopened)
    else:
        expected = Counter((record["source"], record["target"]) for record in _records(catalog, cancel))
        actual = Counter((unit.source_for(variant), target) for unit in reopened.units
                         for variant, target in enumerate(unit.targets))
        if actual != expected:
            raise ValueError("Formatbytet kunde inte bevara alla källtexter och översättningar. Välj originalformatet.")
    check_cancel(cancel)
    return Export(name, data, catalog.path)


def write_exports(exports, directory, cancel=None, progress=lambda *args: None):
    """Create export copies without replacing existing files or imported originals."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for index, item in enumerate(exports, 1):
        check_cancel(cancel)
        path = directory / item.name
        stem, suffix, number = path.stem, path.suffix, 2
        descriptor, temporary = tempfile.mkstemp(prefix=".ordverk-export-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(item.data)
                output.flush()
                os.fsync(output.fileno())
            while True:
                check_cancel(cancel)
                try:
                    if item.source_path and path.resolve() == item.source_path.resolve():
                        raise FileExistsError
                    # Publishing a complete file with link() is atomic and cannot overwrite.
                    os.link(temporary, path)
                    break
                except FileExistsError:
                    path = directory / f"{stem}-{number}{suffix}"
                    number += 1
        finally:
            Path(temporary).unlink(missing_ok=True)
        verify_written(path, item.data)
        written.append(path)
        progress(f"Exporterar {index}/{len(exports)}: {path.name}", index, len(exports))
    return written
