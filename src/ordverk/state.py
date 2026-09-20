"""Portable formats stay untouched; JSON's local review metadata lives in the cache."""
import json
from pathlib import Path

from .catalog import atomic_write, digest
from .settings import data_dir


def state_path(catalog):
    return data_dir() / "catalogs" / (digest(str(catalog.path.resolve()).encode()) + ".json")


def save_state(catalog):
    if catalog.ext != ".json" or not catalog.path:
        return
    path = state_path(catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    reference = Path(catalog.reference) if catalog.reference else None
    data = {"fingerprint": catalog.fingerprint, "reference": str(reference) if reference else None,
            "reference_hash": digest(reference.read_bytes()) if reference and reference.exists() else None,
            "units": {u.key: {"source": u.source, "source_is_key": u.source_is_key,
                               "reviewed": u.reviewed, "targets": u.targets} for u in catalog.units}}
    atomic_write(path, json.dumps(data, ensure_ascii=False).encode())


def restore_state(catalog):
    if catalog.ext != ".json" or not catalog.path or catalog.reference:
        return
    try:
        data = json.loads(state_path(catalog).read_text())
        if data["fingerprint"] != catalog.fingerprint:
            return
        reference = data.get("reference")
        if reference and (not Path(reference).exists() or digest(Path(reference).read_bytes()) != data.get("reference_hash")):
            if Path(reference).exists():
                catalog.attach_reference(reference)
            return
        for unit in catalog.units:
            saved = data["units"].get(unit.key)
            if saved and saved["targets"] == unit.targets:
                unit.source, unit.source_is_key = saved["source"], saved["source_is_key"]
                unit.reviewed = bool(saved["reviewed"] and all(unit.targets))
                unit.checkpoint()
        catalog.reference = reference
    except (OSError, ValueError, KeyError, TypeError):
        return  # A damaged optional cache must not prevent opening the translation.
