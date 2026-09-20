"""PO header editing and optional translator attribution on save."""
from datetime import datetime
import re
from . import __version__


def translator_identity(name, email):
    name, email = name.strip(), email.strip()
    if any(character in name + email for character in "\n\r\x00"):
        raise ValueError("Namn och e-postadress får inte innehålla radbrytningar.")
    if email and not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email):
        raise ValueError("Ange en giltig e-postadress, till exempel namn@exempel.se.")
    return f"{name} <{email}>" if name and email else name or email


def update_header(catalog, metadata, comment):
    if catalog.ext not in {".po", ".pot"}:
        raise ValueError("Huvudredigeraren används för PO- och POT-filer.")
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip() or ":" in key or any(c in key for c in "\r\n\x00"):
            raise ValueError("Ett huvudfält har ett ogiltigt namn.")
        if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
            raise ValueError(f"Huvudfältet {key} får inte innehålla radbrytningar.")
    if "\x00" in comment:
        raise ValueError("Huvudkommentaren innehåller ett ogiltigt kontrolltecken.")
    if catalog.po.metadata != metadata or catalog.po.header != comment:
        catalog.po.metadata = dict(metadata)
        catalog.po.header = comment
        catalog.language = metadata.get("Language", "")
        catalog._pending_structure = True


def stamp_generator(catalog):
    if catalog.ext not in {".po", ".pot"}:
        return
    metadata = dict(catalog.po.metadata)
    metadata["X-Generator"] = f"Ordverk {__version__}"
    metadata.setdefault("Content-Type", f"text/plain; charset={catalog.po.encoding}")
    metadata.setdefault("MIME-Version", "1.0")
    metadata.setdefault("Content-Transfer-Encoding", "8bit")
    update_header(catalog, metadata, catalog.po.header)


def stamp_translator(catalog, settings):
    stamp_generator(catalog)
    if catalog.ext not in {".po", ".pot"} or not settings.update_po_header:
        return
    identity = translator_identity(settings.translator_name, settings.translator_email)
    metadata = dict(catalog.po.metadata)
    if identity:
        metadata["Last-Translator"] = identity
    metadata["PO-Revision-Date"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M%z")
    update_header(catalog, metadata, catalog.po.header)
