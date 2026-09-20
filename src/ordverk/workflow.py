"""Batch work returns proposals against snapshots; only the UI thread applies changes."""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .importers import Cancelled, check_cancel


@dataclass(frozen=True)
class Change:
    catalog: object
    key: str
    revision: int
    variant: int
    before: str
    after: str
    origin: str
    issues: list


def batch_translate(catalogs, store, quality, translator=None, *, limit=100, cancel=None,
                    selected=None, overwrite=False, method="combined",
                    progress=lambda message, current=None, total=None: None):
    if method not in {"resources", "ai", "combined"}:
        raise ValueError("Välj språkresurser, AI eller språkresurser följt av AI.")
    if method == "ai" and translator is None:
        raise ValueError("Konfigurera en AI-anslutning först.")
    # Capture all inputs once, before the worker begins making requests.
    work = [(catalog, copy.deepcopy(unit)) for catalog in catalogs for unit in catalog.units
            if selected is None or (id(catalog), unit.key) in selected]
    changes, messages = [], []
    attempted = 0
    eligible = sum(overwrite or not target for _, unit in work if not unit.source_is_key for target in unit.targets)
    limit = eligible if limit is None else limit
    total = min(limit, eligible)
    try:
        for catalog, unit in work:
                for variant, target in enumerate(unit.targets):
                    check_cancel(cancel)
                    if (target and not overwrite) or unit.source_is_key:
                        continue
                    if attempted >= limit:
                        messages.append(f"Gränsen {limit} strängar nåddes. Starta igen för att fortsätta.")
                        return changes, messages
                    attempted += 1
                    progress(f"Översätter {attempted}/{total}: {unit.source_for(variant)[:65]}", attempted - 1, total)
                    exact = store.exact_translation(unit.source_for(variant), unit.context) if method != "ai" else None
                    proposal, origin, issues = None, "", []
                    # Conflicting exact matches need context: don't let frequency silently decide.
                    if exact:
                        proposal, origin = exact
                        candidate = copy.deepcopy(unit)
                        candidate.edit(variant, proposal)
                        issues = quality.check(candidate, variant)
                    elif translator is not None and method != "resources":
                        try:
                            result = translator.suggest(unit, variant, cancel)
                            proposal, origin, issues = result.translation, f"AI · {result.model}", result.issues
                        except ValueError as exc:
                            messages.append(f"{catalog.name}: {exc}")
                            # Stop on API errors instead of repeatedly spending requests on a failing service.
                            return changes, messages
                    if proposal is not None and proposal != target:
                        if any(issue.severity == "error" for issue in issues):
                            messages.append(f"{unit.source_for(variant)[:70]}: förslag stoppat av kvalitetsfel.")
                        else:
                            changes.append(Change(catalog, unit.key, unit.revision, variant, target, proposal, origin, issues))
                    progress(f"Föröversätter {attempted}/{total}", attempted, total)
    except Cancelled:
        messages.append("Arbetet avbröts. Färdiga förslag finns kvar.")
    return changes, messages


def apply_changes(changes):
    applied, skipped = 0, 0
    # All forms share a revision, so compare against the revision at the start of application.
    catalogs = {id(change.catalog): change.catalog for change in changes}
    indexes = {identity: {unit.key: unit for unit in catalog.units} for identity, catalog in catalogs.items()}
    revisions = {(identity, unit.key): unit.revision for identity, catalog in catalogs.items() for unit in catalog.units}
    for change in changes:
        unit = indexes[id(change.catalog)].get(change.key)
        if unit is None or revisions[(id(change.catalog), change.key)] != change.revision or unit.targets[change.variant] != change.before:
            skipped += 1
            continue
        unit.edit(change.variant, change.after)
        applied += 1
    return applied, skipped
