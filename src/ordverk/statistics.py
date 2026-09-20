"""Counts are derived from the current work, including unsaved edits and every plural variant."""
from dataclasses import dataclass


@dataclass
class Statistics:
    files: int = 0
    total: int = 0
    translated: int = 0
    reviewed: int = 0
    variants: int = 0
    translated_variants: int = 0
    source_words: int = 0

    @property
    def remaining(self):
        return self.total - self.translated

    @property
    def needs_review(self):
        return self.translated - self.reviewed

    @property
    def fraction(self):
        return self.translated / self.total if self.total else 0.0


def statistics(catalogs):
    result = Statistics(files=len(catalogs))
    for catalog in catalogs:
        for unit in catalog.units:
            result.total += 1
            complete = all(bool(value) for value in unit.targets)
            result.translated += complete
            result.reviewed += complete and unit.reviewed
            result.variants += len(unit.targets)
            result.translated_variants += sum(bool(value) for value in unit.targets)
            result.source_words += len(unit.source.split()) if not unit.source_is_key else 0
    return result
