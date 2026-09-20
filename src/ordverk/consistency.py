"""Read-back validation shared by file saving and local exports."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from lxml import etree as ET

from .catalog import Catalog


def parse_checked(name, data, *, reference=None):
    result = Catalog(name, data, reference=reference)
    if result.ext in {".po", ".pot"}:
        executable = shutil.which("msgfmt")
        if not executable:
            raise ValueError("Installera paketet gettext för att kontrollera PO-filens konsistens.")
        with tempfile.TemporaryDirectory(prefix="ordverk-check-") as directory:
            path = Path(directory) / "translation.po"
            path.write_bytes(data)
            try:
                check = subprocess.run([executable, "--check-format", "-o", "/dev/null", str(path)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Konsistenskontrollen tog för lång tid. Filen har inte godkänts.") from exc
            if check.returncode:
                raise ValueError("PO-filen klarade inte Gettexts konsistenskontroll. Kontrollera PO-syntax, platshållare och pluralformer.")
    return result


def target_value(unit, variant):
    text = unit.targets[variant]
    if not text or not unit.codecs:
        return text
    codec = unit.codecs[variant]
    wrapper = ET.Element("translation", nsmap=codec.nsmap)
    codec.write(wrapper, text)
    return ET.tostring(wrapper, method="c14n")


def check_roundtrip(original, parsed):
    if len(original.units) != len(parsed.units):
        raise ValueError("Konsistenskontrollen hittade ett ändrat antal strängar.")
    for before, after in zip(original.units, parsed.units):
        if len(before.targets) != len(after.targets) or any(
            target_value(before, variant) != target_value(after, variant) for variant in range(len(before.targets))
        ):
            raise ValueError("Konsistenskontrollen hittade en ändrad översättning eller pluralform.")


def verify_written(path, expected):
    path = Path(path)
    actual = path.read_bytes()
    if actual != expected:
        raise ValueError(f"Konsistenskontrollen misslyckades för {path.name}: filen stämmer inte med det som skrevs.")
    if path.suffix.lower() in {".diff", ".patch"}:
        from .diffs import parse_diff
        if actual:
            parse_diff(actual.decode("utf-8"))
        return None
    return parse_checked(path.name, actual)
