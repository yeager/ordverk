"""Provider presets and their wire protocols; credentials remain endpoint-scoped."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    protocol: str
    base_url: str
    model: str
    key_env: str


PROVIDERS = (
    Provider("custom", "Egen OpenAI-kompatibel tjänst", "chat", "", "", "ORDVERK_API_KEY"),
    Provider("openai", "OpenAI", "chat", "https://api.openai.com/v1", "gpt-5.6-terra", "OPENAI_API_KEY"),
    Provider("anthropic", "Anthropic / Claude", "anthropic", "https://api.anthropic.com/v1",
             "claude-sonnet-5", "ANTHROPIC_API_KEY"),
    Provider("xai", "X / Grok", "chat", "https://api.x.ai/v1", "grok-4.6", "XAI_API_KEY"),
    Provider("deepl-free", "DeepL API Free", "deepl", "https://api-free.deepl.com/v2", "", "DEEPL_API_KEY"),
    Provider("deepl-pro", "DeepL API Pro", "deepl", "https://api.deepl.com/v2", "", "DEEPL_API_KEY"),
)


def provider_for(settings):
    return next((p for p in PROVIDERS if p.id == settings.ai_provider), PROVIDERS[0])


def is_configured(settings):
    return bool(settings.base_url.strip() and (provider_for(settings).protocol == "deepl" or settings.model.strip()))
