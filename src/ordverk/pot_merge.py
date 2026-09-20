"""Update an open PO work copy with GNU gettext's POT merge semantics."""
import copy
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
from time import monotonic

from .catalog import Catalog, digest
from .consistency import parse_checked
from .importers import check_cancel
from .po_header import stamp_generator


@dataclass
class MergeProposal:
    original: Catalog
    candidate: Catalog
    expected: str
    added: int
    removed: int
    fuzzy: int
    retained: int
    template_date: str

    def apply(self):
        if digest(self.original.render()) != self.expected:
            raise ValueError("PO-filen har ändrats sedan förhandsgranskningen. Läs in POT-filen på nytt.")
        self.original.__dict__.update(self.candidate.__dict__)


def prepare_merge(catalog, template_name, template_data, *, cancel=None, fuzzy=True):
    check_cancel(cancel)
    if catalog.ext != ".po":
        raise ValueError("Välj en redan inläst PO-fil som ska uppdateras.")
    if Path(template_name).suffix.lower() != ".pot":
        raise ValueError("Välj en POT-fil med de nya källtexterna.")
    if catalog.language and not catalog.language.lower().startswith("sv"):
        raise ValueError("Skapa en svensk PO-arbetskopia innan du uppdaterar från en POT-fil.")
    template = Catalog(template_name, template_data)
    executable = shutil.which("msgmerge")
    if not executable:
        raise ValueError("Installera paketet gettext för att uppdatera PO-filer från POT.")
    before = catalog.render()
    working = copy.deepcopy(catalog)
    stamp_generator(working)
    with tempfile.TemporaryDirectory(prefix="ordverk-merge-") as directory:
        directory = Path(directory)
        existing, new = directory / "existing.po", directory / "template.pot"
        existing.write_bytes(working.render())
        new.write_bytes(template_data)
        command = [executable, "--quiet", "--previous", "--no-wrap", "--lang=sv"]
        if not fuzzy:
            command.append("--no-fuzzy-matching")
        command.extend([str(existing), str(new)])
        started = monotonic()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                while True:
                    check_cancel(cancel)
                    if monotonic() - started > 120:
                        raise ValueError("Sammanfogningen tog för lång tid. Prova utan luddig matchning.")
                    try:
                        data, _errors = process.communicate(timeout=.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            if process.returncode:
                raise ValueError("POT-filen kunde inte sammanfogas. Kontrollera PO- och POT-filernas syntax och teckenkodning.")
    check_cancel(cancel)
    candidate = parse_checked(catalog.name, data)
    candidate.path, candidate.origin = catalog.path, catalog.origin
    candidate.fingerprint = catalog.fingerprint
    candidate.import_raw, candidate.import_name = catalog.import_raw, catalog.import_name
    candidate.import_encoding = catalog.import_encoding
    template_date = template.po.metadata.get("POT-Creation-Date", "")
    if template_date:
        candidate.po.metadata["POT-Creation-Date"] = template_date
    elif "POT-Creation-Date" in catalog.po.metadata:
        candidate.po.metadata["POT-Creation-Date"] = catalog.po.metadata["POT-Creation-Date"]
    stamp_generator(candidate)
    candidate._pending_structure = True
    def identity(unit):
        return unit.source, unit.context, unit.source_plural
    previous = {identity(unit): unit for unit in catalog.units}
    current = {identity(unit) for unit in candidate.units}
    for unit in candidate.units:
        old = previous.get(identity(unit))
        unit.imported = old.imported if old else None
    # Verify the final header as well, after replacing the template date.
    parse_checked(candidate.name, candidate.render())
    return MergeProposal(catalog, candidate, digest(before),
                         sum(identity(unit) not in previous for unit in candidate.units),
                         sum(identity(unit) not in current for unit in catalog.units),
                         sum("fuzzy" in unit.flags for unit in candidate.units),
                         sum(identity(unit) in previous for unit in candidate.units), template_date)
