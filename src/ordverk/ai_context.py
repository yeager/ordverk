"""Bounded file context, captured before a translation request is sent."""
from .catalog import localname


def catalog_context(catalog, unit, neighbors=2, *, index=None):
    neighbors = max(0, min(10, neighbors))
    if index is None:
        index = next((i for i, item in enumerate(catalog.units) if item is unit), None)
    if index is None:
        return {}
    surrounding = []
    for position in range(max(0, index - neighbors), min(len(catalog.units), index + neighbors + 1)):
        if position == index:
            continue
        other = catalog.units[position]
        surrounding.append({
            "position": "before" if position < index else "after",
            "source": other.source[:2000], "translation": other.targets[0][:2000],
            "context": other.context[:500], "reviewed": other.reviewed,
        })
    project, source_language = "", ""
    if hasattr(catalog, "po"):
        project = catalog.po.metadata.get("Project-Id-Version", "")[:500]
        source_language = catalog.po.metadata.get("X-Source-Language", "")[:30]
    elif hasattr(catalog, "tree"):
        root = catalog.tree.getroot()
        source_language = root.get("sourcelanguage", root.get("srcLang", ""))[:30]
        file = next((child for child in root if localname(child) == "file"), None)
        if file is not None:
            project = file.get("original", "")[:500]
            source_language = file.get("source-language", source_language)[:30]
    return {
        "file": catalog.name, "format": catalog.ext.lstrip("."),
        "project": project, "source_language": source_language,
        "target_language": catalog.language or "sv", "position": index + 1,
        "total_strings": len(catalog.units), "references": unit.references[:2000],
        "neighbors": surrounding,
    }
