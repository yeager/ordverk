"""Strict unified-diff import, applied to an in-memory catalog, never to arbitrary paths."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath

from .catalog import Catalog, digest


@dataclass(frozen=True)
class Patch:
    before: str
    after: str
    hunks: list


def filename(header):
    value = header[4:].rstrip("\r\n").split("\t", 1)[0]
    if value.startswith('"'):
        value = shlex.split(value)[0]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not value:
        raise ValueError("Diffen innehåller en otillåten filsökväg.")
    return value


def parse_diff(text):
    lines = text.splitlines(keepends=True)
    patches, index = [], 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        before = filename(lines[index])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Diffen saknar filhuvud för den nya versionen.")
        after = filename(lines[index])
        index += 1
        hunks = []
        while index < len(lines):
            if lines[index].startswith(("diff --git ", "--- ")):
                break
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[index])
            if not match:
                index += 1
                continue
            old, old_count, new, new_count = [int(v) if v is not None else 1 for v in match.groups()]
            index += 1
            body, seen_old, seen_new = [], 0, 0
            while index < len(lines) and (seen_old < old_count or seen_new < new_count):
                line = lines[index]
                if not line or line[0] not in " +-":
                    raise ValueError("Diffens ändringsblock är ofullständigt.")
                body.append(line)
                seen_old += line[0] in " -"
                seen_new += line[0] in " +"
                index += 1
                if index < len(lines) and lines[index].startswith("\\ No newline at end of file"):
                    body[-1] = body[-1].removesuffix("\n").removesuffix("\r")
                    index += 1
            if (seen_old, seen_new) != (old_count, new_count):
                raise ValueError("Diffens radantal stämmer inte.")
            hunks.append((old, old_count, new, new_count, body))
        if not hunks:
            raise ValueError("Diffen innehåller inga stödda textändringar.")
        patches.append(Patch(before, after, hunks))
    if not patches:
        raise ValueError("Ingen unified diff hittades. Välj en .diff- eller .patch-fil med textändringar.")
    return patches


def patch_text(original, patch):
    lines = original.splitlines(keepends=True)
    result, cursor = [], 0
    for old, count, new, _new_count, body in patch.hunks:
        offset = old - 1 if count else old
        if offset < cursor or offset > len(lines):
            raise ValueError("Diffens radnummer stämmer inte med den öppna filen.")
        result.extend(lines[cursor:offset])
        if len(result) != (new - 1 if _new_count else new):
            raise ValueError("Diffens nya radnummer stämmer inte.")
        cursor = offset
        for line in body:
            kind, content = line[0], line[1:]
            if kind in " -":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise ValueError("Diffen matchar inte filens innehåll exakt. Importera rätt grundversion först.")
                cursor += 1
            if kind in " +":
                result.append(content)
    result.extend(lines[cursor:])
    return "".join(result)


@dataclass
class DiffProposal:
    original: object
    candidate: object
    expected: str
    changed: list
    removed: int
    path: str

    def apply(self):
        if digest(self.original.render()) != self.expected:
            raise ValueError("Katalogen har ändrats sedan diffen öppnades. Importera diffen på nytt.")
        self.original.__dict__.update(self.candidate.__dict__)


def propose_diff(catalog, patch):
    before = catalog.render()
    encoding = catalog.po.encoding if catalog.ext in {".po", ".pot"} else (catalog.tree.docinfo.encoding or "utf-8") if hasattr(catalog, "tree") else "utf-8-sig"
    text = patch_text(before.decode(encoding), patch)
    after = text.encode(encoding)
    if encoding == "utf-8-sig" and not before.startswith(b"\xef\xbb\xbf"):
        after = after.removeprefix(b"\xef\xbb\xbf")
    candidate = Catalog(catalog.name, after, path=catalog.path, origin=catalog.origin, reference=catalog.reference)
    candidate.fingerprint = catalog.fingerprint
    candidate.import_raw = catalog.import_raw
    candidate.import_name = catalog.import_name
    candidate.import_encoding = catalog.import_encoding
    candidate._pending_structure = before != after or catalog.dirty
    # Source/context identity avoids positional PO keys confusing inserted or removed units.
    def identity(unit):
        return unit.source, unit.context, unit.source_plural
    from collections import defaultdict, deque
    previous = defaultdict(deque)
    for unit in catalog.units:
        previous[unit.key if catalog.ext == ".json" else identity(unit)].append(unit)
    changed = []
    for unit in candidate.units:
        prior = previous[unit.key if catalog.ext == ".json" else identity(unit)]
        old = prior.popleft() if prior else None
        if old is not None and catalog.ext == ".json" and unit.source_is_key and not old.source_is_key:
            unit.source, unit.source_is_key = old.source, False
        unit.imported = old.imported if old is not None else None
        if old is None or old.targets != unit.targets or old.reviewed != unit.reviewed:
            unit.reviewed = False
            unit.original = (("__ordverk_diff__",), True)
            changed.append(unit)
    return DiffProposal(catalog, candidate, digest(before), changed,
                        sum(len(units) for units in previous.values()), patch.after)
