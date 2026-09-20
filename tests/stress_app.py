"""Repeatable, offline stress run: xvfb-run -a dbus-run-session -- python tests/stress_app.py."""
import argparse
import json
import os
from pathlib import Path
import random
import resource
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
parser = argparse.ArgumentParser()
parser.add_argument('--size', type=int, default=10000)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
report = {'strings': args.size, 'timings': {}}


def measured(name, operation):
    start = time.monotonic()
    result = operation()
    report['timings'][name] = round(time.monotonic() - start, 3)
    return result


with tempfile.TemporaryDirectory(prefix='ordverk-stress-') as temporary:
    root = Path(temporary)
    files = root / "files"
    files.mkdir()
    os.environ['ORDVERK_HOME'] = str(root / 'cache')
    from ordverk.catalog import Catalog
    from ordverk.gtk_support import GLib
    from ordverk.settings import Settings
    from ordverk.ui import Application, Window
    from ordverk.workflow import batch_translate, apply_changes

    count = min(args.size, 5000)
    formats = {
        'sv.po': '\n\n'.join(f'msgid "Item {i} %s"\nmsgstr ""' for i in range(count)),
        'sv.ts': '<TS version="2.1" language="sv_SE"><context><name>Editor</name>' + ''.join(
            f'<message><source>Item {i} %s</source><translation type="unfinished"/></message>' for i in range(count)) + '</context></TS>',
        'sv.xlf': '<xliff version="1.2"><file source-language="en" target-language="sv"><body>' + ''.join(
            f'<trans-unit id="{i}"><source>Item {i} %s</source><target/></trans-unit>' for i in range(count)) + '</body></file></xliff>',
        'sv.xliff': '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="sv"><file id="f">' + ''.join(
            f'<unit id="{i}"><segment><source>Item {i} %s</source><target/></segment></unit>' for i in range(count)) + '</file></xliff>',
        'sv.json': json.dumps([{'source': f'Item {i} %s', 'target': ''} for i in range(args.size)]),
    }
    catalogs = []
    randomizer = random.Random(42)
    for filename, data in formats.items():
        path = files / filename
        path.write_text(data)
        catalog = measured('open_' + filename, lambda: Catalog.open(path))
        for index in randomizer.sample(range(len(catalog.units)), min(200, len(catalog.units))):
            catalog.units[index].edit(0, catalog.units[index].source.replace('Item', 'Text'))
        expected = [list(unit.targets) for unit in catalog.units]
        measured('save_' + filename, catalog.save)
        reopened = Catalog.open(path)
        assert [unit.targets for unit in reopened.units] == expected, filename
        catalogs.append(catalog)

    app = Application()
    app.register(None)
    window = Window(app, settings=Settings(auto_update_resources=False, show_import_guide=False,
                                          use_hunspell=False, use_aspell=False), startup=False)
    window.present()
    errors = []
    window.error = lambda text, **kwargs: errors.append(text)
    previous_hook = sys.excepthook
    sys.excepthook = lambda *exc: errors.append(str(exc[1]))
    ticks = []
    timer = GLib.timeout_add(20, lambda: ticks.append(time.monotonic()) or True)

    def pump(seconds=.05):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.001)

    import_started = time.monotonic()
    window.import_paths([str(files)])
    while window.busy and time.monotonic() - import_started < 30:
        pump(.02)
    assert not window.busy and len(window.catalogs) == 5, errors
    report['timings']['import_folder'] = round(time.monotonic() - import_started, 3)
    index = next(i for i, catalog in enumerate(window.catalogs) if catalog.name == 'sv.json')
    measured('display_large_file', lambda: window.file_picker.set_selected(index))
    pump()
    assert window.unit_store.get_n_items() == args.size
    measured('mark_all', lambda: window.mark_visible(True))
    assert len(window.marked) == args.size
    window.mark_visible(False)
    measured('select_last', lambda: window.selection.set_selected(args.size - 1))
    measured('edit_100_times', lambda: [window.set_target(f'Svensk text {i} %s') for i in range(100)])
    assert window.catalog.units[-1].targets == ['Svensk text 99 %s']
    pump()

    def filters():
        for query in ('Item 1', 'Text', 'missing', '') * 3:
            window.search.set_text(query)
            window.filter_units()
            pump(.01)
    measured('filter_12_times', filters)
    assert window.unit_store.get_n_items() == args.size
    assert window.unit_store.get_item(args.size - 1).number == args.size

    # Rapid selection with slow lookups must not starve later user work.
    calls = []
    original_memory = window.store.memory
    def slow_memory(*a, **kw):
        calls.append(a[0])
        time.sleep(.02)
        return []
    window.store.memory = slow_memory
    measured('select_100_times', lambda: [window.selection.set_selected(i) for i in range(min(100, args.size))])
    completed = threading.Event()
    wait_started = time.monotonic()
    window.job('Kontrollerar köer', lambda _: None, lambda _: completed.set())
    while not completed.is_set() and time.monotonic() - wait_started < 10:
        pump(.02)
    report['timings']['job_after_selection'] = round(time.monotonic() - wait_started, 3)
    assert completed.is_set(), 'Background queue starved a user job'
    window.store.memory = original_memory
    report['memory_lookups_after_selection'] = len(calls)

    completed.clear()
    def many_progress_updates(_cancel):
        for i in range(100000):
            window.progress_message('Föröversätter', i + 1, 100000)
    progress_started = time.monotonic()
    window.job('Många förloppsuppdateringar', many_progress_updates, lambda _: completed.set())
    while not completed.is_set() and time.monotonic() - progress_started < 10:
        pump(.02)
    assert completed.is_set(), 'Progress updates flooded the main loop'
    report['timings']['progress_updates_100000'] = round(time.monotonic() - progress_started, 3)

    cancel = threading.Event()
    cancel.set()
    store = SimpleNamespace(exact_translation=lambda *a: ('Översättning %s', 'stress'))
    quality = SimpleNamespace(check=lambda *a: [])
    result = measured('already_cancelled_batch', lambda: batch_translate(catalogs, store, quality, cancel=cancel))
    assert not result[0]
    changes, messages = measured('batch_1000', lambda: batch_translate(catalogs, store, quality, limit=1000))
    assert changes and all(not u.changed for c in catalogs[:-1] for u in c.units)
    victim = changes[0]
    victim.unit.edit(victim.variant, 'Manuellt ändrad %s')
    applied, skipped = measured('apply_batch', lambda: apply_changes(changes))
    assert skipped == 1 and applied == len(changes) - 1
    assert victim.unit.targets[victim.variant] == 'Manuellt ändrad %s'
    assert not errors, errors
    report['max_main_loop_gap'] = round(max((b - a for a, b in zip(ticks, ticks[1:])), default=0), 3)
    report['errors'] = errors
    report['peak_memory_mib'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    GLib.source_remove(timer)
    window.shutdown()
    app.quit()
    sys.excepthook = previous_hook

print(json.dumps(report, ensure_ascii=False, indent=2))
if args.output:
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
