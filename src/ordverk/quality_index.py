"""Revision-aware, in-memory diagnostics for filters and file statistics."""
from collections import Counter
from dataclasses import dataclass, field


RULE_GROUPS = {
    "spelling": {"spelling", "typo"},
    "case": {"inconsistent-capitalization"},
    "placeholders": {"placeholder-mismatch", "python-format", "option-value-missing"},
    "markup": {"inline-codes", "html-tag-mismatch", "xml-tags-mismatch"},
    "punctuation": {"inconsistent-punctuation", "punctuation-mismatch", "end-stop-mismatch", "mixed-quotes", "ellipsis"},
    "whitespace": {"trailing-whitespace", "missing-trailing-space", "missing-leading-space", "double-spaces", "newline-mismatch", "escaped-chars-mismatch", "escaped-newline-count"},
    "numbers": {"numeric-mismatch", "number-localization", "decimal-separator", "currency-localization", "date-format"},
    "terminology": {"terminology", "domain-terminology", "false-friends", "term-alternative", "required-term-missing", "forbidden-term", "glossary", "svengelska"},
}
UNAVAILABLE = {"unavailable", "dictionary-unavailable"}


def signature(unit):
    # Include fields that can change without Unit.edit(), e.g. a linked source JSON.
    return (unit.source, unit.source_plural, tuple(unit.targets), unit.context,
            unit.notes, unit.references, unit.source_is_key, tuple(unit.flags),
            tuple(unit.variants), tuple(tuple(c.tokens.items()) for c in unit.codecs))


def categories(issues):
    found = set()
    for issue in issues:
        if issue.rule in UNAVAILABLE:
            continue
        found.add("issues")
        if issue.severity in {"error", "warning"}:
            found.add(issue.severity + "s")
        for group, rules in RULE_GROUPS.items():
            if issue.rule in rules:
                found.add(group)
    return frozenset(found)


@dataclass
class ReviewIndex:
    entries: dict = field(default_factory=dict)
    state: str = "Hela filen är inte kvalitetsgranskad"
    file_issues: list = field(default_factory=list)

    def record(self, unit, issues):
        current = signature(unit)
        previous = self.entries.get(unit.key)
        if previous and previous[0] != current:
            self.file_issues = [issue for issue in self.file_issues if issue.rule in UNAVAILABLE]
        self.entries[unit.key] = (current, categories(issues))

    def groups(self, unit):
        entry = self.entries.get(unit.key)
        return entry[1] if entry and entry[0] == signature(unit) else frozenset()

    def known(self, unit):
        entry = self.entries.get(unit.key)
        return bool(entry and entry[0] == signature(unit))

    def apply(self, catalog, snapshot, issues):
        grouped = {}
        for issue in issues:
            if issue.key:
                grouped.setdefault(issue.key, []).append(issue)
        current = {unit.key: unit for unit in catalog.units}
        for unit in snapshot.units:
            live = current.get(unit.key)
            if live is not None and signature(live) == signature(unit):
                self.record(unit, grouped.get(unit.key, ()))
        complete = len(current) == len(snapshot.units) and all(
            unit.key in current and signature(current[unit.key]) == signature(unit) for unit in snapshot.units)
        self.file_issues = [issue for issue in issues if not issue.key and (complete or issue.rule in UNAVAILABLE)]
        self.state = "Kvalitetsgranskning klar" if complete else "Filens innehåll ändrades under granskningen"

    def counts(self, catalog):
        counts = Counter(total=len(catalog.units), checked=0)
        for unit in catalog.units:
            if self.known(unit):
                counts["checked"] += 1
                counts.update(self.groups(unit))
        return counts

    def summary(self, catalog):
        counts = self.counts(catalog)
        text = (f"{catalog.name} · {counts['total']} strängar · {counts['checked']} kvalitetskontrollerade"
                f" · {counts['spelling']} med stavfel · {counts['case']} med fel skiftläge"
                f" · {counts['errors']} med fel · {counts['warnings']} med varningar")
        if counts['checked'] != counts['total'] or self.state != "Kvalitetsgranskning klar":
            text += " · " + self.state
            if self.state == "Kvalitetsgranskning klar":
                text += " för tidigare innehåll; ändrade strängar behöver kontrolleras"
        unavailable = sorted({issue.tool for issue in self.file_issues if issue.rule in UNAVAILABLE})
        if unavailable:
            text += " · Stavningskontrollen är ofullständig: " + ", ".join(unavailable)
        return text
