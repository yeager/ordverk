# Verifiering av Ordverk 0.1.0

Verifierat 20 september 2026 i Linux på ARM64 med Python 3.14, GTK 4.22 och
libadwaita 1.9.

- 44 automatiserade tester för format, sparning, import, resurser, LLM-kontrakt,
  svensk diagnostik, statistik och sju sekunders fördröjning av förloppsmätaren.
- Ett GTK-test under Xvfb för faktisk filimport, redigering, sparning,
  granskningsmarkering, statistik, förloppsmätare, inställningar och importguide.
- Statisk Python-kontroll med Ruff och validering av `.desktop`-filen.
- Byggt källpaket och wheel; wheel installerad i separat virtuell miljö.
  Installerad GTK-app och appikon har öppnats utan att importera projektets källmapp.
- Startprogram och ikoninstallation provade i en tillfällig användarkatalog.
- Fullständig faktisk resurshämtning och indexering: **790 331 minnesposter**
  från samtliga 15 ekosystem och **325 936 termpar**. Inga hämtningsfel.
- Faktisk stavningskontroll med cachade `hunspell-sv` och `aspell-sv`:
  båda accepterar `filnamn` och rapporterar `nogrann` för granskning.
- API-anrop verifierade mot en lokal HTTP-testserver, inklusive promptkontext,
  JSON-svar, ogiltiga svar och HTTP 429. Inget betalt externt API-anrop har utförts.

Gränserna för 0.1.0 beskrivs i README: JSON-granskningsstatus gäller under
sessionen, privata GitHub-källor importeras lokalt, och filgranskning använder
l10n-lint medan extra språk-/stavningsråd visas per sträng. Appen sparar
aktuella filer i samma format och erbjuder ännu inte formatkonvertering eller
ett beständigt projektformat.
