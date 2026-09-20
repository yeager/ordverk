"""Protect localization syntax with DeepL's XML ignore_tags mechanism."""
from collections import Counter
import re
from xml.sax.saxutils import escape
from lxml import etree as ET

from .catalog import parse_xml


TOKENS = re.compile(
    r"⟦/?\d+⟧|<[^>]+>|https?://[^\s<>]+|\$?\{[^{}]*\}|"
    r"%(?:\([^)]+\))?(?:\d+\$)?[-+#0 ]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[a-zA-Z%]|%\d+"
)


def protect(source):
    parts, tokens, start = [], [], 0
    for match in TOKENS.finditer(source):
        parts.extend([escape(source[start:match.start()]),
                      f'<ph id="{len(tokens)}">{escape(match.group())}</ph>'])
        tokens.append(match.group())
        start = match.end()
    parts.append(escape(source[start:]))
    return "<text>" + "".join(parts) + "</text>", tokens


def restore(translated, tokens):
    try:
        root = parse_xml(translated.encode()).getroot()
        if root.tag != "text":
            raise ValueError
        seen = []
        for child in root:
            index = int(child.get("id", ""))
            if child.tag != "ph" or len(child) or not 0 <= index < len(tokens) or child.text != tokens[index]:
                raise ValueError
            seen.append(index)
        if Counter(seen) != Counter(range(len(tokens))):
            raise ValueError
        return "".join(root.itertext())
    except (ValueError, TypeError, AttributeError, ET.XMLSyntaxError) as exc:
        raise ValueError("DeepL ändrade skyddade platshållare eller inlinekoder. Förslaget har stoppats.") from exc
