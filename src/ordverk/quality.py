"""Diagnostics reuse l10n-lint and svlang; spelling engines remain independent advice."""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import polib

from .catalog import Unit
from .settings import Settings
from .swedish import diagnostic
from .importers import check_cancel


def spelling_text(target):
    return re.sub(r"⟦/?\d+⟧|<[^>]+>|https?://\S+|\{[^{}]*\}|%\([^)]+\)[a-z]|%\d*\$?[a-zA-Z\d]+", " ", target)


def initial_case(text):
    clean = html.unescape(spelling_text(text))
    letter = next((c for c in clean if c.lower() != c.upper()), None)
    return None if letter is None else letter.isupper() or letter.istitle()


@dataclass(frozen=True)
class Issue:
    severity: str
    tool: str
    rule: str
    message: str
    variant: int = 0
    key: str = ""


class Quality:
    def __init__(self, settings: Settings, store):
        self.settings, self.store = settings, store

    def check(self, unit: Unit, variant=0, *, spell=True, lint=True):
        from l10n_lint import L10nLinter
        from svlang.checkers.skrivregler import SkrivreglerChecker
        from svlang.checkers.svengelska import SvengelskaChecker

        source, target = unit.source_for(variant), unit.targets[variant]
        issues = []
        if unit.source_is_key:
            issues.append(Issue("info", "Ordverk", "source-reference",
                                "Källtext saknas. Koppla en käll-JSON för jämförelse och AI-översättning.", variant))
        # A synthetic single-form catalog reuses public linter entry points for every editor variant.
        po = polib.POFile()
        po.metadata = {"Language": "sv", "Plural-Forms": "nplurals=2; plural=(n != 1);",
                       "Content-Type": "text/plain; charset=UTF-8"}
        po.append(polib.POEntry(msgid=source if not unit.source_is_key else target or " ", msgstr=target,
                               msgctxt=unit.context or None, flags=[f for f in unit.flags if f != "fuzzy"]))
        disabled = {"fuzzy", "source-equals-translation"} if unit.source_is_key else {"fuzzy"}
        result = L10nLinter({"language": "sv"}, disabled_rules=disabled).lint_file("sv.po", str(po))
        for issue in result.issues if lint else []:
            if issue.rule == "inconsistent-capitalization":
                continue
            issues.append(Issue(issue.severity.value, "l10n-lint", issue.rule, diagnostic(issue), variant))
        if unit.codecs and target:
            from lxml import etree as ET
            try:
                unit.codecs[variant].write(ET.Element("target"), target)
            except ValueError as exc:
                issues.append(Issue("error", "Ordverk", "inline-codes", str(exc), variant))
        if not target:
            return issues
        if not unit.source_is_key:
            source_case, target_case = initial_case(source), initial_case(target)
            if source_case is not None and target_case is not None and source_case != target_case:
                expected = "stor" if source_case else "liten"
                issues.append(Issue("warning", "Ordverk", "inconsistent-capitalization",
                                    f"Källtexten börjar med {expected} bokstav. Översättningen börjar med annat skiftläge.", variant))
        clean = spelling_text(target)
        for issue in SkrivreglerChecker().check(clean):
            issues.append(Issue("warning", "svlang", issue.rule, f"{issue.word} → {issue.suggestion}", variant))
        for issue in SvengelskaChecker().check(clean):
            issues.append(Issue("info", "svlang", "svengelska", f"{issue.word}: överväg {issue.suggestion}", variant))
        if not unit.source_is_key:
            for term in self.store.terminology(source):
                # Do not enforce fragments: Swedish inflection and compound formation need context.
                if term.source.casefold() == source.casefold() and term.target.casefold() != target.casefold():
                    consensus = f"konsensus {term.score:.0%}" if term.score is not None else "konsensus inte angiven"
                    issues.append(Issue("info", "Terminologi", "term-alternative",
                                        f"{term.source} → {term.target} ({consensus}; bedöm sammanhanget)", variant))
        if spell:
            issues.extend(self.spelling(clean, variant))
        return issues

    def spelling(self, text, variant):
        hunspell_path, aspell_path = self.store.dictionary_paths()
        dictionary = self.settings.hunspell_dictionary or (
            str(hunspell_path) if hunspell_path.with_suffix(".dic").exists() else "sv_SE")
        aspell_dir = self.settings.aspell_directory or (str(aspell_path) if (aspell_path / "sv.rws").exists() else "")
        if self.settings.hunspell_dictionary:
            dictionary = str(Path(dictionary).expanduser().absolute())
        if aspell_dir:
            aspell_dir = str(Path(aspell_dir).expanduser().absolute())
        commands = []
        if self.settings.use_hunspell:
            commands.append(("Hunspell", ["hunspell", "-l", "-i", "UTF-8", "-d", dictionary]))
        if self.settings.use_aspell:
            command = ["aspell", "--lang=sv", "--encoding=utf-8", "list"]
            if aspell_dir:
                command[1:1] = [f"--dict-dir={aspell_dir}", f"--local-data-dir={aspell_dir}"]
            commands.append(("Aspell", command))
        issues = []
        for name, command in commands:
            if not shutil.which(command[0]):
                issues.append(Issue("info", name, "unavailable", f"{name} är inte installerat.", variant))
                continue
            try:
                result = subprocess.run(command, input=text, encoding="utf-8", capture_output=True, timeout=8)
                if result.returncode:
                    issues.append(Issue("info", name, "dictionary-unavailable", f"{name}: svensk ordlista saknas eller kunde inte läsas.", variant))
                    continue
                unknown = list(dict.fromkeys(result.stdout.splitlines()))
                for word in unknown:
                    issues.append(Issue("warning", name, "spelling", f"Kontrollera stavningen: {word}", variant))
            except (OSError, subprocess.TimeoutExpired):
                issues.append(Issue("info", name, "unavailable", f"{name} svarade inte.", variant))
        return issues

    def catalog(self, catalog, *, cancel=None, progress=lambda message, current=None, total=None: None):
        from l10n_lint import L10nLinter
        check_cancel(cancel)
        data = catalog.render()
        # l10n-lint expects decoded input even for legacy PO/XML encodings.
        if catalog.ext in {".po", ".pot"}:
            text = data.decode(catalog.po.encoding)
        elif catalog.ext in {".ts", ".xlf", ".xliff"}:
            text = data.decode(catalog.tree.docinfo.encoding or "utf-8")
        else:
            text = data.decode("utf-8-sig")
        filename = catalog.name if catalog.ext != ".pot" else "sv.po"
        result = L10nLinter({"language": "sv"}).lint_file(filename, text)
        file_issues = [(i.rule, diagnostic(i), Issue(i.severity.value, "l10n-lint", i.rule,
                      f"Rad {i.line}: {diagnostic(i)}" + (f" · {i.context}" if i.context else ""))) for i in result.issues]
        issues, local_diagnostics = [], set()
        variants = [(u, v) for u in catalog.units for v in range(len(u.targets))]
        unavailable = set()
        # Load both dictionaries once per batch, while keeping diagnostics tied to a string.
        for start in range(0, len(variants), 50):
            check_cancel(cancel)
            batch = variants[start:start + 50]
            texts = [spelling_text(u.targets[v]) for u, v in batch]
            spelling = self.spelling("\n".join(texts), 0) if any(texts) else []
            for offset, ((unit, variant), clean) in enumerate(zip(batch, texts)):
                check_cancel(cancel)
                progress(f"Granskar {catalog.name}: {start + offset + 1}/{len(variants)}", start + offset, len(variants))
                local = self.check(unit, variant, spell=False)
                local_diagnostics.update((i.rule, i.message) for i in local if i.tool == "l10n-lint")
                for issue in spelling:
                    if issue.rule == "spelling":
                        word = issue.message.removeprefix("Kontrollera stavningen: ")
                        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", clean):
                            local.append(issue)
                    elif (issue.tool, issue.rule) not in unavailable:
                        issues.append(issue)
                        unavailable.add((issue.tool, issue.rule))
                issues.extend(replace(i, variant=variant, key=unit.key,
                                      message=f"{unit.source_for(variant)[:80]} · {unit.variants[variant]}: {i.message}") for i in local)
        # Per-string diagnostics carry exact keys even for duplicate texts and JSON.
        # Keep additional file-level rules (e.g. plural headers) in the report.
        issues.extend(issue for rule, message, issue in file_issues
                      if rule != "inconsistent-capitalization" and (rule, message) not in local_diagnostics)
        progress(f"Granskning klar: {catalog.name}", len(variants), len(variants))
        return issues
