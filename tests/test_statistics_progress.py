from ordverk.catalog import Catalog
from ordverk.progress import ProgressState
from ordverk.statistics import statistics
from ordverk.swedish import diagnostic
from types import SimpleNamespace


def test_progress_begins_after_seven_seconds_and_supports_indeterminate():
    state = ProgressState(started=100)
    assert not state.visible(106.99)
    assert state.visible(107)
    assert state.fraction is None
    state.update(3, 10)
    assert state.fraction == 0.3
    state.update(20, 10)
    assert state.fraction == 1


def test_statistics_count_plural_variants_and_review_separately():
    catalog = Catalog("sv.po", b'msgid "Save"\nmsgstr "Spara"\n\nmsgid "File"\nmsgid_plural "Files"\nmsgstr[0] "Fil"\nmsgstr[1] ""\n')
    counts = statistics([catalog])
    assert counts.total == 2
    assert counts.translated == counts.reviewed == 1
    assert counts.remaining == 1
    assert counts.variants == 3 and counts.translated_variants == 2
    catalog.units[1].edit(1, "Filer")
    counts = statistics([catalog])
    assert counts.fraction == 1
    assert counts.needs_review == 1


def test_swedish_diagnostics_keep_placeholder_details():
    issue = SimpleNamespace(rule="placeholder-mismatch", message="Placeholder mismatch (printf): source has {'%s': 1}, translation has {}")
    message = diagnostic(issue)
    assert message.startswith("Platshållarna skiljer sig")
    assert "'%s': 1" in message
    assert diagnostic(SimpleNamespace(rule="fuzzy", message="Fuzzy translation needs review")).startswith("Översättningen")
