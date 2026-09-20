from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .catalog import atomic_write


def data_dir():
    return Path(os.environ.get("ORDVERK_HOME", Path.home() / ".ordverk")).expanduser().absolute()


def config_dir():
    return data_dir()


@dataclass
class Settings:
    translator_name: str = ""
    translator_email: str = ""
    update_po_header: bool = True
    ai_provider: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "ORDVERK_API_KEY"
    project_context: str = ""
    ai_instructions: str = ""
    ai_auto_context: bool = True
    ai_context_neighbors: int = 2
    domain: str = "Allmänt"
    hunspell_dictionary: str = ""
    aspell_directory: str = ""
    use_hunspell: bool = True
    use_aspell: bool = True
    auto_review_imports: bool = True
    auto_update_resources: bool = True
    show_import_guide: bool = True
    import_purpose: str = "translate"
    import_automatic: bool = False
    automatic_use_ai: bool = False
    automatic_limit: int = 100
    resource_components: list[str] = None

    def __post_init__(self):
        if self.resource_components is None:
            self.resource_components = ["tm", "terms", "hunspell", "aspell"]

    @classmethod
    def load(cls):
        path = config_dir() / "settings.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})

    def save(self):
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write(directory / "settings.json", json.dumps(asdict(self), ensure_ascii=False, indent=2).encode())

    def key(self):
        value = os.environ.get(self.api_key_env, "")
        if value:
            return value
        try:
            return secure_keyring().get_password("ordverk", self.base_url) or ""
        except Exception:
            return ""


def secure_keyring():
    import keyring
    backend = keyring.get_keyring()
    # Never silently fall back to a plaintext file backend.
    module = type(backend).__module__
    if not module.startswith(("keyring.backends.SecretService", "keyring.backends.kwallet")):
        raise RuntimeError("Ingen stödd systemnyckelring. Använd sessionsnyckel eller miljövariabel.")
    return backend


def save_key(endpoint, key):
    backend = secure_keyring()
    if key:
        backend.set_password("ordverk", endpoint, key)
    else:
        try:
            backend.delete_password("ordverk", endpoint)
        except Exception:
            pass
