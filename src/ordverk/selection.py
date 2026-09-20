"""Shared string selections for the editor and pretranslation dialog."""

FILTERS = (
    ("all", "Alla strängar"),
    ("untranslated", "Oöversatta strängar"),
    ("needs-review", "Strängar att granska"),
    ("reviewed", "Granskade strängar"),
    ("translated", "Översatta strängar"),
    ("partial", "Delvis översatta strängar"),
    ("changed", "Ändrade strängar sedan import"),
    ("unchanged", "Oförändrade strängar sedan import"),
    ("unsaved", "Strängar med osparade ändringar"),
    ("new", "Nya strängar sedan import"),
    ("plural", "Strängar med flera former"),
    ("marked", "Markerade strängar"),
    ("quality-issues", "Strängar med kvalitetsanmärkningar"),
    ("quality-errors", "Strängar med fel"),
    ("quality-warnings", "Strängar med varningar"),
    ("quality-spelling", "Strängar med stavfel"),
    ("quality-case", "Strängar med fel skiftläge"),
    ("quality-placeholders", "Strängar med fel i platshållare"),
    ("quality-markup", "Strängar med fel i taggar eller inlinekoder"),
    ("quality-punctuation", "Strängar med avvikande skiljetecken"),
    ("quality-whitespace", "Strängar med blankstegs- eller radbrytningsfel"),
    ("quality-numbers", "Strängar med avvikande tal eller datum"),
    ("quality-terminology", "Strängar med terminologianmärkningar"),
)


def matches(unit, kind, *, marked=False, quality_groups=()):
    if kind.startswith("quality-"):
        return kind.removeprefix("quality-") in quality_groups
    if kind == "all":
        return True
    if kind == "untranslated":
        return not all(unit.targets)
    if kind == "needs-review":
        return all(unit.targets) and not unit.reviewed
    if kind == "reviewed":
        return all(unit.targets) and unit.reviewed
    if kind == "translated":
        return all(unit.targets)
    if kind == "partial":
        return any(unit.targets) and not all(unit.targets)
    if kind == "changed":
        return unit.changed_since_import
    if kind == "unchanged":
        return not unit.changed_since_import
    if kind == "unsaved":
        return unit.changed
    if kind == "new":
        return unit.imported is None
    if kind == "plural":
        return len(unit.targets) > 1
    if kind == "marked":
        return marked
    raise ValueError("Okänt strängurval.")
