"""Swedish-only presentation of upstream diagnostic templates; rule IDs stay machine-readable."""
import re
from string import Formatter

TRANSLATIONS = {
    "Translation has trailing whitespace": "Översättningen har avslutande blanksteg.",
    "Source ends with space but translation does not": "Källtexten slutar med ett blanksteg som saknas i översättningen.",
    "Source starts with space but translation does not": "Källtexten börjar med ett blanksteg som saknas i översättningen.",
    "Translation contains double spaces between words": "Översättningen innehåller dubbla blanksteg mellan ord.",
    "Translation has mixed quote styles": "Översättningen blandar olika typer av citattecken.",
    "Keyboard accelerator missing in translation": "Snabbtangent saknas i översättningen.",
    "Translation is identical to source (possibly untranslated)": "Översättningen är identisk med källtexten och kan vara oöversatt.",
    "Swedish menu: 'File' menu must be 'Arkiv', not 'Fil'": "I en meny bör ”File” översättas med ”Arkiv”.",
    "Swedish terminology: prefer 'rad' over 'linje' in CLI/programming context": "Använd ”rad” för en rad i kod eller kommandoradstext.",
    "Swedish: 'y/n' should be 'j/n' (ja/nej)": "Använd ”j/n” (ja/nej) för svenska svarsalternativ.",
    "Source ends with period but translation does not": "Källtexten slutar med punkt, men översättningen saknar slutpunkt.",
    "Use typographic ellipsis (…) instead of three dots (...)": "Använd typografiskt uteslutningstecken (…) i stället för tre punkter (...).",
    "Fuzzy translation needs review": "Översättningen är markerad som osäker och behöver granskas.",
    "Extra ending punctuation (source has none)": "Översättningen har ett avslutande skiljetecken som saknas i källtexten.",
    "Source starts with uppercase, translation with lowercase": "Källtexten börjar med stor bokstav och översättningen med liten.",
    "Number formatting may not be localized (e.g. 1,000 should be 1 000 in some locales)": "Kontrollera svensk talformatering, exempelvis 1 000 i stället för 1,000.",
    "Currency format may need localization": "Kontrollera svensk formatering av valutabelopp.",
    "Inconsistent translation: same source has different translations": "Samma källtext har olika översättningar. Kontrollera om sammanhanget motiverar skillnaden.",
    "Translation differs from project translation memory": "Översättningen avviker från projektets översättningsminne.",
    "Translation ends with period but source does not": "Översättningen slutar med punkt, men källtexten gör det inte.",
    "All plural forms are identical, but source forms differ": "Alla pluralformer är lika trots att källformerna skiljer sig.",
    "Unknown file format: {ext}": "Okänt filformat: {ext}",
    "Missing translation (empty msgstr)": "Översättning saknas (tomt msgstr).",
    "Unfinished/missing translation": "Översättningen är ofärdig eller saknas.",
    "Translation is very long ({length} chars, max {max})": "Översättningen är mycket lång ({length} tecken, gräns {max}).",
    "Translation is {ratio:.1f}x longer than source": "Översättningen är {ratio} gånger så lång som källtexten.",
    "Translation is suspiciously short ({ratio:.1f}x of source)": "Översättningen verkar mycket kort ({ratio} av källtextens längd).",
    "Missing ending punctuation (source ends with '{char}')": "Avslutande skiljetecken saknas; källtexten slutar med ”{char}”.",
    "HTML tags mismatch: source has {source}, translation has {trans}": "HTML-taggarna skiljer sig: källa {source}, översättning {trans}.",
    "Escape character count mismatch: {details}": "Antalet escape-tecken skiljer sig: {details}",
    "Numbers {nums} from source missing in translation": "Tal från källtexten saknas i översättningen: {nums}",
    "Translation may contain untranslated English: {words}": "Översättningen kan innehålla oöversatt engelska: {words}",
    "Repeated word: '{word}'": "Upprepat ord: ”{word}”.",
    "Decimal point «{num}» should use comma in Swedish (e.g. {fix})": "Använd svenskt decimalkomma: ”{num}” → ”{fix}”.",
    "Date format may not be localized": "Kontrollera svensk datumformatering.",
    "Newline count mismatch: source has {src}, translation has {trans}": "Antalet radbrytningar skiljer sig: källa {src}, översättning {trans}.",
    "Possible typo(s): {words}": "Möjliga stavfel: {words}",
    "Translation contains zero-width characters not in source: {chars}": "Översättningen innehåller osynliga tecken som saknas i källtexten: {chars}",
    "XML/HTML tags mismatch: source has {src}, translation has {trans}": "XML/HTML-taggarna skiljer sig: källa {src}, översättning {trans}.",
    "URLs missing in translation: {urls}": "Webbadresser saknas i översättningen: {urls}",
    "Escaped newline count mismatch: source has {src}, translation has {trans}": "Antalet escape-kodade radbrytningar skiljer sig: källa {src}, översättning {trans}.",
    "Translation is {ratio:.1f}x longer than source (max {maximum:g}x recommended)": "Översättningen är {ratio} gånger så lång som källtexten (högst {maximum} rekommenderas).",
    "Duplicate msgid (first seen at line {line})": "Dubbelt msgid; första förekomsten finns på rad {line}.",
    "Nordic character '{char}' used as keyboard accelerator": "Det nordiska tecknet ”{char}” används som snabbtangent. Kontrollera projektets tangentregler.",
    "Word '{word}' duplicated across line break": "Ordet ”{word}” upprepas över en radbrytning.",
    "Swedish: '{word}' is an anglicism, use Swedish equivalent": "Överväg en svensk motsvarighet till ”{word}”.",
    "Swedish menu: '{src}' should be '{expected}', got '{got}'": "Menyterm: ”{src}” bör vara ”{expected}”; nu står det ”{got}”.",
    "Swedish terminology: prefer '{correct}' over '{wrong}' ({context})": "Terminologiråd: ”{wrong}” → ”{correct}” ({context}).",
    "False friend: '{english}' should be '{correct}', not '{wrong}'": "Kontrollera betydelsen: ”{english}” → ”{correct}”; nu står det ”{wrong}”.",
    "Missing Plural-Forms header for {lang} (expected nplurals={n})": "Plural-Forms-huvud saknas för {lang}; förväntat nplurals={n}.",
    "Triple duplicate word: '{word}'": "Ordet ”{word}” upprepas tre gånger.",
    "Source ends with '{punct}' but translation does not": "Källtexten slutar med ”{punct}”, vilket saknas i översättningen.",
    "Could not read file: {error}": "Filen kunde inte läsas: {error}",
    "Option value '{value}' from source not found in translation": "Flaggvärdet ”{value}” från källtexten saknas i översättningen.",
    "Music domain terminology: prefer '{correct}' over '{wrong}' ({context})": "Musikterm: ”{wrong}” → ”{correct}” ({context}).",
    "Wrong nplurals={got} for {lang} (expected nplurals={expected})": "Fel antal pluralformer för {lang}: {got}; förväntat {expected}.",
    "Missing plural forms: got {got}, expected {exp} (nplurals={n})": "Pluralformer saknas: {got}; förväntat {exp} (nplurals={n}).",
    "Empty msgstr[{idx}] in plural entry": "Pluralformen msgstr[{idx}] är tom.",
    "Duplicate word: '{word}'": "Dubbelt ord: ”{word}”.",
    "Translation ends with '{punct}' but source does not": "Översättningen slutar med ”{punct}”, vilket saknas i källtexten.",
    "Web platform terminology: prefer '{correct}' over '{wrong}' ({context})": "Webbterm: ”{wrong}” → ”{correct}” ({context}).",
    "Suspicious plural formula for {lang}: got '{got}', expected '{exp}'": "Kontrollera pluralformeln för {lang}: ”{got}”; förväntat ”{exp}”.",
    "Extra plural forms: got {got}, expected {exp} (nplurals={n})": "Övertaliga pluralformer: {got}; förväntat {exp} (nplurals={n}).",
    "Source contains '{punct}' but translation does not": "Källtexten innehåller ”{punct}”, vilket saknas i översättningen.",
    "Placeholder mismatch ({kind}): source has {source}, translation has {target}": "Platshållarna skiljer sig ({kind}): källa {source}, översättning {target}.",
    "Missing CLDR plural form(s): {forms}": "CLDR-pluralformer saknas: {forms}.",
}


def compile_templates():
    result = []
    for source, target in TRANSLATIONS.items():
        parts = []
        for literal, name, _format, _conversion in Formatter().parse(source):
            parts.append(re.escape(literal))
            if name is not None:
                parts.append(f"(?P<{name}>.+?)")
        result.append((re.compile("^" + "".join(parts) + "$", re.DOTALL), target))
    return result


PATTERNS = compile_templates()


def diagnostic(issue):
    for pattern, target in PATTERNS:
        match = pattern.match(issue.message)
        if match:
            return target.format(**match.groupdict())
    # Future upstream rules remain visible without introducing an English UI.
    summaries = {
        "syntax-error": "Filen har ett syntaxfel och kunde inte tolkas fullständigt.",
        "python-format": "Python-formatet är felaktigt. Kontrollera fältnamn, klamrar och formatangivelser.",
        "placeholder-mismatch": "Platshållarnas namn, typer, ordning eller antal skiljer sig från källtexten.",
        "bidi-control": "Översättningen introducerar styrtecken för textriktning.",
        "unicode-normalization": "Texten använder en avvikande Unicode-normalisering.",
        "required-term-missing": "En term som projektet kräver saknas.",
        "forbidden-term": "Översättningen innehåller en term som projektet avråder från.",
        "glossary": "Översättningen avviker från projektets ordlista.",
    }
    return summaries.get(issue.rule, f"Kontrollen ”{issue.rule}” hittade en avvikelse. Jämför källtext och översättning.")
