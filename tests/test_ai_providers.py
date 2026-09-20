import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ordverk.ai_context import catalog_context
from ordverk.ai_providers import PROVIDERS, is_configured
from ordverk.catalog import Catalog, Unit
from ordverk.deepl import restore
from ordverk.importers import Cancelled
from ordverk.llm import Translator
from ordverk.settings import Settings
from ordverk.workflow import batch_translate


@pytest.fixture
def service():
    state = {"requests": [], "status": 200, "response": None}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append((self.path, dict(self.headers), payload))
            self.send_response(state['status'])
            if state['status'] == 302:
                self.send_header('Location', '/do-not-forward')
            self.end_headers()
            response = state['response']
            if response is None:
                output = json.dumps({'translation': 'Spara', 'explanation': 'Knapp i bildredigeraren.'})
                if self.path.endswith('/messages'):
                    response = {'content': [{'type': 'thinking', 'thinking': 'internal'}, {'type': 'text', 'text': output}],
                                'stop_reason': 'end_turn', 'model': 'claude-test', 'usage': {'output_tokens': 12}}
                elif self.path.endswith('/translate'):
                    response = {'translations': [{'text': payload['text'][0].replace('Save', 'Spara'), 'billed_characters': 4}]}
                else:
                    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': output}}], 'usage': {}}
            self.wfile.write(response if isinstance(response, bytes) else json.dumps(response).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state['base'] = f'http://127.0.0.1:{server.server_port}'
    yield state
    server.shutdown()
    server.server_close()


def translator(service, provider='custom', **options):
    settings = Settings(ai_provider=provider, base_url=service['base'], model='' if provider.startswith('deepl') else 'test',
                        project_context='Bildredigerare', ai_instructions='Använd korta verb på knappar.', **options)
    store = SimpleNamespace(memory=lambda *a, **k: [], terminology=lambda *a, **k: [])
    return Translator(settings, store, SimpleNamespace(check=lambda *a: []), 'test-provider-token')


@pytest.mark.parametrize('provider', PROVIDERS, ids=lambda p: p.id)
def test_provider_protocol_context_and_authentication(service, provider):
    client = translator(service, provider.id)
    catalog = Catalog('sv.json', b'[{"source":"Open","target":""},{"source":"Save","target":""},{"source":"Close","target":""}]')
    unit = catalog.units[1]
    context = catalog_context(catalog, unit)
    result = client.suggest(unit, context=context)
    assert result.translation == 'Spara' and unit.targets == ['']
    path, headers, payload = service['requests'][0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers['user-agent'] == 'Ordverk/0.2'
    if provider.protocol == 'anthropic':
        assert path == '/messages'
        assert headers['x-api-key'] == 'test-provider-token'
        assert headers['anthropic-version'] == '2023-06-01'
        assert 'authorization' not in headers
        assert payload['max_tokens'] > 0
        task = json.loads(payload['messages'][0]['content'])
        assert 'korta verb' in payload['system']
    elif provider.protocol == 'deepl':
        assert path == '/translate'
        assert headers['authorization'] == 'DeepL-Auth-Key test-provider-token'
        assert payload['target_lang'] == 'SV' and payload['ignore_tags'] == ['ph']
        assert 'model' not in payload
        task = json.loads(payload['context'])
        assert 'korta verb' in task['translation_instructions']
    else:
        assert path == '/chat/completions'
        assert headers['authorization'] == 'Bearer test-provider-token'
        task = json.loads(payload['messages'][1]['content'])
        assert 'korta verb' in payload['messages'][0]['content']
    assert task['project_context'] == 'Bildredigerare'
    assert task['file_context']['file'] == 'sv.json'
    assert [n['source'] for n in task['file_context']['neighbors']] == ['Open', 'Close']


def test_automatic_context_can_be_disabled_without_losing_manual_context(service):
    client = translator(service, ai_auto_context=False)
    task = client.task(Unit('x', 'Save', ['']), context={'file': 'private.json'})
    assert task['file_context'] == {} and task['project_context'] == 'Bildredigerare'
    assert 'korta verb' in client.system()


def test_context_keeps_file_positions_after_selection_and_is_bounded():
    catalog = Catalog('sv.json', json.dumps([{'source': 'x' * 10000, 'target': ''} for _ in range(9)]).encode())
    unit = catalog.units[5]
    context = catalog_context(catalog, unit, 1)
    assert context['position'] == 6 and context['total_strings'] == 9
    assert len(context['neighbors']) == 2
    assert all(len(n['source']) == 2000 for n in context['neighbors'])
    assert catalog_context(catalog, unit, 0)['neighbors'] == []


@pytest.mark.parametrize('source', ['Save %s for {name}', 'Save %1$s: %.2f / %1', 'Save ⟦1⟧text⟦/1⟧',
                                    '<b>Save</b> & ${name}', 'Save\nfile https://example.org/a?x=1&y=2'])
def test_deepl_preserves_placeholders_and_markup(service, source):
    result = translator(service, 'deepl-free').suggest(Unit('x', source, ['']))
    assert result.translation == source.replace('Save', 'Spara')


@pytest.mark.parametrize('output', ['<text>Hej</text>', '<text><ph id="7">%s</ph></text>',
                                    '<text><ph id="0">%d</ph></text>', '<text><ph id="0">%s</ph><ph id="0">%s</ph></text>',
                                    '<!DOCTYPE text [<!ENTITY secret SYSTEM "file:///etc/passwd">]><text>&secret;</text>',
                                    '<text><ph'])
def test_deepl_rejects_damaged_placeholders_and_invalid_xml(output):
    with pytest.raises(ValueError, match='DeepL'):
        restore(output, ['%s'])


def test_deepl_size_limit_is_checked_before_sending(service):
    with pytest.raises(ValueError, match='för stora'):
        translator(service, 'deepl-pro').suggest(Unit('x', 'å' * 100000, ['']))
    assert not service['requests']


@pytest.mark.parametrize('response', [[], {}, b'\xff', b'```', {'choices': []},
    {'choices': [{'message': {'content': None}}]},
    {'choices': [{'message': {'content': '```'}}]},
    {'choices': [{'message': {'content': '{"translation": 3}'}}]},
    {'choices': [{'finish_reason': 'length', 'message': {'content': '{"translation":"Spara"}'}}]},
])
def test_malformed_responses_never_change_units(service, response):
    service['response'] = response
    unit = Unit('x', 'Save', ['Egen text'])
    with pytest.raises(ValueError):
        translator(service).suggest(unit)
    assert unit.targets == ['Egen text']


@pytest.mark.parametrize('reason', ['max_tokens', 'refusal', 'tool_use', None])
def test_anthropic_incomplete_or_refused_output_is_rejected(service, reason):
    service['response'] = {'stop_reason': reason, 'content': [{'type': 'text', 'text': '{"translation":"Spara"}'}]}
    with pytest.raises(ValueError, match='avslutade inte'):
        translator(service, 'anthropic').suggest(Unit('x', 'Save', ['']))


@pytest.mark.parametrize('status', [302, 400, 401, 403, 429, 456, 500])
def test_errors_do_not_forward_keys_retry_or_expose_bodies(service, status):
    service['status'], service['response'] = status, b'test-provider-token private source'
    catalog = Catalog('sv.json', b'[{"source":"Save","target":""},{"source":"Close","target":""}]')
    client = translator(service)
    changes, messages = batch_translate([catalog], client.store, client.quality, client, method='ai')
    assert not changes and len(service['requests']) == 1
    assert 'test-provider-token' not in str(messages) and 'private source' not in str(messages)


def test_cancelled_request_does_not_contact_provider(service):
    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        translator(service).suggest(Unit('x', 'Save', ['']), cancel=event)
    assert not service['requests']


def test_deepl_does_not_require_a_model():
    assert is_configured(Settings(ai_provider='deepl-free', base_url='https://api-free.deepl.com/v2'))
    assert not is_configured(Settings(ai_provider='anthropic', base_url='https://api.anthropic.com/v1'))


def test_context_includes_declared_project_and_source_language():
    catalog = Catalog('sv.po', b'msgid ""\nmsgstr ""\n"Project-Id-Version: Photo Editor 2.0\\n"\n"X-Source-Language: en\\n"\n\nmsgid "Save"\nmsgstr ""\n')
    context = catalog_context(catalog, catalog.units[0])
    assert context['project'] == 'Photo Editor 2.0' and context['source_language'] == 'en'


def test_batch_passes_context_for_selected_strings_only(service):
    client = translator(service, 'anthropic')
    catalog = Catalog('sv.json', b'[{"source":"Open","target":""},{"source":"Save","target":""},{"source":"Close","target":""}]')
    selected = {(id(catalog), catalog.units[1].key)}
    changes, _ = batch_translate([catalog], client.store, client.quality, client, selected=selected, method='ai')
    assert len(changes) == len(service['requests']) == 1
    task = json.loads(service['requests'][0][2]['messages'][0]['content'])
    assert task['file_context']['position'] == 2
    assert [n['source'] for n in task['file_context']['neighbors']] == ['Open', 'Close']


def test_cancellation_during_response_preserves_existing_translation(service):
    event = threading.Event()
    client = translator(service)
    client.quality.check = lambda *_: event.set() or []
    unit = Unit('x', 'Save', ['Egen översättning'])
    with pytest.raises(Cancelled):
        client.suggest(unit, cancel=event)
    assert unit.targets == ['Egen översättning']


def test_invalid_key_is_rejected_without_sending(service):
    client = translator(service)
    client.session_key = 'invalid\nkey'
    with pytest.raises(ValueError, match='radbrytningar'):
        client.suggest(Unit('x', 'Save', ['']))
    assert not service['requests']


@pytest.mark.parametrize('provider', ['anthropic', 'deepl-free'])
def test_invalid_provider_response_has_a_swedish_error(service, provider):
    service['response'] = []
    with pytest.raises(ValueError, match='API-svaret hade ett oväntat format'):
        translator(service, provider).suggest(Unit('x', 'Save', ['']))
