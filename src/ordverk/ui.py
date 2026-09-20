"""Native GTK4/libadwaita workbench. Worker results are marshalled to the main loop."""
from __future__ import annotations

import copy
import json
import locale
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import gi

os.environ["LANGUAGE"] = "sv"
try:
    locale.setlocale(locale.LC_MESSAGES, "sv_SE.UTF-8")
except locale.Error:
    pass  # Ordverk's own strings are Swedish even when this system locale is absent.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from . import __version__
from .catalog import atomic_write
from .dialogs import ImportGuide, Preferences, PretranslateDialog
from .importers import import_sources
from .llm import Translator
from .quality import Quality
from .progress import ProgressState
from .resources import ResourceStore
from .remote_dialogs import Connections, MailDialog
from .diff_dialog import DiffDialog
from .diffs import parse_diff, propose_diff
from .settings import Settings
from .statistics import statistics
from .workflow import apply_changes, batch_translate


def label(text="", css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=True)
    if css:
        widget.add_css_class(css)
    return widget


def clear(box):
    while box.get_first_child():
        box.remove(box.get_first_child())


def button(text, callback, icon=None, css=None):
    widget = Gtk.Button(label=text)
    if icon:
        content = Gtk.Box(spacing=6)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(Gtk.Label(label=text))
        widget.set_child(content)
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda _: callback())
    return widget


def scroll(child, **kwargs):
    view = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, **kwargs)
    view.set_child(child)
    return view


class UnitRow(GObject.Object):
    changed = GObject.Signal()

    def __init__(self, unit):
        super().__init__()
        self.unit = unit


class Window(Adw.ApplicationWindow):
    def __init__(self, application, *, settings=None, store=None, startup=True):
        super().__init__(application=application, title="Ordverk", default_width=1440, default_height=920)
        self.settings = settings or Settings.load()
        self.store = store or ResourceStore()
        self.session_key = ""
        self.catalogs, self.catalog, self.unit = [], None, None
        self.variant, self.loading, self.closed = 0, False, False
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ordverk")
        self.check_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ordverk-check")
        self.check_future = None
        self.check_generation = 0
        self.job_cancel, self.resource_cancel = threading.Event(), threading.Event()
        self.busy, self.updating = False, False
        self.job_meter = self.resource_meter = self.check_meter = None
        self.progress_timer = None
        self.diagnostics = []
        self.session_statistics = {"memory": 0, "ai": 0}
        self.marked = set()
        self.suggestion_generation = 0
        self.suggestion_meter = None
        self.report_ready = False
        self.connection_tokens = {}
        self.pending_save = None
        self.refresh_services()
        self._build()
        icon_folder = Path(__file__).resolve().parents[2] / "data"
        if icon_folder.is_dir():
            Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(icon_folder))
        Gtk.IconTheme.get_for_display(self.get_display()).add_search_path(str(Path(sys.prefix) / "share/pixmaps"))
        self.set_icon_name("io.github.yeager.Ordverk")
        self.connect("close-request", self.on_close)
        self._actions()
        if startup and self.settings.auto_update_resources:
            GLib.idle_add(self.update_resources)

    def refresh_services(self):
        self.quality = Quality(self.settings, self.store)
        self.translator = Translator(self.settings, self.store, self.quality, self.session_key)
        if hasattr(self, "api_status"):
            self.api_status.set_label(f"AI · {self.settings.model}" if self.settings.model else "AI inte konfigurerad")
            self.schedule_check()

    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(root)
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="Ordverk", subtitle="Din svenska översättningsverkstad")
        header.set_title_widget(self.title_widget)
        menu = Gio.Menu()
        for text, action in [("Filer…", "win.open"), ("Mapp med undermappar…", "win.folder"),
                             ("Diff / patch…", "win.diff"),
                             ("URL eller GitHub-förråd…", "win.url"), ("Översättningsminne (PO)…", "win.memory"),
                             ("Terminologi (TBX / CSV)…", "win.terms"), ("GitHub / Weblate / Transifex / Crowdin…", "win.connections")]:
            menu.append(text, action)
        import_button = Gtk.MenuButton(label="Importera", menu_model=menu, css_classes=["suggested-action"])
        header.pack_start(import_button)
        self.save_button = button("Spara", self.save, "document-save-symbolic")
        self.save_button.set_sensitive(False)
        header.pack_start(self.save_button)
        header.pack_start(button("Föröversätt…", self.pretranslate))
        header.pack_end(button("Inställningar", lambda: Preferences(self).present(), "emblem-system-symbolic"))
        more = Gio.Menu()
        for text, action in [("Spara som…", "win.save-as"), ("Koppla käll-JSON…", "win.reference"),
                             ("Kör importguiden igen…", "win.guide"), ("Granska hela filen", "win.review-file"),
                             ("Exportera granskningsrapport…", "win.report"), ("Exportera eget minne…", "win.export-memory"),
                             ("Uppdatera språkresurser", "win.update"), ("Om Ordverk", "win.about")]:
            more.append(text, action)
        more.append("Importera eller exportera till tjänst…", "win.connections")
        more.append("Skicka via e-post / TP…", "win.mail")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=more))
        root.append(header)

        toolbar = Gtk.Box(spacing=10, margin_start=16, margin_end=16, margin_top=10, margin_bottom=10)
        self.file_names = Gtk.StringList()
        self.file_picker = Gtk.DropDown(model=self.file_names, hexpand=True, enable_search=True)
        self.file_picker.set_tooltip_text("Importerade kataloger")
        self.file_picker.connect("notify::selected", self.select_catalog)
        toolbar.append(self.file_picker)
        self.api_status = label("AI inte konfigurerad", "dim-label")
        toolbar.append(self.api_status)
        toolbar.append(button("Statistik", self.show_statistics, "view-list-symbolic"))
        toolbar.append(button("Granska fil", lambda: self.review_catalogs([self.catalog]) if self.catalog else None,
                              "object-select-symbolic"))
        root.append(toolbar)
        summary = Gtk.Box(spacing=24, margin_start=18, margin_end=18, margin_bottom=12)
        self.statistics_labels = {}
        for key, caption in [("total", "strängar"), ("translated", "översatta"), ("reviewed", "granskade"), ("remaining", "kvar")]:
            box = Gtk.Box(spacing=6)
            value = label("0", "heading")
            self.statistics_labels[key] = value
            box.append(value)
            box.append(label(caption, "dim-label"))
            summary.append(box)
        self.completion = Gtk.ProgressBar(show_text=True, text="0 % översatt", hexpand=True, valign=Gtk.Align.CENTER)
        summary.append(self.completion)
        root.append(summary)
        root.append(Gtk.Separator())
        self.stack = Gtk.Stack(vexpand=True)
        root.append(self.stack)
        welcome = Adw.StatusPage(title="Välkommen till Ordverk", icon_name="io.github.yeager.Ordverk",
                                 description="Översätt med sammanhang. Granska med omsorg.\nPO · Qt TS · XLIFF · JSON")
        welcome_buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        welcome_buttons.append(button("Importera filer", lambda: self.choose_files(), css="suggested-action"))
        welcome_buttons.append(button("Importera en mapp", self.choose_folder))
        welcome_buttons.append(button("Hämta från URL eller GitHub", self.url_dialog))
        welcome_buttons.append(label("Du kan också släppa filer och mappar här.", "dim-label"))
        welcome.set_child(welcome_buttons)
        self.stack.add_named(welcome, "welcome")

        workspace = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=335,
                              resize_start_child=False, shrink_start_child=False, shrink_end_child=False)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=260,
                       margin_start=12, margin_end=12, margin_top=14, margin_bottom=12)
        left.append(label("STRÄNGAR", "heading"))
        self.search = Gtk.SearchEntry(placeholder_text="Sök källtext, svenska eller kontext")
        self.search.connect("search-changed", lambda _: self.filter_units())
        left.append(self.search)
        self.filter = Gtk.DropDown.new_from_strings(["Alla strängar", "Oöversatta", "Att granska", "Granskade"])
        self.filter.connect("notify::selected", lambda *_: self.filter_units())
        left.append(self.filter)
        marked_controls = Gtk.Box(spacing=6)
        marked_controls.append(button("Markera visade", lambda: self.mark_visible(True)))
        marked_controls.append(button("Rensa", lambda: self.mark_visible(False)))
        left.append(marked_controls)
        self.unit_store = Gio.ListStore.new(UnitRow)
        self.selection = Gtk.SingleSelection(model=self.unit_store, autoselect=False)
        self.selection.connect("notify::selected-item", self.select_unit)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        factory.connect("unbind", self.unbind_row)
        self.unit_list = Gtk.ListView(model=self.selection, factory=factory, css_classes=["navigation-sidebar"])
        left.append(scroll(self.unit_list, vexpand=True))
        self.list_count = label("", "dim-label")
        left.append(self.list_count)
        workspace.set_start_child(left)

        right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=630,
                          resize_start_child=True, resize_end_child=False, shrink_start_child=False, shrink_end_child=False)
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, width_request=360,
                         margin_start=22, margin_end=22, margin_top=20, margin_bottom=20)
        caption = Gtk.Box(spacing=8)
        caption.append(label("ÖVERSÄTTNING", "heading"))
        self.unit_status = Gtk.Label(hexpand=True, xalign=1, css_classes=["dim-label"])
        caption.append(self.unit_status)
        editor.append(caption)
        self.context_label = label("", "dim-label", wrap=True)
        editor.append(self.context_label)
        self.variants = Gtk.DropDown()
        self.variants.connect("notify::selected", self.select_variant)
        editor.append(self.variants)
        editor.append(label("Källtext", "heading"))
        self.source_view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                        left_margin=12, right_margin=12, top_margin=12, bottom_margin=12)
        editor.append(scroll(self.source_view, min_content_height=100, max_content_height=200, css_classes=["card"]))
        target_caption = Gtk.Box(spacing=8)
        target_caption.append(label("Svenska", "heading"))
        self.length_label = Gtk.Label(hexpand=True, xalign=1, css_classes=["dim-label"])
        target_caption.append(self.length_label)
        editor.append(target_caption)
        self.target_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                        left_margin=12, right_margin=12, top_margin=12, bottom_margin=12)
        self.target_buffer = self.target_view.get_buffer()
        self.target_buffer.set_enable_undo(True)
        self.target_buffer.connect("changed", self.target_changed)
        editor.append(scroll(self.target_view, min_content_height=150, vexpand=True, css_classes=["card"]))
        controls = Gtk.Box(spacing=8)
        self.ai_button = button("Föreslå med AI", self.request_ai, "system-search-symbolic")
        controls.append(self.ai_button)
        controls.append(button("Kopiera källa", self.copy_source))
        self.review_button = button("Markera granskad", self.mark_reviewed, "object-select-symbolic", "suggested-action")
        controls.append(self.review_button)
        editor.append(controls)
        self.notes = label("", "dim-label", wrap=True)
        editor.append(self.notes)
        self.issue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.issue_box.append(label("Välj en sträng för att börja granska.", "dim-label", wrap=True))
        self.check_bar = Gtk.ProgressBar(show_text=True, text="Granskar strängen…", visible=False)
        editor.append(self.check_bar)
        expander = Gtk.Expander(label="Kvalitetskontroller", expanded=True)
        expander.set_child(scroll(self.issue_box, min_content_height=110, max_content_height=190))
        editor.append(expander)
        right.set_start_child(editor)

        help_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=310,
                             margin_start=18, margin_end=18, margin_top=20, margin_bottom=20)
        help_panel.append(label("SPRÅKSTÖD", "heading"))
        help_panel.append(label("Förslag med sammanhang", "title-3"))
        help_panel.append(label("Bedöm alltid träffen i sitt nya sammanhang.", "dim-label", wrap=True))
        self.suggestion_bar = Gtk.ProgressBar(show_text=True, visible=False)
        help_panel.append(self.suggestion_bar)
        self.proposal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        help_panel.append(self.proposal_box)
        help_panel.append(Gtk.Separator())
        help_panel.append(label("Översättningsminne", "heading"))
        self.memory_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        help_panel.append(self.memory_box)
        help_panel.append(Gtk.Separator())
        help_panel.append(label("Terminologi", "heading"))
        self.term_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        help_panel.append(self.term_box)
        help_panel.append(Gtk.Separator())
        help_panel.append(label("Referenser", "heading"))
        for title, uri in [("Computer Swedens IT-ord", "https://it-ord.computersweden.se/"),
                           ("SAOL · SO · SAOB", "https://svenska.se/"),
                           ("Rikstermbanken", "https://www.rikstermbanken.se/")]:
            help_panel.append(Gtk.LinkButton(uri=uri, label=title, halign=Gtk.Align.START))
        right.set_end_child(scroll(help_panel))
        workspace.set_end_child(right)
        self.stack.add_named(workspace, "workspace")

        root.append(Gtk.Separator())
        footer = Gtk.Box(spacing=12, margin_start=16, margin_end=16, margin_top=9, margin_bottom=9)
        self.progress = Gtk.Spinner()
        footer.append(self.progress)
        self.status = label("Redo att importera", "dim-label")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_hexpand(True)
        footer.append(self.status)
        self.cancel_button = button("Avbryt", self.cancel_job)
        self.cancel_button.set_visible(False)
        footer.append(self.cancel_button)
        root.append(footer)
        self.job_bar = Gtk.ProgressBar(show_text=True, visible=False, margin_start=16, margin_end=16, margin_bottom=8)
        root.append(self.job_bar)
        self.resource_status = Gtk.Label(label="Språkresurser · lokalt minne", xalign=0, margin_start=16,
                                         margin_end=16, margin_bottom=8, css_classes=["caption", "dim-label"])
        self.resource_status.set_ellipsize(Pango.EllipsizeMode.END)
        resource_footer = Gtk.Box(spacing=8)
        self.resource_status.set_hexpand(True)
        resource_footer.append(self.resource_status)
        self.resource_stop = button("Avbryt uppdatering", lambda: self.resource_cancel.set())
        self.resource_stop.set_visible(False)
        resource_footer.append(self.resource_stop)
        root.append(resource_footer)
        self.resource_bar = Gtk.ProgressBar(show_text=True, visible=False, margin_start=16, margin_end=16, margin_bottom=8)
        root.append(self.resource_bar)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.refresh_services()
        counts = self.store.counts()
        self.resource_status.set_label(f"Språkresurser · {counts['memory']:,} minnesposter · {counts['terms']:,} termer".replace(",", " "))

    def _actions(self):
        actions = {
            "open": self.choose_files, "folder": self.choose_folder, "url": self.url_dialog,
            "save": self.save, "save-as": lambda: self.save(True), "reference": self.choose_reference,
            "memory": lambda: self.choose_resource("memory"), "terms": lambda: self.choose_resource("terms"),
            "guide": lambda: ImportGuide(self, [self.catalog]).present() if self.catalog else None,
            "review-file": lambda: self.review_catalogs([self.catalog]) if self.catalog else None,
            "report": self.export_report, "export-memory": self.export_memory,
            "update": self.update_resources, "about": self.about,
            "next": lambda: self.move(1), "previous": lambda: self.move(-1),
            "approve": self.mark_reviewed,
            "pretranslate": self.pretranslate,
            "connections": lambda: Connections(self).present(),
            "diff": self.import_diff,
            "mail": lambda: MailDialog(self).present() if self.catalog else self.toast("Importera en fil först."),
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, callback=callback: callback())
            self.add_action(action)
        for action, keys in {"open": ["<Control>o"], "save": ["<Control>s"], "save-as": ["<Control><Shift>s"],
                             "next": ["<Alt>Down"], "previous": ["<Alt>Up"], "approve": ["<Control>Return"]}.items():
            self.get_application().set_accels_for_action("win." + action, keys)

    def toast(self, text):
        if not self.closed:
            self.toasts.add_toast(Adw.Toast(title=text, timeout=5))

    def error(self, text, parent=None):
        if self.closed:
            return
        dialog = Adw.MessageDialog(transient_for=parent or self, modal=True, heading="Det gick inte att slutföra", body=str(text))
        dialog.add_response("ok", "Stäng")
        dialog.present()

    def dispatch(self, callback, *args):
        def run():
            if not self.closed:
                callback(*args)
            return False
        GLib.idle_add(run)

    def job(self, text, operation, done):
        if self.busy:
            self.toast("Låt det pågående arbetet bli klart eller avbryt det först.")
            return
        self.busy = True
        self.job_meter = ProgressState()
        self.start_progress_timer()
        self.job_cancel = threading.Event()
        self.progress.start()
        self.cancel_button.set_visible(True)
        self.status.set_label(text)
        future = self.executor.submit(operation, self.job_cancel)

        def complete(f):
            def finish():
                self.busy = False
                self.job_meter = None
                self.job_bar.set_visible(False)
                self.stack.set_sensitive(True)
                self.progress.stop()
                self.cancel_button.set_visible(False)
                try:
                    result = f.result()
                    done(result)
                except Exception as exc:
                    self.status.set_label("Arbetet kunde inte slutföras")
                    self.error(str(exc))
            self.dispatch(finish)
        future.add_done_callback(complete)
        return True

    def progress_message(self, text, current=None, total=None):
        def update():
            self.status.set_label(text)
            if self.job_meter:
                self.job_meter.update(current, total)
        self.dispatch(update)

    def start_progress_timer(self):
        if self.progress_timer is None:
            self.progress_timer = GLib.timeout_add(120, self.tick_progress)

    def tick_progress(self):
        for meter, bar in ((self.job_meter, self.job_bar), (self.resource_meter, self.resource_bar),
                           (self.check_meter, self.check_bar), (self.suggestion_meter, self.suggestion_bar)):
            visible = meter is not None and meter.visible()
            bar.set_visible(visible)
            if visible:
                if meter.fraction is None:
                    bar.set_text("Arbetar…")
                    bar.pulse()
                else:
                    bar.set_fraction(meter.fraction)
                    bar.set_text(f"{meter.current} av {meter.total} · {meter.fraction:.0%}")
        self.resource_stop.set_visible(self.resource_meter is not None and self.resource_meter.visible())
        if self.closed or not any((self.job_meter, self.resource_meter, self.check_meter, self.suggestion_meter)):
            self.progress_timer = None
            return False
        return True

    def cancel_job(self):
        self.job_cancel.set()
        self.status.set_label("Avbryter efter pågående anrop…")

    def update_resources(self, components=None):
        if self.updating:
            self.toast("Språkresurserna uppdateras redan.")
            return False
        self.updating = True
        self.resource_cancel = threading.Event()
        self.resource_meter = ProgressState()
        self.start_progress_timer()
        components = list(components if components is not None else self.settings.resource_components)
        def progress(text, current=None, total=None):
            def update():
                self.resource_status.set_label(text)
                if self.resource_meter:
                    self.resource_meter.update(current, total)
            self.dispatch(update)
        future = self.executor.submit(self.store.update, components,
                                       progress=progress,
                                       cancel=self.resource_cancel)

        def done(f):
            def finish():
                self.updating = False
                self.resource_meter = None
                self.resource_bar.set_visible(False)
                self.resource_stop.set_visible(False)
                try:
                    errors = f.result()
                    counts = self.store.counts()
                    summary = f"Språkresurser · {counts['memory']:,} minnesposter · {counts['terms']:,} termer".replace(",", " ")
                    if errors:
                        summary += " · Uppdateringen är ofullständig (se information)"
                        self.resource_status.set_tooltip_text("\n".join(errors))
                        self.toast("Vissa språkresurser kunde inte uppdateras. Tillgänglig cache används.")
                    self.resource_status.set_label(summary)
                    self.schedule_check()
                    self.refresh_suggestions()
                except Exception as exc:
                    self.resource_status.set_label("Tillgänglig cache används · " + str(exc))
            self.dispatch(finish)
        future.add_done_callback(done)
        return False

    def file_dialog(self, title, callback, *, patterns=None, folder=False, multiple=False, save_name=None):
        dialog = Gtk.FileDialog(title=title, modal=True, accept_label="Spara" if save_name else "Välj")
        if patterns:
            file_filter = Gtk.FileFilter(name=title)
            for pattern in patterns:
                file_filter.add_pattern(pattern)
            dialog.set_filters(Gio.ListStore.new(Gtk.FileFilter))
            dialog.get_filters().append(file_filter)
        if save_name:
            dialog.set_initial_name(save_name)

        def response(d, result):
            try:
                if folder:
                    selected = d.select_folder_finish(result)
                elif save_name:
                    selected = d.save_finish(result)
                elif multiple:
                    files = d.open_multiple_finish(result)
                    callback([files.get_item(i).get_path() for i in range(files.get_n_items())])
                    return
                else:
                    selected = d.open_finish(result)
                if not selected.get_path():
                    raise ValueError("Välj en lokal fil eller använd URL-import.")
                callback(selected.get_path())
            except GLib.Error:
                return
            except Exception as exc:
                self.error(str(exc))
        if folder:
            dialog.select_folder(self, None, response)
        elif save_name:
            dialog.save(self, None, response)
        elif multiple:
            dialog.open_multiple(self, None, response)
        else:
            dialog.open(self, None, response)

    def choose_files(self):
        self.file_dialog("Importera översättningsfiler", self.import_paths, multiple=True)

    def choose_folder(self):
        self.file_dialog("Importera en mapp", lambda path: self.import_paths([path]), folder=True)

    def import_diff(self):
        if not self.catalogs:
            self.toast("Importera filerna som diffen gäller först.")
            return
        def load(path):
            def run(cancel):
                from .importers import check_cancel
                proposals = []
                for patch in parse_diff(Path(path).read_text(encoding="utf-8-sig")):
                    check_cancel(cancel)
                    matches = [c for c in self.catalogs if str(c.path or c.name).endswith("/" + patch.before) or c.name == patch.before]
                    if not matches:
                        matches = [c for c in self.catalogs if Path(c.name).name == Path(patch.before).name]
                    if len(matches) != 1:
                        raise ValueError(f"{patch.before}: importera en entydig grundfil först. {len(matches)} matchande filer är öppna.")
                    proposals.append(propose_diff(matches[0], patch))
                return proposals
            self.job("Läser och jämför diff…", run, lambda proposals: [DiffDialog(self, p).present() for p in proposals])
        self.file_dialog("Importera unified diff", load, patterns=["*.diff", "*.patch"])

    def url_dialog(self):
        dialog = Adw.MessageDialog(transient_for=self, heading="Importera från nätet",
                                   body="Ange en direktadress till en PO-, TS-, XLIFF- eller JSON-fil, eller ett offentligt GitHub-förråd.")
        entry = Gtk.Entry(placeholder_text="https://…", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Avbryt")
        dialog.add_response("import", "Importera")
        dialog.set_default_response("import")
        dialog.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: self.import_paths([entry.get_text().strip()]) if response == "import" else None)
        dialog.present()

    def on_drop(self, _target, value, _x, _y):
        self.import_paths([f.get_path() or f.get_uri() for f in value.get_files()])
        return True

    def import_paths(self, paths):
        self.job("Importerar…", lambda cancel: import_sources(paths, progress=self.progress_message, cancel=cancel), self.finish_import)

    def finish_import(self, result):
        new = []
        origins = {c.origin for c in self.catalogs}
        for catalog in result.catalogs:
            if catalog.origin not in origins:
                origins.add(catalog.origin)
                new.append(catalog)
        first = len(self.catalogs)
        self.catalogs.extend(new)
        self.refresh_files()
        if new:
            self.file_picker.set_selected(first)
            self.select_catalog()
            self.stack.set_visible_child_name("workspace")
        self.status.set_label(f"{len(new)} filer importerades" + (" · importen avbröts" if result.cancelled else ""))
        if result.errors:
            self.show_report("Importresultat", result.errors)
        if new and not result.cancelled:
            if self.settings.show_import_guide or any(c.language and not c.language.lower().startswith("sv") for c in new):
                ImportGuide(self, new).present()
            else:
                self.start_workflow(new, self.settings.import_purpose, self.settings.import_automatic,
                                    self.settings.automatic_use_ai)

    def add_copies(self, catalogs):
        first = len(self.catalogs)
        self.catalogs.extend(catalogs)
        self.refresh_files()
        self.file_picker.set_selected(first)
        self.select_catalog()

    def refresh_files(self):
        self.loading = True
        selected = self.file_picker.get_selected()
        self.file_names.splice(0, self.file_names.get_n_items(),
                               [("● " if c.dirty else "") + c.name for c in self.catalogs])
        if selected < len(self.catalogs):
            self.file_picker.set_selected(selected)
        self.loading = False

    def select_catalog(self, *_):
        if self.loading:
            return
        i = self.file_picker.get_selected()
        if i >= len(self.catalogs):
            return
        self.catalog = self.catalogs[i]
        self.title_widget.set_subtitle(self.catalog.name)
        self.file_picker.set_tooltip_text(self.catalog.origin)
        self.save_button.set_sensitive(True)
        self.filter_units()
        self.update_statistics()

    def setup_row(self, _factory, item):
        outer = Gtk.Box(spacing=6)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text="Markera för föröversättning")
        check.connect("toggled", lambda widget: self.toggle_mark(item, widget))
        outer.append(check)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5,
                      margin_top=9, margin_bottom=9, margin_start=6, margin_end=6, hexpand=True)
        for css in ("heading", "dim-label", "caption"):
            line = label("", css)
            line.set_selectable(False)
            line.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(line)
        outer.append(box)
        item.set_child(outer)

    def bind_row(self, _factory, item):
        item.handler = item.get_item().connect("changed", lambda _: self.update_row(item))
        self.update_row(item)

    def unbind_row(self, _factory, item):
        item.get_item().disconnect(item.handler)

    def update_row(self, item):
        unit = item.get_item().unit
        check = item.get_child().get_first_child()
        item.binding = True
        check.set_active((id(self.catalog), unit.key) in self.marked)
        item.binding = False
        box = check.get_next_sibling()
        source = box.get_first_child()
        target = source.get_next_sibling()
        state = target.get_next_sibling()
        source.set_label(unit.source.replace("\n", " ↵ "))
        target.set_label((unit.targets[0] or "Skriv en svensk översättning…").replace("\n", " ↵ "))
        state.set_label(unit.status + (" · " + unit.context[:40] if unit.context else ""))

    def toggle_mark(self, item, check):
        if getattr(item, "binding", False) or not item.get_item() or not self.catalog:
            return
        identity = (id(self.catalog), item.get_item().unit.key)
        if check.get_active():
            self.marked.add(identity)
        else:
            self.marked.discard(identity)
        self.update_list_count()

    def update_list_count(self):
        if self.catalog:
            marked = sum((id(self.catalog), u.key) in self.marked for u in self.catalog.units)
            self.list_count.set_label(f"{self.unit_store.get_n_items()} av {len(self.catalog.units)} · {marked} markerade")

    def mark_visible(self, active):
        if not self.catalog:
            return
        if not active:
            self.marked = {key for key in self.marked if key[0] != id(self.catalog)}
        for row in self.unit_store:
            if active:
                self.marked.add((id(self.catalog), row.unit.key))
            row.emit("changed")
        self.update_list_count()

    def filter_units(self):
        if not self.catalog:
            return
        previous = self.unit
        query = self.search.get_text().casefold()
        status_filter = self.filter.get_selected()
        statuses = {1: "Oöversatt", 2: "Att granska", 3: "Granskad"}
        units = [u for u in self.catalog.units if (not status_filter or u.status == statuses[status_filter])
                 and (not query or query in "\n".join([u.source, *u.targets, u.context, u.notes]).casefold())]
        self.loading = True
        self.unit_store.splice(0, self.unit_store.get_n_items(), [UnitRow(u) for u in units])
        index = next((i for i, u in enumerate(units) if u is previous or (previous and u.key == previous.key)), 0)
        if units:
            self.selection.set_selected(index)
        self.loading = False
        self.update_list_count()
        self.select_unit()

    def select_unit(self, *_):
        if self.loading:
            return
        selected = self.selection.get_selected_item()
        self.unit = selected.unit if selected else None
        self.loading = True
        self.variant = 0
        self.variants.set_model(Gtk.StringList.new(self.unit.variants if self.unit else ["Översättning"]))
        self.variants.set_selected(0)
        self.loading = False
        clear(self.proposal_box)
        self.show_unit()

    def select_variant(self, *_):
        if not self.loading and self.unit:
            self.variant = min(self.variants.get_selected(), len(self.unit.targets) - 1)
            clear(self.proposal_box)
            self.show_unit()

    def show_unit(self):
        self.loading = True
        unit = self.unit
        self.target_view.set_sensitive(unit is not None)
        self.ai_button.set_sensitive(unit is not None and not unit.source_is_key)
        self.review_button.set_sensitive(unit is not None)
        self.source_view.get_buffer().set_text(unit.source_for(self.variant) if unit else "")
        # Switching units starts a new undo history, so undo cannot mix separate messages.
        self.target_buffer.set_enable_undo(False)
        self.target_buffer.set_text(unit.targets[self.variant] if unit else "")
        self.target_buffer.set_enable_undo(True)
        self.context_label.set_label((unit.context or "Ingen kontext angiven") if unit else "Ingen sträng vald")
        self.notes.set_label("\n".join(filter(None, [unit.notes, unit.references])) if unit else "")
        self.variants.set_visible(bool(unit and len(unit.targets) > 1))
        self.loading = False
        self.update_editor_status()
        self.refresh_suggestions()
        self.schedule_check()

    def target_changed(self, _buffer):
        if self.loading or not self.unit:
            return
        self.unit.edit(self.variant, self.target_buffer.get_text(*self.target_buffer.get_bounds(), True))
        selected = self.selection.get_selected_item()
        if selected:
            selected.emit("changed")
        self.update_editor_status()
        self.schedule_check()

    def update_editor_status(self):
        if self.unit:
            self.unit_status.set_label(self.unit.status)
            self.length_label.set_label(f"{len(self.unit.targets[self.variant])} tecken")
            self.set_title(("● " if self.catalog.dirty else "") + self.catalog.name + " — Ordverk")

    def copy_source(self):
        if self.unit and not self.unit.source_is_key:
            self.set_target(self.unit.source_for(self.variant))

    def set_target(self, text):
        if self.unit:
            self.target_buffer.begin_user_action()
            self.target_buffer.delete(*self.target_buffer.get_bounds())
            self.target_buffer.insert(self.target_buffer.get_start_iter(), text)
            self.target_buffer.end_user_action()

    def schedule_check(self):
        self.check_generation += 1
        self.check_meter = None
        self.check_bar.set_visible(False)
        generation = self.check_generation
        if hasattr(self, "check_timer") and self.check_timer:
            GLib.source_remove(self.check_timer)
            self.check_timer = None
        if not self.unit:
            return
        self.check_timer = GLib.timeout_add(450, self.run_check, generation)

    def run_check(self, generation):
        self.check_timer = None
        self.update_statistics()
        if not self.unit:
            return False
        unit, variant = copy.deepcopy(self.unit), self.variant
        if self.check_future:
            self.check_future.cancel()
        future = self.check_executor.submit(self.quality.check, unit, variant)
        self.check_future = future
        self.check_meter = ProgressState()
        self.start_progress_timer()

        def done(f):
            if f.cancelled():
                return
            def display():
                if generation != self.check_generation:
                    return
                self.check_meter = None
                self.check_bar.set_visible(False)
                try:
                    self.show_issues(f.result())
                except Exception as exc:
                    clear(self.issue_box)
                    self.issue_box.append(label(f"Kontrollen kunde inte köras: {exc}", "warning", wrap=True))
            self.dispatch(display)
        future.add_done_callback(done)
        return False

    def show_issues(self, issues):
        clear(self.issue_box)
        if not issues:
            self.issue_box.append(label("Inga avvikelser hittades av de aktiva kontrollerna.", "success", wrap=True))
        for issue in issues[:30]:
            line = label(f"{issue.tool} · {issue.message}", "error" if issue.severity == "error" else
                         "warning" if issue.severity == "warning" else "dim-label", wrap=True)
            line.set_tooltip_text(issue.rule)
            self.issue_box.append(line)

    def refresh_suggestions(self):
        if not hasattr(self, "memory_box"):
            return
        clear(self.memory_box)
        clear(self.term_box)
        self.suggestion_generation += 1
        generation = self.suggestion_generation
        self.suggestion_meter = None
        if not self.unit:
            return
        unit = self.unit
        if unit.source_is_key:
            self.memory_box.append(label("Koppla käll-JSON via menyn för att söka på rätt källtext.", "dim-label", wrap=True))
            return
        source, context = unit.source_for(self.variant), unit.context
        self.suggestion_meter = ProgressState()
        self.start_progress_timer()
        future = self.executor.submit(lambda: (self.store.memory(source, context, 8), self.store.terminology(source, 8)))
        def finish(f):
            def display():
                if generation != self.suggestion_generation:
                    return
                self.suggestion_meter = None
                self.suggestion_bar.set_visible(False)
                try:
                    self.show_suggestions(*f.result())
                except Exception as exc:
                    self.memory_box.append(label(f"Språkstödet kunde inte läsas: {exc}", wrap=True))
            self.dispatch(display)
        future.add_done_callback(finish)

    def show_suggestions(self, matches, terms):
        grouped = {}
        for match in matches:
            grouped.setdefault((match.source, match.target), []).append(match)
        for group in list(grouped.values())[:3]:
            match = group[0]
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            origin = match.origin if len(group) == 1 else f"{len(group)} minneskällor"
            provenance = label(f"{match.score:.0%} likhet · {origin}", "caption", wrap=True)
            provenance.set_tooltip_text("\n".join(m.origin + " · " + m.context for m in group))
            card.append(provenance)
            card.append(label(match.source, "dim-label", wrap=True))
            card.append(label(match.target, wrap=True))
            if match.context:
                card.append(label(match.context[:200], "caption", wrap=True))
            use = button("Använd förslag", lambda text=match.target: self.use_suggestion(text, "memory"))
            use.set_halign(Gtk.Align.START)
            card.append(use)
            self.memory_box.append(card)
        if not matches:
            self.memory_box.append(label("Inga minnesträffar. Resurser hämtas via inställningarna.", "dim-label", wrap=True))
        for term in terms:
            self.term_box.append(label(f"{term.source} → {term.target}", wrap=True))
            consensus = f"{term.score:.0%} konsensus" if term.score is not None else "Konsensus inte angiven"
            self.term_box.append(label(f"{consensus} · {term.origin}", "caption", wrap=True))
        if not terms:
            self.term_box.append(label("Inga matchande termer.", "dim-label"))

    def request_ai(self):
        if not self.unit:
            return
        if not self.settings.base_url or not self.settings.model:
            Preferences(self).present()
            self.toast("Ange API-adress och modell för att få AI-förslag.")
            return
        unit, variant, revision = self.unit, self.variant, self.unit.revision
        snapshot = copy.deepcopy(unit)

        def done(proposal):
            if self.unit is not unit or unit.revision != revision or self.variant != variant:
                self.toast("Strängen ändrades under API-anropet. Begär ett nytt förslag.")
                return
            clear(self.proposal_box)
            self.proposal_box.append(label(f"AI-förslag · {proposal.model}", "heading", wrap=True))
            self.proposal_box.append(label(proposal.translation, wrap=True))
            self.proposal_box.append(label(proposal.explanation, "dim-label", wrap=True))
            use = button("Använd AI-förslag", lambda: self.use_suggestion(proposal.translation, "ai"), css="suggested-action")
            errors = [i for i in proposal.issues if i.severity == "error"]
            use.set_sensitive(not errors)
            self.proposal_box.append(use)
            for issue in errors:
                self.proposal_box.append(label(issue.message, "error", wrap=True))
            self.status.set_label("AI-förslaget är klart för granskning")
        self.job("Ber om ett AI-förslag…", lambda cancel: self.translator.suggest(snapshot, variant, cancel), done)

    def mark_reviewed(self):
        if not self.unit:
            return
        if not all(self.unit.targets):
            self.toast("Översätt alla plural- och längdvarianter först.")
            return
        unit, revision, catalog = self.unit, self.unit.revision, self.catalog
        snapshot = copy.deepcopy(unit)

        def check(_cancel):
            return [issue for variant in range(len(snapshot.targets)) for issue in self.quality.check(snapshot, variant)]

        def done(issues):
            if unit.revision != revision:
                self.toast("Strängen ändrades under granskningen. Granska den igen.")
                return
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                self.show_report("Åtgärda dessa fel först", [i.message for i in errors])
                return
            unit.reviewed = True
            unit.revision += 1
            if not unit.source_is_key:
                for variant, target in enumerate(unit.targets):
                    self.store.remember(unit.source_for(variant), target, unit.context)
            self.refresh_files()
            if self.catalog is catalog:
                self.filter_units()
            self.status.set_label("Granskad · sparad som förslag i ditt eget översättningsminne")
        self.job("Kontrollerar alla varianter…", check, done)

    def start_workflow(self, catalogs, purpose, automatic, use_ai):
        if not automatic:
            self.status.set_label("Manuell granskning · välj en sträng" if purpose == "review" else "Manuell översättning · välj en sträng")
        elif purpose == "review":
            self.review_catalogs(catalogs)
        else:
            if use_ai and (not self.settings.base_url or not self.settings.model):
                self.error("Konfigurera API-adress och modell innan automatisk AI-översättning startas.")
                Preferences(self).present()
                return

            def done(result):
                changes, messages = result
                applied, skipped = apply_changes(changes)
                for change in changes:
                    matching = next((u for u in change.catalog.units if u.key == change.key), None)
                    if matching and matching.targets[change.variant] == change.after:
                        self.session_statistics["ai" if change.origin.startswith("AI ·") else "memory"] += 1
                self.refresh_files()
                self.filter_units()
                self.update_statistics()
                self.status.set_label(f"{applied} förslag tillämpade · {skipped} ändrade strängar hoppades över · spara när du är klar")
                if messages:
                    self.show_report("Automatiskt arbete", messages)
            self.job("Översätter tomma strängar…", lambda cancel: batch_translate(
                catalogs, self.store, self.quality, self.translator if use_ai else None,
                limit=self.settings.automatic_limit, cancel=cancel, progress=self.progress_message), done)

    def pretranslate(self):
        if self.catalog:
            PretranslateDialog(self).present()
        else:
            self.toast("Importera en fil först.")

    def run_pretranslation(self, catalog, selected, method, overwrite):
        return self.job("Tar fram föröversättningar…", lambda cancel: batch_translate(
            [catalog], self.store, self.quality, self.translator if method != "resources" else None,
            selected=selected, method=method, overwrite=overwrite, limit=None, cancel=cancel,
            progress=self.progress_message), self.preview_changes)

    def preview_changes(self, result):
        changes, messages = result
        dialog = Adw.Window(transient_for=self, title="Granska föröversättningar", default_width=800, default_height=620)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.append(Adw.HeaderBar())
        root.append(label(f"{len(changes)} förslag · ändringar sparas när du väljer Spara", "heading", wrap=True))
        text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            left_margin=20, right_margin=20)
        catalogs = {id(c.catalog): c.catalog for c in changes}
        units = {(identity, u.key): u for identity, catalog in catalogs.items() for u in catalog.units}
        rows = [f"{units[(id(c.catalog), c.key)].source_for(c.variant)}\nFöre: {c.before or '(tomt)'}\nFörslag: {c.after}\n{c.origin}"
                + ("\n" + "\n".join(i.message for i in c.issues) if c.issues else "") for c in changes]
        text.get_buffer().set_text("\n\n".join(rows + messages) or "Inga nya förslag. Kontrollera källtexter och tillgängliga språkresurser.")
        root.append(scroll(text, vexpand=True))
        def apply():
            applied, skipped = apply_changes(changes)
            self.refresh_files()
            self.filter_units()
            self.update_statistics()
            self.toast(f"{applied} förslag tillämpades. {skipped} strängar hade ändrats och hoppades över.")
            dialog.close()
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_bottom=18, margin_end=18)
        controls.append(button("Stäng", dialog.close))
        use = button("Tillämpa förslagen", apply, css="suggested-action")
        use.set_sensitive(bool(changes))
        controls.append(use)
        root.append(controls)
        dialog.set_content(root)
        dialog.present()

    def review_catalogs(self, catalogs):
        def run(cancel):
            results = []
            for original in catalogs:
                if cancel.is_set():
                    break
                catalog = copy.deepcopy(original)
                self.progress_message("Granskar " + catalog.name)
                results.extend((catalog.name, issue) for issue in self.quality.catalog(catalog, cancel=cancel, progress=self.progress_message))
            return results

        def done(results):
            self.diagnostics = [{"file": name, **asdict(issue)} for name, issue in results]
            self.report_ready = True
            self.show_report("Granskning av kataloger", [f"{name}: {i.message}" for name, i in results]
                             or ["De aktiva kvalitetskontrollerna hittade inga avvikelser."])
            self.status.set_label(f"Granskningen är klar · {len(results)} avvikelser")
        self.job("Granskar kataloger…", run, done)

    def show_report(self, title, messages):
        window = Adw.Window(transient_for=self, modal=False, title=title, default_width=720, default_height=480)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Adw.HeaderBar())
        text = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            left_margin=20, right_margin=20, top_margin=16, bottom_margin=20)
        text.get_buffer().set_text("\n\n".join(messages[:1000]) + (f"\n\n… och {len(messages)-1000} till." if len(messages) > 1000 else ""))
        box.append(scroll(text, vexpand=True))
        window.set_content(box)
        window.present()

    def save(self, save_as=False):
        catalog = self.catalog
        if not catalog or self.busy:
            if self.busy:
                self.toast("Avsluta pågående arbete innan du sparar.")
            return

        def write(path):
            def finish(overwrite=False):
                self.stack.set_sensitive(False)
                def operation(cancel):
                    if cancel.is_set():
                        return None
                    snapshot = copy.deepcopy(catalog)
                    snapshot.save(path, overwrite=overwrite)
                    return snapshot
                def saved(result):
                    if result is None:
                        self.status.set_label("Sparningen avbröts.")
                        return
                    catalog.__dict__.update(result.__dict__)
                    self.refresh_files()
                    if self.catalog is catalog:
                        self.filter_units()
                    self.toast("Filen har sparats. Tidigare innehåll finns som säkerhetskopia.")
                    self.status.set_label("Sparad · " + str(path))
                    self.update_statistics()
                self.job("Sparar filen…", operation, saved)
            if Path(path).exists() and Path(path).absolute() != catalog.path:
                dialog = Adw.MessageDialog(transient_for=self, heading="Ersätta den befintliga filen?", body=str(path))
                dialog.add_response("cancel", "Avbryt")
                dialog.add_response("replace", "Ersätt")
                dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.connect("response", lambda _d, response: finish(True) if response == "replace" else None)
                dialog.present()
            else:
                finish()
        if save_as or not catalog.path or catalog.ext == ".pot":
            name = Path(catalog.name).name
            if catalog.ext == ".pot":
                name = Path(name).stem + ".sv.po"
            self.file_dialog("Spara översättning", write, save_name=name)
        else:
            write(catalog.path)

    def choose_reference(self):
        catalog = self.catalog
        if not catalog:
            return
        def attach(path):
            snapshot = copy.deepcopy(catalog)
            def read(_):
                return snapshot.attach_reference(path)
            def done(count):
                for source, target in zip(snapshot.units, catalog.units):
                    if source.source != target.source or source.source_is_key != target.source_is_key:
                        target.source, target.source_is_key = source.source, source.source_is_key
                        target.reviewed = False
                        target.revision += 1
                catalog.reference = snapshot.reference
                self.filter_units()
                self.toast(f"Källtext kopplad för {count} strängar. Spara filen för att behålla kopplingen.")
            self.job("Läser källtexter…", read, done)
        self.file_dialog("Koppla engelsk käll-JSON", attach, patterns=["*.json"])

    def choose_resource(self, kind):
        def load(path):
            operation = self.store.import_po if kind == "memory" else self.store.import_terms
            self.job("Indexerar språkresurs…", lambda cancel: operation(path, cancel=cancel),
                     lambda count: (self.toast(f"{count} poster importerades."), self.refresh_suggestions()))
        self.file_dialog("Importera översättningsminne" if kind == "memory" else "Importera terminologi", load,
                         patterns=["*.po"] if kind == "memory" else ["*.tbx", "*.csv"])

    def export_report(self):
        if not self.report_ready:
            self.toast("Kör en filgranskning först. Rapporten exporterar den senaste körningen.")
            return
        self.file_dialog("Exportera senaste granskningsrapport", lambda path: self.job("Exporterar rapport…", lambda _: atomic_write(
            Path(path), json.dumps(self.diagnostics, ensure_ascii=False, indent=2).encode(), 0o644), lambda _: self.toast("Rapporten har exporterats.")),
            save_name="ordverk-granskning.json")

    def export_memory(self):
        self.file_dialog("Exportera eget granskat minne", lambda path: self.job("Exporterar minne…", lambda _: self.store.export_memory(path),
                                                                               lambda _: self.toast("Minnet har exporterats.")), save_name="ordverk-minne.po")

    def use_suggestion(self, text, kind):
        if self.unit and self.unit.targets[self.variant] != text:
            self.session_statistics[kind] += 1
            self.set_target(text)

    def update_statistics(self):
        total = statistics(self.catalogs)
        for key, widget in self.statistics_labels.items():
            widget.set_label(f"{getattr(total, key):,}".replace(",", " "))
        self.completion.set_fraction(total.fraction)
        self.completion.set_text(f"{total.fraction:.0%} översatt")
        self.completion.set_tooltip_text("Alla importerade filer. En sträng räknas som översatt när alla dess plural- och längdvarianter är ifyllda.")

    def show_statistics(self):
        total = statistics(self.catalogs)
        window = Adw.Window(transient_for=self, title="Statistik", default_width=850, default_height=600)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_start=24, margin_end=24, margin_top=18, margin_bottom=24)
        box.append(label("Arbetet i siffror", "title-1"))
        box.append(label(f"{total.files} filer · {total.total} strängar · {total.source_words} ord i källtexterna", "dim-label"))
        box.append(label(f"{total.translated} översatta  ·  {total.reviewed} granskade  ·  {total.needs_review} att granska  ·  {total.remaining} återstår", wrap=True))
        box.append(label(f"Plural- och längdvarianter: {total.translated_variants} av {total.variants} ifyllda."))
        box.append(label(f"Tillämpade förslag denna session: {self.session_statistics['memory']} från minnet, {self.session_statistics['ai']} från AI."))
        grid = Gtk.Grid(column_spacing=22, row_spacing=12)
        for col, title in enumerate(["Fil", "Strängar", "Översatta", "Granskade", "Kvar"]):
            grid.attach(label(title, "heading"), col, 0, 1, 1)
        for row, catalog in enumerate(self.catalogs, 1):
            counts = statistics([catalog])
            for col, value in enumerate([catalog.name, counts.total, counts.translated, counts.reviewed, counts.remaining]):
                grid.attach(label(str(value)), col, row, 1, 1)
        box.append(grid)
        box.append(label("Statistiken omfattar osparade ändringar. Granskad betyder manuellt godkänd. JSON:s granskningsstatus lagras lokalt i .ordverk när filen sparas.", "dim-label", wrap=True))
        root.append(scroll(box, vexpand=True))
        window.set_content(root)
        window.present()

    def move(self, delta):
        count = self.unit_store.get_n_items()
        if count:
            self.selection.set_selected(max(0, min(count - 1, self.selection.get_selected() + delta)))

    def about(self):
        dialog = Adw.AboutWindow(transient_for=self, modal=True, application_name="Ordverk", version=__version__,
                                  application_icon="io.github.yeager.Ordverk",
                                  comments="En svensk översättningsverkstad.\nGTK4 · libadwaita · Python",
                                  license_type=Gtk.License.GPL_3_0)
        for name in ("swedish-tm", "swedish-foss-terminology", "l10n-lint", "svlang", "hunspell-sv", "aspell-sv"):
            dialog.add_link(name, "https://github.com/yeager/" + name)
        dialog.present()

    def on_close(self, *_):
        if any(c.dirty for c in self.catalogs):
            dialog = Adw.MessageDialog(transient_for=self, heading="Du har osparade ändringar",
                                       body="Spara de ändrade katalogerna innan du stänger om du vill behålla arbetet.")
            dialog.add_response("cancel", "Fortsätt arbeta")
            dialog.add_response("discard", "Stäng utan att spara")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda _d, response: self.shutdown() if response == "discard" else None)
            dialog.present()
            return True
        self.shutdown()
        return True

    def shutdown(self):
        self.closed = True
        self.job_cancel.set()
        self.resource_cancel.set()
        self.check_generation += 1
        if self.progress_timer:
            GLib.source_remove(self.progress_timer)
            self.progress_timer = None
        if getattr(self, "check_timer", None):
            GLib.source_remove(self.check_timer)
            self.check_timer = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.check_executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.yeager.Ordverk", flags=Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name("Ordverk")

    def do_activate(self):
        window = self.get_active_window()
        if not window:
            window = Window(self)
        window.present()

    def do_open(self, files, *_):
        self.activate()
        self.get_active_window().import_paths([file.get_path() or file.get_uri() for file in files])


def run():
    return Application().run(sys.argv)
