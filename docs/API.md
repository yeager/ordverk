# Integrationskontrakt

Ordverk använder följande primära API-dokumentation:

- [GitHub: repository contents](https://docs.github.com/en/rest/repos/contents):
  GET för import och PUT med blob-SHA för en filcommit på vald gren.
- [Weblate: REST API](https://docs.weblate.org/en/latest/api.html):
  GET/POST av översättningsfil; multipart med translate/suggest och konflikthantering.
- [Transifex API v3](https://transifex.github.io/openapi/):
  resource_translations_async_downloads och resource_translations_async_uploads;
  JSON:API-relationer, multipart, statuspollning och signerad nedladdning.
- [Crowdin: Translations API](https://crowdin.github.io/crowdin-api-client-python/api_resources/translations/resource.html):
  byggd filöversättning, storage-uppladdning och import för svenska.
- [Translation Projects robot](https://translationproject.org/html/robot.html):
  robot@translationproject.org, paket-version.sv.po som ämne, filen som bilaga.
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create):
  `/chat/completions`, Bearer-nyckel, system- och användarmeddelande, JSON-innehåll.
  Förvald modell är [gpt-5.6-terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
  Samma protokoll används för egna kompatibla tjänster; adress och modell kan ändras.
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create):
  `/messages`, `x-api-key`, `anthropic-version: 2023-06-01`, systeminstruktion
  separat från meddelandena och `max_tokens: 8192`. Förvalet är
  [claude-sonnet-5](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5).
  Endast avslutade textsvar används; avbrutna svar och nekanden stoppas.
- [X/Grok Chat Completions](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions):
  OpenAI-kompatibelt protokoll på `https://api.x.ai/v1`, med
  [grok-4.6](https://docs.x.ai/developers/grok-4-6) som förval.
- [DeepL textöversättning](https://developers.deepl.com/api-reference/translate/request-translation):
  `/v2/translate`, målspråk `SV`, automatisk identifiering av källspråket och
  ett separat `context`-fält. Free och Pro har olika basadresser och använder
  [DeepL-Auth-Key](https://developers.deepl.com/docs/getting-started/auth).
  Platshållare, URL:er och inlinekoder kapslas i XML med `ignore_tags: ["ph"]`.
  Svaret måste bevara varje skyddad markör exakt en gång. Anrop större än
  128 KiB stoppas lokalt.

AI-förvalen och modellnamnen kontrollerades mot dokumentationen den 20 september
2026. De kan ändras i inställningarna när tjänsterna får nya modeller.
Alla tjänster får manuellt sammanhang och relevanta minnes-/termträffar.
Automatisk filkontext omfattar metadata, hänvisningar och ett begränsat antal
grannsträngar. Den kan stängas av och underlaget kan förhandsvisas lokalt.
Grannsträngarnas granskningsstatus följer med så att osäkra översättningar
kan skiljas från granskade. Endast vald källtext ska översättas.

API-kontrakten testas mot en lokal HTTP-server utan betalda anrop. Testerna
täcker varje förval, autentisering, kontext, felaktiga svar, avbrutna svar,
anropsgränser, omdirigeringar och avbrytning. Ingen omdirigering får föra
nycklar vidare och inga kostnadsbelagda anrop återförsöks automatiskt.

Anslutningarna delar inte API-nycklar mellan ändrade basadresser. Exportåtgärder
skickas bara efter att användaren sett och valt att skicka den konkreta filen.
Vid ett oklart nätverksfel efter sändning återförsöker Ordverk inte automatiskt.

Lokala filformat och verktyg följer [GNU Gettexts msgmerge](https://www.gnu.org/software/gettext/manual/html_node/msgmerge-Invocation.html),
[Qt TS](https://doc.qt.io/qt-6/linguist-ts-file-format.html),
[XLIFF 1.2](https://docs.oasis-open.org/xliff/v1.2/os/xliff-core.html) och
[XLIFF 2.0](https://docs.oasis-open.org/xliff/xliff-core/v2.0/os/xliff-core-v2.0-os.html).
POT-uppdatering kör `msgmerge` mot tillfälliga kopior och tillämpas först efter
förhandsgranskning. PO-konsistens kontrolleras med `msgfmt --check-format`.
