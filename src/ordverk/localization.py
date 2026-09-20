"""Select Swedish before PyGObject initializes GTK and its gettext domains."""
from __future__ import annotations

import locale
import os
from pathlib import Path
import subprocess
import tempfile


SWEDISH = "sv_SE.UTF-8"


def configure_language():
    # GTK calls setlocale(LC_ALL, "") during import. Setting only the C library's
    # LC_MESSAGES beforehand is therefore insufficient, especially with LC_ALL=C.
    os.environ["LC_ALL"] = SWEDISH
    os.environ["LANGUAGE"] = "sv"
    os.environ["LC_MESSAGES"] = SWEDISH
    try:
        locale.setlocale(locale.LC_ALL, SWEDISH)
    except locale.Error:
        _prepare_local_locale()
        locale.setlocale(locale.LC_ALL, SWEDISH)


def _prepare_local_locale():
    # Minimal Debian installations ship locale sources without generating sv_SE.
    # Build a private cache from installed glibc data; never change system locales.
    cache = Path(os.environ.get("ORDVERK_HOME", Path.home() / ".ordverk")).expanduser().absolute()
    version = os.confstr("CS_GNU_LIBC_VERSION").replace(" ", "-")
    root = cache / "locale" / version
    target = root / SWEDISH
    root.mkdir(parents=True, exist_ok=True)
    try:
        if not target.is_dir():
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                built = Path(temporary) / SWEDISH
                subprocess.run(
                    ["localedef", "--no-archive", "--quiet", "-i", "sv_SE", "-f", "UTF-8", str(built)],
                    check=True, capture_output=True, timeout=7,
                    env={**os.environ, "LC_ALL": "C", "LANGUAGE": "C"},
                )
                try:
                    built.rename(target)
                except OSError:
                    if not target.is_dir():
                        raise
        # Expose only completed data. glibc caches failed lookups as well.
        os.environ["LOCPATH"] = os.pathsep.join(filter(None, [str(root), os.environ.get("LOCPATH")]))
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Svenska språkdata saknas. Installera locales på Debian/Ubuntu "
            "eller glibc-langpack-sv på Fedora och starta Ordverk igen."
        ) from exc
