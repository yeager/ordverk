"""Simple defaults, advanced controls, and an optional import guide."""
from __future__ import annotations

import copy
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .settings import data_dir, save_key


def entry(group, title, text="", password=False):
    row = Adw.PasswordEntryRow(title=title) if password else Adw.EntryRow(title=title)
    row.set_text(text)
    group.add(row)
    return row


def switch(group, title, subtitle, active):
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    control = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
    row.add_suffix(control)
    row.set_activatable_widget(control)
    group.add(row)
    return control


class Preferences(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Inställningar", default_width=690, default_height=720)
        self.parent = parent
        self.settings = copy.deepcopy(parent.settings)
        simple = Adw.PreferencesPage(title="Enkelt", icon_name="preferences-system-symbolic")
        advanced = Adw.PreferencesPage(title="Avancerat", icon_name="applications-engineering-symbolic")
        self.add(simple)
        self.add(advanced)
        group = Adw.PreferencesGroup(title="Arbetssätt", description="Svenska är målspråk. Du kan alltid redigera varje sträng.")
        simple.add(group)
        self.guide = switch(group, "Visa importguiden", "Fråga hur importerade filer ska behandlas.", self.settings.show_import_guide)
        self.updates = switch(group, "Uppdatera språkresurser automatiskt", "Kontrollera vid varje start. Cachade data fungerar utan nät.", self.settings.auto_update_resources)
        self.project = entry(group, "Projektets sammanhang", self.settings.project_context)
        self.domain = entry(group, "Domän / ekosystem", self.settings.domain)
        ai = Adw.PreferencesGroup(title="AI-anslutning", description="Valfritt. Källtext, aktuell översättning, kommentarer och relevanta språkresurser skickas till tjänsten när du begär AI-hjälp.")
        simple.add(ai)
        self.endpoint = entry(ai, "API-basadress · exempel: http://localhost:11434/v1", self.settings.base_url)
        self.model = entry(ai, "Modellnamn", self.settings.model)
        self.key = entry(ai, "API-nyckel för den här sessionen", "", password=True)
        self.remember_key = switch(ai, "Spara nyckeln i systemnyckelringen", "Annars används den bara i denna session eller från miljövariabeln.", False)
        simple.add(self.save_group())

        resources = Adw.PreferencesGroup(title="Språkresurser", description=f"Cache: {data_dir()}\nFörsta hämtningen av alla resurser är ungefär 170 MB. CSV bevarar även de två termer som TBX inte kan representera.")
        advanced.add(resources)
        self.components = {}
        for key, title, description in [
            ("tm", "Swedish Translation Memory", "Alla 15 ekosystem. Exakta och liknande träffar med källhänvisning."),
            ("terms", "Swedish FOSS Terminology", "Senaste fullständiga CSV-exporten; egna TBX-filer kan importeras."),
            ("hunspell", "Hunspell-sv", "Svenska böjningar och sammansättningar."),
            ("aspell", "Aspell-sv", "Kompletterande ordkontroll; ordlistan är en förhandsversion."),
        ]:
            self.components[key] = switch(resources, title, description, key in self.settings.resource_components)
        row = Adw.ActionRow(title="Kontrollera uppdateringar nu")
        button = Gtk.Button(label="Uppdatera", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _: self.update_now())
        row.add_suffix(button)
        resources.add(row)
        engines = Adw.PreferencesGroup(title="Granskningsmotorer", description="l10n-lint och svlang följer den installerade programversionen. Endast statiska språkdata uppdateras automatiskt.")
        advanced.add(engines)
        self.hunspell = switch(engines, "Använd Hunspell", "Okända ord blir råd att granska.", self.settings.use_hunspell)
        self.aspell = switch(engines, "Använd Aspell", "En extra bedömning; kan avvisa korrekta nya sammansättningar.", self.settings.use_aspell)
        self.hunpath = entry(engines, "Egen Hunspell-ordlista (sökväg utan .dic)", self.settings.hunspell_dictionary)
        self.asppath = entry(engines, "Egen Aspell-katalog", self.settings.aspell_directory)
        automation = Adw.PreferencesGroup(title="Automatiskt arbete", description="Fyller endast tomma översättningar. Förslag med kvalitetsfel tillämpas inte. Resultat sparas när du väljer Spara.")
        advanced.add(automation)
        self.auto = switch(automation, "Automatiskt arbete som standard", "Används när importguiden är avstängd.", self.settings.import_automatic)
        self.auto_ai = switch(automation, "Tillåt AI i automatiskt arbete", "API-anrop kan medföra kostnader. Exakta, entydiga minnesträffar används först.", self.settings.automatic_use_ai)
        self.limit = entry(automation, "Högst antal strängar per automatisk körning", str(self.settings.automatic_limit))
        self.env = entry(automation, "Miljövariabel för API-nyckeln", self.settings.api_key_env)
        advanced.add(self.save_group())

    def save_group(self):
        group = Adw.PreferencesGroup()
        button = Gtk.Button(label="Spara inställningar", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _: self.save())
        group.add(button)
        return group

    def collect(self):
        limit = int(self.limit.get_text())
        if not 1 <= limit <= 10000:
            raise ValueError("Välj en gräns mellan 1 och 10 000 strängar.")
        settings = self.settings
        settings.show_import_guide = self.guide.get_active()
        settings.auto_update_resources = self.updates.get_active()
        settings.project_context = self.project.get_text()
        settings.domain = self.domain.get_text()
        settings.base_url = self.endpoint.get_text().strip()
        settings.model = self.model.get_text().strip()
        settings.api_key_env = self.env.get_text().strip()
        settings.hunspell_dictionary = self.hunpath.get_text().strip()
        settings.aspell_directory = self.asppath.get_text().strip()
        settings.use_hunspell, settings.use_aspell = self.hunspell.get_active(), self.aspell.get_active()
        settings.import_automatic = self.auto.get_active()
        settings.automatic_use_ai = self.auto_ai.get_active()
        settings.automatic_limit = limit
        settings.resource_components = [key for key, control in self.components.items() if control.get_active()]
        return settings

    def save(self):
        try:
            settings = self.collect()
        except ValueError as exc:
            self.parent.error(str(exc), parent=self)
            return
        key, remember = self.key.get_text(), self.remember_key.get_active()
        def write(_cancel):
            if remember:
                save_key(settings.base_url, key)
            settings.save()
        def done(_):
            if self.parent.settings.base_url != settings.base_url:
                self.parent.session_key = ""
            self.parent.settings = settings
            if key:
                self.parent.session_key = key
            self.parent.refresh_services()
            self.parent.toast("Inställningarna har sparats.")
        if self.parent.job("Sparar inställningar…", write, done):
            self.close()

    def update_now(self):
        self.parent.update_resources([key for key, control in self.components.items() if control.get_active()])


class ImportGuide(Adw.Window):
    def __init__(self, parent, catalogs):
        super().__init__(transient_for=parent, modal=True, title="Hur vill du arbeta?", default_width=550)
        self.parent, self.catalogs = parent, catalogs
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                          margin_top=18, margin_bottom=24, margin_start=24, margin_end=24)
        root.append(content)
        title = Gtk.Label(label="Gör plats för bra svenska.", xalign=0, css_classes=["title-1"])
        content.append(title)
        content.append(Gtk.Label(label=f"{len(catalogs)} filer · {sum(len(c.units) for c in catalogs):,} strängar".replace(",", " "), xalign=0))
        group = Adw.PreferencesGroup(title="Vad vill du göra med importen?")
        content.append(group)
        foreign = any(c.language and not c.language.lower().startswith("sv") for c in catalogs)
        self.new_copy = switch(group, "Skapa tomma svenska arbetskopior", "För källfiler eller kataloger på annat språk. Originalen behålls.", foreign)
        if foreign:
            self.new_copy.set_sensitive(False)
        row = Adw.ActionRow(title="Uppgift")
        self.purpose = Gtk.DropDown.new_from_strings(["Översätta till svenska", "Granska befintliga översättningar"])
        self.purpose.set_selected(1 if parent.settings.import_purpose == "review" else 0)
        self.purpose.set_valign(Gtk.Align.CENTER)
        row.add_suffix(self.purpose)
        group.add(row)
        self.automatic = switch(group, "Arbeta automatiskt", "Av: öppna i redigeraren och arbeta sträng för sträng.", parent.settings.import_automatic)
        self.ai = switch(group, "Använd AI när minnet inte räcker", "Skickar strängar till din konfigurerade API-tjänst. Kan medföra kostnader.", parent.settings.automatic_use_ai)
        self.purpose.connect("notify::selected", lambda *_: self.update_ai())
        self.automatic.connect("notify::active", lambda *_: self.update_ai())
        self.update_ai()
        content.append(Gtk.Label(label="Automatiska förslag fyller bara tomma fält och markeras för granskning. Ingenting skrivs till originalfilerna förrän du sparar.",
                                 wrap=True, xalign=0, css_classes=["dim-label"]))
        self.remember = Gtk.CheckButton(label="Kom ihåg valen och hoppa över guiden nästa gång")
        content.append(self.remember)
        start = Gtk.Button(label="Börja arbeta", css_classes=["suggested-action", "pill"], halign=Gtk.Align.END)
        start.connect("clicked", lambda _: self.start())
        content.append(start)
        self.set_content(root)

    def update_ai(self):
        self.ai.set_sensitive(self.automatic.get_active() and self.purpose.get_selected() == 0)

    def start(self):
        purpose = "review" if self.purpose.get_selected() == 1 else "translate"
        automatic = self.automatic.get_active()
        ai = self.ai.get_active()
        catalogs = self.catalogs
        create_copy = self.new_copy.get_active()
        if self.remember.get_active():
            self.parent.settings.show_import_guide = False
            self.parent.settings.import_purpose = purpose
            self.parent.settings.import_automatic = automatic
            self.parent.settings.automatic_use_ai = ai
            self.parent.settings.save()
        self.close()
        def done(copies):
            if create_copy:
                self.parent.add_copies(copies)
            self.parent.start_workflow(copies, purpose, automatic, ai)
        if create_copy:
            self.parent.job("Skapar svenska arbetskopior…", lambda _: [c.swedish_copy() for c in catalogs], done)
        else:
            done(catalogs)


class PretranslateDialog(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Föröversätt", default_width=640, default_height=600)
        self.parent, self.catalog, self.unit = parent, parent.catalog, parent.unit
        page = Adw.PreferencesPage(title="Föröversättning")
        self.add(page)
        group = Adw.PreferencesGroup(title=self.catalog.name, description="Förslagen visas för granskning innan du tillämpar dem. Alla plural- och längdvarianter ingår.")
        page.add(group)
        self.marked = {(id(self.catalog), u.key) for u in self.catalog.units if (id(self.catalog), u.key) in parent.marked}
        self.scope = Adw.ComboRow(title="Omfattning", model=Gtk.StringList.new([
            "Hela filen", "Aktuell sträng", f"Markerade strängar ({len(self.marked)})"]))
        self.scope.set_selected(2 if self.marked else 0)
        group.add(self.scope)
        self.method = Adw.ComboRow(title="Översätt med", model=Gtk.StringList.new([
            "Översättningsminne och ordlistor", "AI", "Språkresurser först, sedan AI"]))
        group.add(self.method)
        self.overwrite = switch(group, "Ersätt även ifyllda översättningar", "Av: fyll bara tomma fält. På: visa också ersättningsförslag.", False)
        group.add(Adw.ActionRow(title="Språkresurser", subtitle="Entydiga hela träffar används. Ord från en termbank sätts inte ihop till meningar."))
        group.add(Adw.ActionRow(title="AI", subtitle="Valda källtexter och relevant språkstöd skickas till din API-tjänst. Anrop kan medföra kostnader."))
        start = Gtk.Button(label="Ta fram förslag", css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        start.connect("clicked", lambda _: self.start())
        page.add(Adw.PreferencesGroup())
        group.add(start)

    def start(self):
        scope = self.scope.get_selected()
        selected = None if scope == 0 else ({(id(self.catalog), self.unit.key)} if scope == 1 and self.unit else self.marked)
        if selected is not None and not selected:
            self.parent.error("Välj minst en sträng först.", parent=self)
            return
        method = ("resources", "ai", "combined")[self.method.get_selected()]
        if method != "resources" and (not self.parent.settings.base_url or not self.parent.settings.model):
            self.parent.error("Ange API-adress och modell i inställningarna först.", parent=self)
            return
        if self.parent.run_pretranslation(self.catalog, selected, method, self.overwrite.get_active()):
            self.close()
