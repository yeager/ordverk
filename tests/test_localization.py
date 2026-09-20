"""Language selection must survive GTK's later setlocale(LC_ALL, '')."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROBE = """
import json, locale, os
from ordverk.localization import configure_language
configure_language()
locale.setlocale(locale.LC_ALL, '')
print(json.dumps({
    'messages': locale.setlocale(locale.LC_MESSAGES),
    'numeric': locale.setlocale(locale.LC_NUMERIC),
    'language': os.environ['LANGUAGE'],
}))
"""


def probe(tmp_path, **environment):
    env = {**os.environ, "ORDVERK_HOME": str(tmp_path), "LANGUAGE": "en", **environment}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", PROBE], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("inherited", ["C", "C.UTF-8", "en_US.UTF-8"])
def test_swedish_overrides_inherited_language_and_number_formatting(tmp_path, inherited):
    result = probe(tmp_path, LC_ALL=inherited, LC_NUMERIC="C")
    assert result == {"messages": "sv_SE.UTF-8", "numeric": "sv_SE.UTF-8", "language": "sv"}


@pytest.mark.skipif(not shutil.which("localedef") or not Path("/usr/share/i18n/locales/sv_SE").exists(),
                    reason="Requires installed glibc locale sources")
def test_build_and_reuse_swedish_without_a_generated_system_locale(tmp_path):
    empty = tmp_path / "empty-locales"
    empty.mkdir()
    first = probe(tmp_path, LC_ALL="C", LOCPATH=str(empty))
    assert first["messages"] == "sv_SE.UTF-8"
    cached = list((tmp_path / "locale").glob("*/sv_SE.UTF-8/LC_MESSAGES/SYS_LC_MESSAGES"))
    assert len(cached) == 1
    # A second launch must reuse the cache, even without localedef on PATH.
    second = probe(tmp_path, LC_ALL="C", LOCPATH=str(empty), PATH=str(empty))
    assert second == first
