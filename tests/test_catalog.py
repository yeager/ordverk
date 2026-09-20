import json
from pathlib import Path

import polib
import pytest
from lxml import etree as ET

from ordverk.catalog import Catalog, InlineCodec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("name", ["sv.po", "sv.ts", "sv.xlf", "sv.json"])
def test_unedited_is_byte_identical(name):
    catalog = Catalog.open(EXAMPLES / name)
    assert catalog.render() == (EXAMPLES / name).read_bytes()


def test_po_plural_comments_flags_and_metadata_survive(tmp_path):
    catalog = Catalog.open(EXAMPLES / "sv.po")
    plural = next(u for u in catalog.units if u.source_plural)
    plural.edit(1, "{count} markerade filer")
    catalog.save(tmp_path / "sv.po")
    result = polib.pofile(str(tmp_path / "sv.po"))
    assert result.metadata["Project-Id-Version"] == "Ordverk Demo 1.0"
    entry = result.find("{count} file selected")
    assert entry.msgstr_plural[1] == "{count} markerade filer"
    assert "fuzzy" in entry.flags
    assert "python-brace-format" in entry.flags
    assert entry.comment == "Translators: count of selected files"
    assert result.find("Save", msgctxt="menu").occurrences == [("src/window.py", "42")]


def test_atomic_save_backup_external_change_and_permissions(tmp_path):
    path = tmp_path / "sv.po"
    path.write_bytes((EXAMPLES / "sv.po").read_bytes())
    path.chmod(0o640)
    catalog = Catalog.open(path)
    original = path.read_bytes()
    catalog.units[1].edit(0, "Öppna en fil")
    catalog.save()
    assert path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob("*.bak"))[0].read_bytes() == original
    assert not catalog.dirty
    catalog.units[1].edit(0, "Öppna fil")
    path.write_bytes(path.read_bytes() + b"\n# External edit\n")
    with pytest.raises(ValueError, match="utanför"):
        catalog.save()
    assert b"External edit" in path.read_bytes()


def test_save_as_refuses_overwrite_and_symlink(tmp_path):
    catalog = Catalog.open(EXAMPLES / "sv.po")
    path = tmp_path / "sv.po"
    path.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        catalog.save(path)
    link = tmp_path / "link.po"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symbolisk"):
        catalog.save(link, overwrite=True)
    assert path.read_bytes() == b"original"


def test_ts_plural_states_and_unknown_attributes(tmp_path):
    raw = b'''<!DOCTYPE TS><TS language="sv_SE" version="2.1"><context><name>UI</name>
      <message numerus="yes" custom="keep"><source>%n file(s)</source><comment>count</comment>
      <translation type="unfinished"><numerusform/><numerusform/></translation></message>
      <message><source>Removed</source><translation type="vanished">Borta</translation></message></context></TS>'''
    catalog = Catalog("sv.ts", raw)
    assert len(catalog.units) == 1
    unit = catalog.units[0]
    unit.edit(0, "%n fil")
    unit.edit(1, "%n filer")
    unit.reviewed = True
    catalog.save(tmp_path / "sv.ts")
    tree = ET.parse(str(tmp_path / "sv.ts"))
    assert tree.xpath("string(//message/@custom)") == "keep"
    assert tree.xpath("//numerusform/text()") == ["%n fil", "%n filer"]
    assert tree.xpath("//translation[@type='vanished']/text()") == ["Borta"]
    assert not tree.xpath("//translation[@type='unfinished']")


@pytest.mark.parametrize("version", ["1.2", "2.0"])
def test_xliff_inline_codes_preserved_and_invalid_codes_rejected(version):
    if version == "1.2":
        raw = b'''<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"><file target-language="sv"><body>
        <trans-unit id="u"><source>Hello <g id="1">world <x id="2" equiv-text="%s"/></g>!</source><target/></trans-unit>
        <trans-unit id="skip" translate="no"><source>Skip</source></trans-unit></body></file></xliff>'''
    else:
        raw = b'''<xliff version="2.0" xmlns="urn:oasis:names:tc:xliff:document:2.0" trgLang="sv"><file id="f"><unit id="u">
        <segment id="s"><source>Hello <pc id="1">world <ph id="2" equiv="%s"/></pc>!</source><target/></segment>
        </unit><unit id="skip" translate="no"><segment><source>Skip</source></segment></unit></file></xliff>'''
    catalog = Catalog("sv.xlf", raw)
    assert len(catalog.units) == 1
    unit = catalog.units[0]
    unit.edit(0, unit.source.replace("Hello", "Hej").replace("world", "värld"))
    result = Catalog("sv.xlf", catalog.render())
    assert result.units[0].targets[0] == "Hej ⟦1⟧värld ⟦2⟧⟦/1⟧!"
    assert b'id="2"' in result.raw
    unit.edit(0, "Hej världen!")
    with pytest.raises(ValueError, match="inlinekoder"):
        catalog.render()


def test_inline_nesting_and_xml_injection():
    source = ET.fromstring(b'<source>Hi <g id="1">name <g id="2">inner</g></g></source>')
    text, codec = InlineCodec.read(source)
    target = ET.Element("target")
    codec.write(target, text.replace("Hi", "<script>"))
    assert target.text == "<script> "
    assert len(target.findall("script")) == 0
    with pytest.raises(ValueError):
        codec.write(target, "⟦1⟧⟦2⟧X⟦/1⟧⟦/2⟧")


@pytest.mark.parametrize("raw", [b'<!DOCTYPE TS SYSTEM "file:///etc/passwd"><TS/>',
                                 b'<!DOCTYPE TS [<!ENTITY x "hello">]><TS>&x;</TS>'])
def test_external_entities_rejected(raw):
    with pytest.raises(ValueError):
        Catalog("sv.ts", raw)


def test_json_reference_metadata_arrays_and_nonstring_values(tmp_path):
    raw = b'{"@locale":"sv","count":7,"enabled":true,"nested":{"save":""},"list":[""],"@nested":{"description":"Keep"}}'
    reference = tmp_path / "en.json"
    reference.write_text('{"nested":{"save":"Save"},"list":["Hello"]}')
    catalog = Catalog("sv.json", raw, reference=reference)
    assert [u.source for u in catalog.units] == ["Save", "Hello"]
    assert not any(u.source_is_key for u in catalog.units)
    catalog.units[0].edit(0, "Spara")
    result = json.loads(catalog.render())
    assert result["count"] == 7 and result["enabled"] is True
    assert result["@nested"]["description"] == "Keep"
    assert result["nested"]["save"] == "Spara"


def test_json_explicit_plural_and_skipped_entry():
    raw = b'[{"source":"%n files","target":{"one":"","other":""},"notes":"keep"},{"source":"Skip","target":"Keep","translate":false}]'
    catalog = Catalog("sv.json", raw)
    assert len(catalog.units) == 2
    catalog.units[1].edit(0, "%n filer")
    result = json.loads(catalog.render())
    assert result[0]["target"]["other"] == "%n filer"
    assert result[0]["notes"] == "keep"
    assert result[1]["target"] == "Keep"


def test_json_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="dubbel"):
        Catalog("sv.json", b'{"a":"first","a":"second"}')


def test_swedish_source_copy_retains_source_and_original(tmp_path):
    catalog = Catalog.open(EXAMPLES / "en.json")
    copied = catalog.swedish_copy()
    assert copied.path is None and copied.language == "sv"
    assert copied.units[0].source == "Save"
    assert not copied.units[0].source_is_key
    assert all(not any(u.targets) for u in copied.units)
    copied.units[0].edit(0, "Spara")
    copied.save(tmp_path / "sv.json")
    assert copied.units[0].source == "Save"
    assert catalog.units[0].targets == ["Save"]


def test_can_clear_inline_translation():
    catalog = Catalog.open(EXAMPLES / "sv.xlf")
    catalog.units[0].edit(0, "Välkommen, ⟦1⟧!")
    catalog.units[0].checkpoint()
    catalog.units[0].edit(0, "")
    assert b"<target" in catalog.render()


def test_ts_length_variants_and_namespaced_xliff():
    catalog = Catalog("sv.ts", b'<TS><context><name>UI</name><message><source>Open</source><translation variants="yes"><lengthvariant>Oppna</lengthvariant><lengthvariant>O</lengthvariant></translation></message></context></TS>')
    catalog.units[0].edit(1, "Ö")
    assert Catalog("sv.ts", catalog.render()).units[0].targets == ["Oppna", "Ö"]
    prefixed = Catalog("sv.xlf", b'<x:xliff xmlns:x="urn:oasis:names:tc:xliff:document:2.0" version="2.0"><x:file id="f"><x:unit id="u"><x:segment><x:source>Save</x:source></x:segment></x:unit></x:file></x:xliff>')
    prefixed.units[0].edit(0, "Spara")
    assert Catalog("sv.xlf", prefixed.render()).units[0].targets == ["Spara"]


@pytest.mark.parametrize("extension, raw", [
    ("po", b'msgid ""\nmsgstr "Language: ru\\n"\n\nmsgid "File"\nmsgid_plural "Files"\nmsgstr[0] "A"\nmsgstr[1] "B"\nmsgstr[2] "C"\n'),
    ("ts", b'<TS language="ru"><context><name>UI</name><message numerus="yes"><source>%n file(s)</source><translation><numerusform>A</numerusform><numerusform>B</numerusform><numerusform>C</numerusform></translation></message></context></TS>'),
])
def test_foreign_plural_template_uses_two_swedish_forms(extension, raw):
    catalog = Catalog("ru." + extension, raw)
    copied = catalog.swedish_copy()
    assert len(copied.units[0].targets) == 2
    assert len(catalog.units[0].targets) == 3
