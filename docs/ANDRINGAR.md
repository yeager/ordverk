# Ändringslogg

## 0.4 — 20 september 2026

- Importstatistik i en popup som uppdateras med bakgrundsgranskningens resultat.
- Strängfilter för stavfel, skiftläge och andra kvalitetsanmärkningar, även som
  urval för föröversättning. Rättade strängar lämnar respektive felurval.
- Skiftlägeskontroll i båda riktningarna med stöd för inledande inlinekoder,
  taggar och platshållare. Fil- och strängdiagnostik hålls isär.

## 0.3 — 20 september 2026

- AI-förval för OpenAI, Anthropic, X/Grok och DeepL samt fortsatt stöd för valfri
  OpenAI-kompatibel API-adress. Automatisk kontext från fil, kommentarer och
  närliggande strängar kan kompletteras manuellt och förhandsgranskas.
- Stabila strängnummer och fler urval för filtrering och föröversättning,
  inklusive alla strängar, ändrade sedan import, osparade och nya strängar.
- Export till PO, Qt TS, XLIFF 1.2/2, JSON och diff mot den importerade filen.
  Exporterade filer läses tillbaka och kontrolleras; befintliga filer skyddas.
- Redigering av PO-huvud och uppdatering av öppnad PO-fil från en POT-fil,
  med förhandsgranskning och valfri luddig matchning. POT-Creation-Date följer
  den nya mallen. GNU Gettext ingår som paketberoende.
- Översättarens namn och e-postadress sparas i inställningarna under `.ordverk`.
  PO-sparning uppdaterar Last-Translator, PO-Revision-Date och X-Generator.
  Konsistenskontroll utförs vid varje sparning och export.
- Varning vid avslut med osparade ändringar, med möjlighet att spara alla filer.
  Avbruten sparning eller filkonflikt lämnar appen öppen.
- Svenska GTK-standardtexter och Om-dialog, med tillskrivning till Daniel
  Nylander och språkverktygen.
- Förbättrad köhantering vid snabba strängbyten, förloppsuppdateringar och
  avbrutna batchjobb. Gamla AI-förslag kan inte skriva över senare manuella
  ändringar.

181 automatiserade tester samt GTK-, installations- och paketeringskontroller.
[Stresstestet](STRESSTEST.md) omfattar 100 000 JSON-strängar och 5 000 strängar
vardera i PO, TS och båda XLIFF-varianterna. API-anrop har testats mot lokala
testservrar; betalda tjänster och uppladdning till verkliga projekt ingår inte
i verifieringen.

Vid byte av exportformat blir varje plural- eller längdvariant en separat
post. Välj originalformat för att behålla formatets struktur.

## 0.2 — 20 september 2026

Första releasen: svensk GTK-app med manuell översättning, språkresurser,
föröversättning, fil-/mapp-/URL-import, diffgranskning, fjärranslutningar och
TP-anpassade e-postutkast. Linux-CI, Gitleaks samt DEB- och RPM-paket.
