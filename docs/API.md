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
- [OpenAI-kompatibla Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create):
  messages, model, choices och JSON-innehåll. Adress och modell kan väljas fritt.

Anslutningarna delar inte API-nycklar mellan ändrade basadresser. Exportåtgärder
skickas bara efter att användaren sett och valt att skicka den konkreta filen.
Vid ett oklart nätverksfel efter sändning återförsöker Ordverk inte automatiskt.
