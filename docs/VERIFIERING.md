# Verifiering av Ordverk 0.3

Verifierat den 20 september 2026. Första releasen är 0.2.

## Programmet

- 181 automatiserade tester täcker PO/TS/XLIFF/JSON, XML-koder, pluralformer,
  säkra sparningar, externa ändringar, import, diff, granskningsstatus,
  föröversättning, API-kontrakt, resurser, statistik och förloppsmätare.
- GTK-testet öppnar riktiga fönster under Xvfb och provar import, redigering,
  sparning, granskning, statistik, förlopp, diffredigering, markerade strängar,
  inställningar, fyra tjänsteprofiler och TP:s e-postförval. Det provar också
  samtliga AI-förval, kontextförhandsvisning, stabila strängnummer vid filtrering
  och att ett gammalt AI-förslag inte kan ersätta en senare manuell ändring.
  PO-huvud, POT-uppdatering, export och avslut med flera osparade filer ingår.
  Avbruten sparning och extern filkonflikt får inte stänga appen.
- Export testas mellan samtliga fem formatvarianter, inklusive alla pluralformer,
  diff efter sparning och skydd mot filnamnskollisioner. XLIFF-exporter har även
  validerats mot OASIS officiella scheman för 1.2 och 2.0.
- POT-sammanfogning provas med riktig `msgmerge`, både med och utan luddig
  matchning. Datum, tidigare översättare, referenser och föråldrade poster
  kontrolleras. `X-Generator` följer Ordverks versionsnummer.
- Kontroller efter skrivning provas med avsiktligt skadad export, skadad sparning
  och en simulerad serialisering som tappar en sträng. Felen stoppas och appen
  får inte markera skadad sparning som klar. Inlinekoder kan byta ordning utan
  att den semantiska konsistenskontrollen ger ett falskt fel.
- Språktester startar med C- och engelska miljöinställningar och kontrollerar
  svenska GTK-texter, bland annat Sök, Ingen, Detaljer och Juridisk information.
  Om-dialogens tillskrivning till Daniel Nylander och de sex språkverktygen ingår.
  En separat kontroll bygger och återanvänder svensk språkinställning i en privat
  cache när systemets genererade språkinställningar saknas.
- Ruff och desktop-file-validate passerar. AppStream-validering passerar.
- Installerad source-version och `.desktop`-startprogram har verifierats lokalt.
  Källkod, PNG-ikon, `.desktop`, AppStream och manualsida ingår i paketen.
- [Stresstestet](STRESSTEST.md) omfattar 100 000 JSON-strängar och 5 000 vardera
  i PO, TS, XLIFF 1.2 och XLIFF 2. Ingen dataförlust eller ohanterat programfel
  observerades. Köhantering för minnessökningar och förloppsuppdateringar samt
  förberedelse av avbrutna och begränsade batchjobb förbättrades efter mätning.

## Språkresurser

Fullständig faktisk hämtning och indexering finns i `~/.ordverk`:
**790 331 minnesposter** från alla 15 ekosystem och **325 936 termpar**.
Både Hunspell och Aspell använder användarens cache och accepterar `filnamn`
men rapporterar `nogrann`. Uppdatering av båda ordlistorna passerade utan fel.
Färdiga ordlistegenerationer aktiveras atomiskt; ett test bryter uppdateringen
mellan `.aff` och `.dic` och verifierar att föregående par fortfarande används.

## Paketering och CI

GitHub Actions har separata jobb för Linux/GTK, Gitleaks, Debian och Fedora.
Aktuella resultat finns i [CI-historiken](https://github.com/yeager/ordverk/actions/workflows/ci.yml).
Releasekontrollen kräver en grön körning för den slutliga release-revisionen.
Releasens `release-manifest.json` anger revision, CI-körning och byggresultat.

Debian-bygget använder debhelper, pybuild och dh-python, kontrolleras med
Lintian och installeras med apt. Fedora använder pyproject-rpm-macros,
kontrolleras med rpmlint och installeras med dnf. Båda installationerna
startar det installerade GTK-paketet och hittar programikonen utan import
från källkodsmappen. RPM-kontrollen rapporterade **0 fel och 0 varningar**.
Lintian passerade utan fel eller varningar. Signaturkontrollen för RPM är
undantagen eftersom upstream-paketen inte GPG-signeras; SHA-256 följer releasen.
Debians initial-upload-varning är uttryckligen undantagen eftersom dessa är
upstream-paket, utan ett Debian-ITP-ärende.

`l10n-lint` och `svlang` byggs som separata beroendepaket, från fastställda
Git-revisioner med verifierade arkivhashar. Native installation kör inte pip
eller nätverkshämtning av kod. Källpaket och bygginformation ingår.

## API- och leveranskontroller

En lokal HTTP-server verifierar autentisering, request-metoder, sökvägar,
JSON- och multipart-innehåll för GitHub, Weblate, Transifex och Crowdin.
Testerna täcker GitHubs SHA-villkor, skydd mot ändrad Weblate-fil, Transifex
asynkrona status/redirect och Crowdin storage/translation-import. Nycklar följer
inte signerade nedladdningsadresser. E-posttestet tolkar MIME-utkastet och
jämför bilagan byte för byte samt kontrollerar TP-adress och ämnesrad.

AI-stödet provas mot lokala HTTP-servrar för OpenAI-kompatibla tjänster,
Anthropic och DeepL, inklusive automatisk/manuell kontext, ogiltiga och
ofullständiga svar, skyddade platshållare, avbrytning samt HTTP 429 och 456.
Tester har inte skickat översättningar
till verkliga externa projekt, skickat e-post eller anropat ett betalt LLM-API.
Det kräver att användaren konfigurerar sin tjänst och väljer att skicka.

## Avgränsningar

- Diff-import hanterar unified textdiffar mot öppnade grundfiler, inte binära
  patchar eller skapande/borttagning av hela filer.
- Sparning bevarar filformatet. Vid byte av exportformat blir varje plural- eller
  längdvariant en separat post; välj originalformat för att behålla strukturen.
- JSON:s lokala granskningsstatus och källreferenser följer inte med filen till
  andra program, eftersom JSON saknar standard för detta.
- E-post lämnas som utkast till e-postprogrammet för slutlig sändning.
- Weblate, Transifex och Crowdin saknar ett atomiskt filvillkor motsvarande
  GitHubs SHA. Ordverk jämför fjärrfilen igen före uppladdning.
