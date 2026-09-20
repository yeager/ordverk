import copy
import json
from dataclasses import replace

import pytest

from ordverk.catalog import Catalog, Unit
from ordverk.quality import Issue, Quality
from ordverk.quality_index import ReviewIndex, categories
from ordverk.resources import ResourceStore
from ordverk.selection import matches
from ordverk.settings import Settings


@pytest.fixture
def quality(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDVERK_HOME", str(tmp_path / "cache"))
    return Quality(Settings(use_hunspell=False, use_aspell=False), ResourceStore())


@pytest.mark.parametrize("source,target,mismatch", [
    ("Save", "spara", True), ("save", "Spara", True),
    ("Open", "Öppna", False), ("open", "öppna", False),
    ('<b>Save</b>', '<b>spara</b>', True),
    ('{filename}: Save', '{filename}: spara', True),
    ('%(name)s: open', '%(name)s: Öppna', True),
    ('%1 &Open', '%1 &öppna', True),
    ('⟦1⟧Open⟦/1⟧', '⟦1⟧öppna⟦/1⟧', True),
    ('123', '123', False), ('Save', '', False),
])
def test_initial_case_both_directions_and_visible_letters(quality, source, target, mismatch):
    issues = quality.check(Unit("a", source, [target]))
    assert ("case" in categories(issues)) is mismatch
    assert sum(i.rule == 'inconsistent-capitalization' for i in issues) == int(mismatch)


def test_key_json_has_no_fake_case_comparison(quality):
    unit = Catalog('sv.json', b'{"save":"Spara"}').units[0]
    assert unit.source_is_key
    assert "case" not in categories(quality.check(unit))


@pytest.mark.parametrize("name,data", [
    ('sv.po', 'msgid "Save %s"\nmsgstr "spara"\n\nmsgid "Open"\nmsgstr "Öppna"\n'),
    ('sv.ts', '<TS><context><name>Menu</name><message><source>Save %s</source><translation>spara</translation></message><message><source>Open</source><translation>Öppna</translation></message></context></TS>'),
    ('sv.xlf', '<xliff version="1.2"><file><body><trans-unit id="a"><source>Save %s</source><target>spara</target></trans-unit><trans-unit id="b"><source>Open</source><target>Öppna</target></trans-unit></body></file></xliff>'),
    ('sv.xliff', '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="sv"><file id="f"><unit id="a"><segment><source>Save %s</source><target>spara</target></segment></unit><unit id="b"><segment><source>Open</source><target>Öppna</target></segment></unit></file></xliff>'),
    ('sv.json', '[{"source":"Save %s","target":"spara"},{"source":"Open","target":"Öppna"}]'),
])
def test_file_diagnostics_map_to_exact_string_in_every_format(quality, name, data):
    catalog = Catalog(name, data.encode())
    issues = quality.catalog(catalog)
    index = ReviewIndex()
    index.apply(catalog, catalog, issues)
    groups = index.groups(catalog.units[0])
    assert {'case', 'placeholders', 'errors', 'warnings'} <= groups
    assert matches(catalog.units[0], 'quality-case', quality_groups=groups)
    assert not index.groups(catalog.units[1])
    assert index.counts(catalog)['checked'] == 2
    assert index.counts(catalog)['case'] == 1
    assert all(i.key == catalog.units[0].key for i in issues if i.rule in {'inconsistent-capitalization', 'placeholder-mismatch'})


def test_spelling_counts_strings_once_across_engines_and_forms(quality, monkeypatch):
    catalog = Catalog('sv.po', b'msgid "Careful"\nmsgid_plural "Careful people"\nmsgstr[0] "nogrann"\nmsgstr[1] "nogrann"\n')
    monkeypatch.setattr(quality, 'spelling', lambda *_: [Issue('warning', engine, 'spelling', 'Kontrollera stavningen: nogrann') for engine in ('Hunspell', 'Aspell')])
    issues = quality.catalog(catalog)
    index = ReviewIndex()
    index.apply(catalog, catalog, issues)
    assert len([i for i in issues if i.rule == 'spelling']) == 4
    assert index.counts(catalog)['spelling'] == 1
    assert index.counts(catalog)['case'] == 1


def test_duplicate_source_diagnostics_do_not_leak_between_contexts(quality):
    catalog = Catalog('sv.json', json.dumps([{'source':'Save', 'target':'spara', 'context':'A'}, {'source':'Save', 'target':'Spara', 'context':'B'}]).encode())
    index = ReviewIndex()
    index.apply(catalog, catalog, quality.catalog(catalog))
    assert 'case' in index.groups(catalog.units[0])
    assert 'case' not in index.groups(catalog.units[1])


def test_stale_results_cannot_reintroduce_fixed_errors(quality):
    catalog = Catalog('sv.json', b'[{"source":"Save","target":"spara"}]')
    snapshot = copy.deepcopy(catalog)
    old_issues = quality.catalog(snapshot)
    index = ReviewIndex()
    index.apply(catalog, snapshot, old_issues)
    unit = catalog.units[0]
    unit.edit(0, 'Spara')
    assert not index.groups(unit) and not index.known(unit)
    index.record(unit, quality.check(unit))
    index.apply(catalog, snapshot, old_issues)
    assert index.known(unit) and 'case' not in index.groups(unit)
    unit.source = 'save'  # A reference update can bypass Unit.edit().
    assert not index.known(unit)


def test_unchecked_and_unavailable_are_not_reported_as_clean(quality):
    catalog = Catalog('sv.json', b'[{"source":"Save","target":"Spara"}]')
    index = ReviewIndex()
    assert index.counts(catalog)['checked'] == 0
    assert '0 kvalitetskontrollerade' in index.summary(catalog)
    unavailable = Issue('info', 'Hunspell', 'unavailable', 'Hunspell är inte installerat.')
    assert not categories([unavailable])
    index.apply(catalog, catalog, [unavailable])
    assert 'ofullständig: Hunspell' in index.summary(catalog)


def test_error_categories_and_file_level_issues_are_distinct(quality):
    unit = Unit('a', 'Save', ['Spara'])
    base = Issue('warning', 'l10n-lint', 'double-spaces', 'Blanksteg')
    assert categories([base]) == {'issues', 'warnings', 'whitespace'}
    assert 'markup' in categories([replace(base, rule='inline-codes')])
    assert 'numbers' in categories([replace(base, rule='numeric-mismatch')])
    assert not matches(unit, 'quality-spelling')


def test_auto_review_import_preference_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('ORDVERK_HOME', str(tmp_path))
    assert Settings().auto_review_imports
    settings = Settings(auto_review_imports=False)
    settings.save()
    assert not Settings.load().auto_review_imports


def test_stale_file_wide_findings_are_not_shown_after_edits(quality):
    catalog = Catalog('sv.json', b'[{"source":"Save","target":"spara"}]')
    snapshot = copy.deepcopy(catalog)
    finding = Issue('warning', 'l10n-lint', 'consistency', 'Olika översättningar')
    index = ReviewIndex()
    index.apply(catalog, snapshot, [finding])
    assert index.file_issues
    catalog.units[0].edit(0, 'Spara')
    index.record(catalog.units[0], quality.check(catalog.units[0]))
    assert not index.file_issues
    index.apply(catalog, snapshot, [finding])
    assert not index.file_issues and 'ändrades' in index.state
