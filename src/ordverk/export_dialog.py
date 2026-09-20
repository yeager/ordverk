"""Choose a local export format, preview its contents, then write copies."""
import copy
from pathlib import Path

from .dialogs import Adw, Gtk
from .exporters import FORMATS, prepare, write_exports
from .importers import check_cancel
from .remote_dialogs import preview


class ExportDialog(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Exportera översättningar",
                         default_width=680, default_height=480)
        self.parent = parent
        page = Adw.PreferencesPage(title="Export")
        self.add(page)
        group = Adw.PreferencesGroup(title="Exportera arbetskopior", description="Osparade ändringar ingår i exporten.")
        page.add(group)
        self.scope = Adw.ComboRow(title="Filer", model=Gtk.StringList.new([
            "Aktuell fil", f"Alla öppnade filer ({len(parent.catalogs)})"]))
        group.add(self.scope)
        self.format = Adw.ComboRow(title="Format", model=Gtk.StringList.new([title for _, title, _ in FORMATS]))
        group.add(self.format)
        self.help = Adw.ActionRow(title="Exportens innehåll")
        group.add(self.help)
        self.format.connect("notify::selected", lambda *_: self.update_help())
        self.update_help()
        group.add(Adw.ActionRow(title="Originalfilerna behålls",
                               subtitle="Export skapar kopior. Befintliga filnamn får ett numrerat tillägg. Sparningsstatusen ändras inte."))
        start = Gtk.Button(label="Välj exportmapp och förhandsgranska", css_classes=["suggested-action"], halign=Gtk.Align.CENTER)
        start.connect("clicked", lambda _: self.start())
        group.add(start)

    def update_help(self):
        kind = FORMATS[self.format.get_selected()][0]
        self.help.set_subtitle(
            "En unified diff jämför med innehållet vid import, även efter att du har sparat."
            if kind == "diff" else "Filens struktur, metadata och pluralformer bevaras."
            if kind == "original" else
            "Vid formatbyte blir varje plural- eller längdvariant en separat post med formen i kontexten. "
            "Inlinekoder återges som text. Välj originalformatet för att behålla all formatspecifik struktur.")

    def start(self):
        catalogs = list(self.parent.catalogs) if self.scope.get_selected() else [self.parent.catalog]
        kind = FORMATS[self.format.get_selected()][0]
        self.close()
        self.parent.file_dialog("Välj exportmapp", lambda directory: self.prepare(catalogs, kind, directory), folder=True)

    def prepare(self, catalogs, kind, directory):
        if self.parent.busy:
            self.parent.toast("Låt det pågående arbetet bli klart innan du exporterar.")
            return
        self.parent.stack.set_sensitive(False)
        def operation(cancel):
            result = []
            for index, catalog in enumerate(catalogs, 1):
                check_cancel(cancel)
                result.append(prepare(copy.deepcopy(catalog), kind, cancel))
                self.parent.progress_message(f"Förbereder {catalog.name}", index, len(catalogs))
            return result
        def done(exports):
            summary = [f"Exportmapp: {directory}", f"{len(exports)} filer", ""]
            summary.extend(f"{item.name} · {len(item.data):,} byte".replace(",", " ") for item in exports)
            if kind == "diff":
                summary.extend(["", "Jämförelse mot filernas innehåll vid import:"])
                for item in exports[:3]:
                    text = item.data[:160000].decode("utf-8", errors="replace")
                    summary.append(f"\n{item.name}\n" + (text[:40000] or "Inga ändringar."))
                    if len(text) > 40000 or len(item.data) > 160000:
                        summary.append("… förhandsvisningen är förkortad. Hela diffen exporteras.")
                if len(exports) > 3:
                    summary.append("Förhandsvisningen visar de första tre diffarna. Alla filer exporteras.")
            def write():
                return self.parent.job("Exporterar filer…", lambda cancel: write_exports(
                    exports, directory, cancel, self.parent.progress_message),
                    lambda paths: self.parent.show_report("Exporten är klar", [f"{len(paths)} filer exporterades.",
                        *[str(Path(path).absolute()) for path in paths]]))
            self.preview = preview(self.parent, "Förhandsgranska export", "\n".join(summary), "Exportera filer", write)
        return self.parent.job("Förbereder export…", operation, done)
