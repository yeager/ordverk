# Kravspårning för 0.2

| Användarkrav | Implementation | Verifiering |
| --- | --- | --- |
| GTK-app för Linux | GTK4/libadwaita, systempaket och körbart startprogram | GTK-test och installation i Debian/Fedora |
| Endast svenskt gränssnitt, använd ”inte” | Svensk UI-text och svenskt språkval för GTK | Visuell granskning, källtextsökning och GTK-test |
| PO, TS, XLIFF, JSON; redigera varje sträng | Formatbevarande katalogmodell, plural- och längdvarianter | test_catalog.py och GTK-test |
| Alla sex angivna språkprojekt | TM, terminologi, l10n-lint, svlang, cachad Hunspell och Aspell | Faktiska data/ordkontroller och tjänstetester |
| Valfritt OpenAI-kompatibelt API | Konfigurerbar adress, modell och säker nyckellagring | Lokal HTTP-kontraktsserver och feltester |
| Fil, mapp, URL och fler importvägar | Flera filer, rekursion, drag/släpp, GitHub-URL och tjänsteprofiler | Importtester och GTK-test |
| Senaste statiska data i .ordverk, uppdatering vid start | Git-versioner, ETag, lokalt index och separat ordlisteaktivering | Cache-, offline- och avbruten uppdateringstester; verklig cache |
| Enkla/avancerade inställningar och valfri importguide | Två inställningssidor; granska/översätta och manuellt/automatiskt | GTK-test |
| Genererad ikon, även i .desktop | Godkänd PNG, programikon och menyinstallation | Installerad ikon i två paketmiljöer och lokalt |
| Förloppsmätare efter sju sekunder | Fördröjda mätare för jobb, resurser, granskning och uppslag | Tidsgränstest och GTK-test med synlig mätare |
| Statistik | Totalt/per fil, granskade, kvar, ord, varianter och använda förslag | Statistiktester och GTK-test |
| Föröversätt fil, en eller flera strängar | Kryssmarkering, omfattningsval, resurser/AI, förhandsgranskning | Urvals-, konflikt-, AI- och samtidighetstester |
| GitHub, Weblate, Transifex, Crowdin import/export | API-profiler och export med diff/granskning | HTTP-kontrakt för samtliga fyra; GTK-profiler |
| E-post och TP-förval | robot@translationproject.org, filnamn som ämne, MIME-bilaga | MIME-parsning, exakt bilagejämförelse och GTK-test |
| Diff-import: granskad, fuzzy eller manuell redigering | Strikt diffmatchning och redigerbar förhandsgranskning | Diffroundtrip i båda statuslägen, redigering och konflikter |
| Första release 0.2, yeager/ordverk | Enhetlig version, offentligt GitHub-förråd och releasepaket | Versionskontroller, CI, release och SHA256SUMS |
| Gitleaks | Lokal skanning och separat CI-jobb för Git-historiken | Inga upptäckta hemligheter |
| Linux-CI, DEB/RPM enligt standard | debhelper/pybuild, RPM-makron, separata bibliotekspaket, standardplatser, beroenden, licenser, manualer | Lintian, rpmlint, apt/dnf-installation och start av installerat program |

API-testerna verifierar implementationens kontrakt. De innebär inte att filer
har skickats till användarens externa projekt eller att e-post har sänts.
