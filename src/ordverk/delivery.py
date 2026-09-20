"""Email drafts contain a byte-exact attachment; final sending belongs to the mail client."""
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .catalog import Catalog, atomic_write
from .settings import data_dir

TP_ADDRESS = "robot@translationproject.org"


def tp_defaults(catalog):
    return TP_ADDRESS, Path(catalog.name).name


def prepare_mail(catalog, recipient, subject, body="", sender=""):
    for value in (recipient, subject, sender):
        if "\n" in value or "\r" in value:
            raise ValueError("Adress och ämne får inte innehålla radbrytningar.")
    address = parseaddr(recipient)[1]
    if not re.fullmatch(r"[^\s@]+@[^\s@]+", address):
        raise ValueError("Ange en giltig mottagaradress.")
    if not subject.strip():
        raise ValueError("Ange en ämnesrad.")
    data = catalog.render()
    filename = Path(catalog.name).name
    Catalog(filename, data)
    message = EmailMessage(policy=SMTP)
    message["To"], message["Subject"] = recipient, subject
    if sender:
        message["From"] = sender
    message["Date"], message["Message-ID"] = formatdate(localtime=True), make_msgid(domain="ordverk.local")
    message.set_content(body or "Svensk översättning bifogas.")
    message.add_attachment(data, maintype="application", subtype="octet-stream", filename=filename)
    directory = data_dir() / "outbox" / uuid.uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    attachment = directory / filename
    atomic_write(attachment, data)
    draft = directory / "meddelande.eml"
    atomic_write(draft, message.as_bytes())
    return draft, attachment


def open_mail(recipient, subject, body, attachment):
    if not shutil.which("xdg-email"):
        raise ValueError("xdg-email är inte installerat. Spara .eml-filen och öppna den i ditt e-postprogram.")
    result = subprocess.run(["xdg-email", "--utf8", "--subject", subject, "--body", body,
                             "--attach", str(attachment.absolute()), recipient], timeout=45, capture_output=True)
    if result.returncode:
        raise ValueError("E-postprogrammet kunde inte öppnas. Spara .eml-filen i stället.")
