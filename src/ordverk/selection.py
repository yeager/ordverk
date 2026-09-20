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
)


def matches(unit, kind, *, marked=False):
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
