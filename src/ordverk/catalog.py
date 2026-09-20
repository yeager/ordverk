"""Editable catalogs. Parsers retain the original object graph and unknown metadata."""
from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import os
import re
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import polib
from lxml import etree as ET

EXTENSIONS = {".po", ".pot", ".ts", ".xlf", ".xliff", ".json"}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
PLURALS = ("zero", "one", "two", "few", "many", "other")
SV_PLURALS = "nplurals=2; plural=(n != 1);"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def localname(element) -> str:
    return ET.QName(element).localname if isinstance(element.tag, str) else ""


def child(element, name):
    return next((item for item in element if localname(item) == name), None)


def children(element, name):
    return [item for item in element if localname(item) == name]


def qualified(element, name):
    namespace = ET.QName(element).namespace
    return f"{{{namespace}}}{name}" if namespace else name


def text_content(element) -> str:
    return "" if element is None else "".join(element.itertext())


def parse_xml(data: bytes):
    parser = ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    tree = ET.parse(io.BytesIO(data), parser)
    # A bare <!DOCTYPE TS> is normal Qt output; declarations and external DTDs are not needed.
    if tree.docinfo.internalDTD is not None:
        if tree.docinfo.system_url or tree.docinfo.public_id or list(tree.docinfo.internalDTD.iterentities()):
            raise ValueError("XML med externa DTD:er eller entitetsdeklarationer stöds inte.")
    return tree


@dataclass
class InlineCodec:
    tokens: dict[str, str] = field(default_factory=dict)
    nsmap: dict = field(default_factory=dict)

    @classmethod
    def read(cls, element):
        codec = cls(nsmap=element.nsmap if element is not None else {})
        if element is None:
            return "", codec
        counter = 0

        def walk(parent):
            nonlocal counter
            result = parent.text or ""
            for node in parent:
                counter += 1
                number = counter
                token = f"⟦{number}⟧"
                if not isinstance(node.tag, str) or (len(node) == 0 and not node.text):
                    codec.tokens[token] = ET.tostring(node, encoding="unicode", with_tail=False)
                    result += token
                else:
                    shell = copy.deepcopy(node)
                    shell.clear(keep_tail=False)
                    shell.tag = node.tag
                    shell.attrib.update(node.attrib)
                    serialized = ET.tostring(shell, encoding="unicode", with_tail=False)
                    opening = serialized[:-2] + ">"
                    tag = opening[1:].split(" ", 1)[0].rstrip(">")
                    end = f"⟦/{number}⟧"
                    codec.tokens[token] = opening
                    codec.tokens[end] = f"</{tag}>"
                    result += token + walk(node) + end
                result += node.tail or ""
            return result

        return walk(element), codec

    def write(self, element, text):
        actual = Counter(re.findall(r"⟦/?\d+⟧", text))
        if actual != Counter(self.tokens.keys()):
            raise ValueError("Bevara alla inlinekoder (⟦1⟧, ⟦/1⟧ …) exakt en gång.")
        stack = []
        for token in re.findall(r"⟦/?\d+⟧", text):
            if token.startswith("⟦/"):
                if not stack or stack.pop() != token.replace("/", ""):
                    raise ValueError("Inlinekodernas start- och slutmarkörer måste vara korrekt nästlade.")
            elif token.replace("⟦", "⟦/") in self.tokens:
                stack.append(token)
        if stack:
            raise ValueError("En inlinekod saknar slutmarkör.")
        fragment = html.escape(text, quote=False)
        for token, markup in self.tokens.items():
            fragment = fragment.replace(token, markup)
        wrapper = ET.Element("ordverk-fragment", nsmap=self.nsmap)
        prefix = ET.tostring(wrapper, encoding="unicode")[:-2] + ">"
        try:
            parsed = ET.fromstring((prefix + fragment + "</ordverk-fragment>").encode(),
                                   parser=ET.XMLParser(resolve_entities=False, no_network=True))
        except ET.XMLSyntaxError as exc:
            raise ValueError("Inlinekodernas ordning ger ogiltig XML.") from exc
        for node in list(element):
            element.remove(node)
        element.text = parsed.text
        for node in parsed:
            element.append(node)


@dataclass
class Unit:
    key: str
    source: str
    targets: list[str]
    context: str = ""
    notes: str = ""
    references: str = ""
    source_plural: str = ""
    variants: list[str] = field(default_factory=lambda: ["Översättning"])
    reviewed: bool = False
    source_is_key: bool = False
    binding: object = None
    codecs: list[InlineCodec] = field(default_factory=list)
    revision: int = 0
    flags: list[str] = field(default_factory=list)
    original: tuple = field(init=False)

    def __post_init__(self):
        self.checkpoint()

    def checkpoint(self):
        self.original = (tuple(self.targets), self.reviewed)

    @property
    def changed(self):
        return self.original != (tuple(self.targets), self.reviewed)

    @property
    def status(self):
        if not all(self.targets):
            return "Oöversatt"
        return "Granskad" if self.reviewed else "Att granska"

    def source_for(self, variant):
        return self.source_plural if variant > 0 and self.source_plural else self.source

    def edit(self, variant, text):
        if self.targets[variant] != text:
            self.targets[variant] = text
            self.reviewed = False
            self.revision += 1


class Catalog:
    def __init__(self, name, data, path=None, origin="", reference=None):
        self.name = name
        self.path = Path(path).absolute() if path else None
        self.origin = origin or str(self.path or name)
        self.ext = Path(name).suffix.lower()
        self.raw = data
        self.fingerprint = digest(data)
        self._pending_structure = False
        self.units: list[Unit] = []
        self.language = ""
        self.reference = reference
        if self.ext in {".po", ".pot"}:
            self._load_po(data)
        elif self.ext in {".ts", ".xlf", ".xliff"}:
            self.tree = parse_xml(data)
            if self.ext == ".ts":
                self._load_ts()
            else:
                self._load_xliff()
        elif self.ext == ".json":
            self._load_json(data, reference)
        else:
            raise ValueError(f"Filformatet {self.ext} stöds inte.")
        if not self.units:
            raise ValueError("Filen innehåller inga redigerbara översättningssträngar.")

    @classmethod
    def open(cls, path, **kwargs):
        path = Path(path)
        result = cls(path.name, path.read_bytes(), path=path, **kwargs)
        from .state import restore_state
        restore_state(result)
        return result

    @property
    def dirty(self):
        return getattr(self, "_pending_structure", False) or any(unit.changed for unit in self.units)

    def _load_po(self, data):
        encoding = polib.detect_encoding(data)
        self.po = polib.pofile(data.decode(encoding), encoding=encoding, check_for_duplicates=True)
        self.language = self.po.metadata.get("Language", "")
        for i, entry in enumerate(self.po):
            if entry.obsolete or not entry.msgid:
                continue
            plural = bool(entry.msgid_plural)
            # Swedish has two forms; keep extra existing forms visible instead of deleting them.
            n = max(2, max(entry.msgstr_plural, default=1) + 1) if plural else 1
            targets = [entry.msgstr_plural.get(j, "") for j in range(n)] if plural else [entry.msgstr]
            self.units.append(Unit(str(i), entry.msgid, targets, context=entry.msgctxt or "",
                                   notes="\n".join(filter(None, [entry.comment, entry.tcomment])),
                                   references=", ".join(f"{p}:{line}" for p, line in entry.occurrences),
                                   source_plural=entry.msgid_plural,
                                   variants=["Ental · n = 1", "Flertal · n ≠ 1"][:n]
                                   + [f"Form {j}" for j in range(2, n)] if plural else ["Översättning"],
                                   reviewed=bool(all(targets) and "fuzzy" not in entry.flags), binding=i,
                                   flags=list(entry.flags)))

    def _load_ts(self):
        root = self.tree.getroot()
        if localname(root) != "TS":
            raise ValueError("Filen är inte en Qt TS-katalog.")
        self.language = root.get("language", "")
        for context in children(root, "context"):
            context_name = text_content(child(context, "name"))
            for message in children(context, "message"):
                target = child(message, "translation")
                if target is not None and target.get("type") in {"vanished", "obsolete"}:
                    continue
                source, source_codec = InlineCodec.read(child(message, "source"))
                if not source:
                    continue
                plural = message.get("numerus") == "yes"
                containers = children(target, "numerusform") if target is not None and plural else [target]
                if plural and not containers:
                    containers = [None, None]
                values, codecs, bindings, names = [], [], [], []
                for j, container in enumerate(containers):
                    variants = children(container, "lengthvariant") if container is not None else []
                    for k, variant in enumerate(variants or [container]):
                        value, codec = InlineCodec.read(variant)
                        values.append(value)
                        codecs.append(codec if value else source_codec)
                        bindings.append((j, k if variants else None))
                        names.append(("Ental" if j == 0 else "Flertal") if plural else "Översättning")
                        if variants:
                            names[-1] += f" · längdvariant {k + 1}"
                path = self.tree.getpath(message)
                self.units.append(Unit(path, source, values, context=context_name,
                                       notes="\n".join(text_content(child(message, tag)) for tag in
                                                       ("comment", "extracomment", "translatorcomment")).strip(),
                                       references=", ".join(f"{loc.get('filename', '')}:{loc.get('line', '')}"
                                                            for loc in children(message, "location")),
                                       variants=names, reviewed=bool(all(values) and target is not None
                                                                   and target.get("type") != "unfinished"),
                                       codecs=codecs, binding=(path, plural, bindings)))

    def _load_xliff(self):
        root = self.tree.getroot()
        if localname(root) != "xliff" or root.get("version", "").split(".")[0] not in {"1", "2"}:
            raise ValueError("Endast XLIFF 1.2 och 2.x stöds.")
        self.xliff2 = root.get("version", "").startswith("2")
        self.language = root.get("trgLang", "")
        for element in root.iter():
            if localname(element) == "file" and not self.xliff2:
                self.language = element.get("target-language", self.language)
            if localname(element) != ("segment" if self.xliff2 else "trans-unit"):
                continue
            if any(e.get("translate") == "no" for e in [element, *element.iterancestors()]):
                continue
            source_element = child(element, "source")
            if source_element is None:
                continue
            source, source_codec = InlineCodec.read(source_element)
            target = child(element, "target")
            value, codec = InlineCodec.read(target)
            path = self.tree.getpath(element)
            container = element.getparent() if self.xliff2 else element
            state = element.get("state", "") if self.xliff2 else (
                target.get("state", "") if target is not None else "")
            self.units.append(Unit(path, source, [value],
                                   context=" · ".join(filter(None, [container.get("id"),
                                                                   container.get("resname"), element.get("id")
                                                                   if self.xliff2 else ""])),
                                   notes="\n".join(text_content(e) for e in container.iter()
                                                   if localname(e) in {"note", "context"}),
                                   codecs=[codec if value else source_codec], binding=path,
                                   reviewed=bool(value and (state in {"final", "signed-off", "reviewed"}
                                                           or container.get("approved") == "yes"))))

    def _load_json(self, data, reference):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"JSON innehåller en dubbel nyckel: {key}")
                result[key] = value
            return result
        self.json = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
        if not isinstance(self.json, (dict, list)):
            raise ValueError("JSON-katalogen måste innehålla ett objekt eller en lista.")
        reference_data = json.loads(Path(reference).read_text(encoding="utf-8-sig")) if reference else None
        metadata = {"@locale", "locale", "targetLanguage", "target_language", "@@locale"}
        if isinstance(self.json, dict):
            self.language = next((self.json[k] for k in metadata if isinstance(self.json.get(k), str)), "")

        def add(path, source, value, context="", source_is_key=False):
            if reference_data is not None and source_is_key:
                try:
                    source = lookup(reference_data, path)
                except (KeyError, IndexError, TypeError):
                    pass
                else:
                    source_is_key = not isinstance(source, str)
                    if source_is_key:
                        source = str(path[-1])
            self.units.append(Unit(json.dumps(path, ensure_ascii=False), source, [value],
                                   context=context or "/".join(map(str, path)),
                                   source_is_key=source_is_key, reviewed=False, binding=path))

        def walk(obj, path=()):
            if isinstance(obj, dict):
                if obj.get("translate") is False or obj.get("translatable") is False:
                    return
                target_key = next((k for k in ("target", "translation", "value") if k in obj), "target")
                if isinstance(obj.get("source"), str):
                    value = obj.get(target_key, "")
                    if isinstance(value, str):
                        add(path + (target_key,), obj["source"], value, str(obj.get("context", "")))
                        return
                    if isinstance(value, dict):
                        for form, text in value.items():
                            if form in PLURALS and isinstance(text, str):
                                add(path + (target_key, form), obj["source"], text,
                                    f"{obj.get('context', '')} · {form}")
                        return
                    raise ValueError("En explicit JSON-översättning måste vara text eller pluralobjekt.")
                for key, value in obj.items():
                    if key in metadata or key.startswith("@") or key in {"$schema", "$id"}:
                        continue
                    if isinstance(value, str):
                        add(path + (key,), key, value, source_is_key=True)
                    else:
                        walk(value, path + (key,))
            elif isinstance(obj, list):
                for i, value in enumerate(obj):
                    if isinstance(value, str):
                        add(path + (i,), str(i), value, source_is_key=True)
                    else:
                        walk(value, path + (i,))
        walk(self.json)

    def attach_reference(self, path):
        if self.ext != ".json":
            raise ValueError("Källkatalog behövs här endast för JSON med strängnycklar.")
        reference = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        count = 0
        for unit in self.units:
            if not unit.source_is_key:
                continue
            try:
                value = lookup(reference, unit.binding)
            except (KeyError, IndexError, TypeError):
                continue
            if isinstance(value, str):
                unit.source, unit.source_is_key = value, False
                unit.revision += 1
                count += 1
        self.reference = str(path)
        return count

    def swedish_copy(self):
        """Explicitly turn a source/foreign-language catalog into a new, empty Swedish work copy."""
        template = copy.deepcopy(self)
        template.language = ""
        for unit in template.units:
            if template.ext == ".json" and unit.source_is_key:
                unit.source = unit.targets[0] or unit.source
                unit.source_is_key = False
            unit.targets = ["" for _ in unit.targets]
            if template.ext in {".po", ".pot"} and unit.source_plural:
                unit.targets = ["", ""]
                unit.variants = ["Ental · n = 1", "Flertal · n ≠ 1"]
            elif template.ext == ".ts" and unit.binding[1]:
                path, plural, _bindings = unit.binding
                message = template.tree.xpath(path, namespaces={k: v for k, v in template.tree.getroot().nsmap.items() if k})[0]
                target = child(message, "translation")
                if target is not None:
                    for form in children(target, "numerusform"):
                        target.remove(form)
                _, codec = InlineCodec.read(child(message, "source"))
                unit.targets, unit.variants, unit.codecs = ["", ""], ["Ental", "Flertal"], [codec, codec]
                unit.binding = (path, plural, [(0, None), (1, None)])
            unit.reviewed = False
            unit.original = (("__ordverk_template__",), True)
        if template.ext == ".json" and isinstance(template.json, dict):
            for key in ("@locale", "@@locale", "locale", "targetLanguage", "target_language"):
                if key in template.json:
                    template.json[key] = "sv"
        name = Path(self.name).stem + ".sv" + (".po" if self.ext == ".pot" else self.ext)
        # Empty inline translations are valid placeholders for untranslated units.
        data = template.render()
        result = Catalog(name, data, origin=self.origin + " · svensk arbetskopia")
        for before, after in zip(template.units, result.units):
            if template.ext == ".json":
                after.source, after.source_is_key = before.source, False
        return result

    def render(self) -> bytes:
        if not self.dirty:
            return self.raw
        language = self.language.lower().replace("-", "_")
        if language and not language.startswith("sv"):
            raise ValueError(f"Katalogens målspråk är {self.language}. Skapa en svensk katalog eller mall först.")
        if self.ext in {".po", ".pot"}:
            po = copy.deepcopy(self.po)
            po.metadata["Language"] = "sv"
            po.metadata["Plural-Forms"] = SV_PLURALS
            for unit in self.units:
                if not unit.changed:
                    continue
                entry = po[unit.binding]
                if unit.source_plural:
                    entry.msgstr_plural = dict(enumerate(unit.targets))
                else:
                    entry.msgstr = unit.targets[0]
                entry.flags = [flag for flag in entry.flags if flag != "fuzzy"]
                if not unit.reviewed:
                    entry.flags.append("fuzzy")
            return str(po).encode(po.encoding)
        if self.ext == ".json":
            obj = copy.deepcopy(self.json)
            for unit in self.units:
                if unit.changed:
                    parent = lookup(obj, unit.binding[:-1])
                    parent[unit.binding[-1]] = unit.targets[0]
            match = re.search(rb"\n([ \t]+)\S", self.raw)
            indent = match.group(1).decode() if match else 2
            result = json.dumps(obj, ensure_ascii=False, indent=indent) + "\n"
            if b"\r\n" in self.raw:
                result = result.replace("\n", "\r\n")
            return (b"\xef\xbb\xbf" if self.raw.startswith(b"\xef\xbb\xbf") else b"") + result.encode()
        tree = copy.deepcopy(self.tree)
        if self.ext == ".ts":
            tree.getroot().set("language", "sv_SE")
        elif self.xliff2:
            tree.getroot().set("trgLang", "sv")
        else:
            for file in tree.getroot().iter():
                if localname(file) == "file":
                    file.set("target-language", "sv")
        for unit in self.units:
            if not unit.changed:
                continue
            if self.ext == ".ts":
                path, plural, bindings = unit.binding
                message = tree.xpath(path, namespaces={k: v for k, v in tree.getroot().nsmap.items() if k})[0]
                target = child(message, "translation")
                if target is None:
                    target = ET.SubElement(message, qualified(message, "translation"))
                for i, (form, length) in enumerate(bindings):
                    container = target
                    if plural:
                        forms = children(target, "numerusform")
                        while len(forms) <= form:
                            forms.append(ET.SubElement(target, qualified(target, "numerusform")))
                        container = forms[form]
                    if length is not None:
                        container = children(container, "lengthvariant")[length]
                    if unit.targets[i]:
                        unit.codecs[i].write(container, unit.targets[i])
                    else:
                        for node in list(container):
                            container.remove(node)
                        container.text = None
                if unit.reviewed and all(unit.targets):
                    target.attrib.pop("type", None)
                else:
                    target.set("type", "unfinished")
            else:
                element = tree.xpath(unit.binding, namespaces={k: v for k, v in tree.getroot().nsmap.items() if k})[0]
                target = child(element, "target")
                if target is None:
                    target = ET.Element(qualified(element, "target"))
                    element.insert(list(element).index(child(element, "source")) + 1, target)
                if unit.targets[0]:
                    unit.codecs[0].write(target, unit.targets[0])
                else:
                    for node in list(target):
                        target.remove(node)
                    target.text = None
                if self.xliff2:
                    element.set("state", "reviewed" if unit.reviewed else "translated" if all(unit.targets) else "initial")
                else:
                    element.set("approved", "yes" if unit.reviewed else "no")
                    target.set("state", "final" if unit.reviewed else "needs-review-translation")
        encoding = tree.docinfo.encoding or "UTF-8"
        result = ET.tostring(tree, encoding=encoding, xml_declaration=self.raw.lstrip().startswith(b"<?xml"))
        return result + (b"\n" if self.raw.endswith(b"\n") and not result.endswith(b"\n") else b"")

    def save(self, destination=None, *, overwrite=False):
        path = Path(destination).absolute() if destination else self.path
        if path is None:
            raise ValueError("Välj en lokal plats för filen.")
        if path.suffix.lower() not in ({".po", ".pot"} if self.ext in {".po", ".pot"} else
                                        {".xlf", ".xliff"} if self.ext in {".xlf", ".xliff"} else {self.ext}):
            raise ValueError("Spara med samma filformat som originalet.")
        if path.is_symlink():
            raise ValueError("Välj den verkliga filen i stället för en symbolisk länk.")
        data = self.render()
        # Reparse before touching disk: serializer bugs must never corrupt an existing catalog.
        Catalog(path.name, data, reference=self.reference)
        expected = path.read_bytes() if path.exists() else None
        if self.path == path and (expected is None or digest(expected) != self.fingerprint):
            raise ValueError("Filen har ändrats eller tagits bort utanför Ordverk. Spara som en ny fil.")
        if self.path != path and expected is not None and not overwrite:
            raise FileExistsError("Filen finns redan. Bekräfta att den ska ersättas.")
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if expected is not None else 0o644
        if expected is not None and data != expected:
            # Content-addressed backups never overwrite a different historical version.
            backup = path.with_name(path.name + f".ordverk-{digest(expected)[:12]}.bak")
            if not backup.exists():
                atomic_write(backup, expected, mode)
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != expected:
            raise ValueError("Filen ändrades under sparningen. Försök med ett nytt filnamn.")
        atomic_write(path, data, mode)
        refreshed = Catalog(path.name, data, path=path, origin=self.origin, reference=self.reference)
        # JSON has no portable review flag. Persist local state alongside the cache.
        for before, after in zip(self.units, refreshed.units):
            after.reviewed = before.reviewed
            if self.ext == ".json":
                after.source, after.source_is_key = before.source, before.source_is_key
            after.checkpoint()
        self.__dict__.update(refreshed.__dict__)
        from .state import save_state
        save_state(self)
        return path


def lookup(obj, path):
    for key in path:
        obj = obj[key]
    return obj


def atomic_write(path: Path, data: bytes, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
