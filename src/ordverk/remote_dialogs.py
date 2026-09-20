"""Connection profiles and explicit previews for outbound files."""
import copy
from pathlib import Path

from .dialogs import Adw, Gtk, entry, switch
from .delivery import open_mail, prepare_mail, tp_defaults
from .importers import ImportResult
from .remotes import Client, DEFAULT_URLS, PROVIDERS, Profile, load_profiles, prepare_export, save_profile
from .settings import save_key, secure_keyring


def preview(parent, title, text, caption, action):
    window = Adw.Window(transient_for=parent, title=title, default_width=800, default_height=640)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.append(Adw.HeaderBar())
    view = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        left_margin=20, right_margin=20, top_margin=12)
    view.get_buffer().set_text(text)
    scroll = Gtk.ScrolledWindow(vexpand=True)
    scroll.set_child(view)
    box.append(scroll)
    controls = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_end=20, margin_bottom=20)
    close = Gtk.Button(label="Stäng")
    close.connect("clicked", lambda _: window.close())
    controls.append(close)
    send = Gtk.Button(label=caption, css_classes=["suggested-action"])
    def run(_):
        if action():
            window.close()
    send.connect("clicked", run)
    controls.append(send)
    box.append(controls)
    window.set_content(box)
    window.present()
    return window


class Connections(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Anslutningar · import och export", default_width=730, default_height=800)
        self.parent, self.profiles = parent, load_profiles()
        self.current = None
        self.loading = False
        page = Adw.PreferencesPage(title="Anslutning")
        self.add(page)
        group = Adw.PreferencesGroup(title="Välj projekt och översättningsfil")
        page.add(group)
        self.saved = Adw.ComboRow(title="Sparad anslutning", model=Gtk.StringList.new(["Ny anslutning"] + [p.name or p.description for p in self.profiles]))
        group.add(self.saved)
        self.provider = Adw.ComboRow(title="Tjänst", model=Gtk.StringList.new(list(PROVIDERS.values())))
        group.add(self.provider)
        self.name = entry(group, "Namn på anslutningen")
        self.base = entry(group, "API-basadress", DEFAULT_URLS["github"])
        self.owner = entry(group, "Ägare / organisation")
        self.project = entry(group, "Förråd")
        self.resource = entry(group, "Filens sökväg i förrådet")
        self.branch = entry(group, "Git-gren", "main")
        self.language = entry(group, "Svensk språkkod", "sv")
        self.filename = entry(group, "Lokalt filnamn med formatändelse", "sv.po")
        self.token = entry(group, "API-nyckel · tomt använder sparad sessionsnyckel", password=True)
        self.remember = switch(group, "Spara nyckeln i systemnyckelringen", "Anslutningens övriga val sparas i .ordverk. Nyckeln sparas aldrig i inställningsfilen.", False)
        self.message = entry(group, "Commitmeddelande vid GitHub-export", "Uppdatera svensk översättning")
        self.weblate_method = Adw.ComboRow(title="Export till Weblate", model=Gtk.StringList.new(["Översättningar", "Förslag"]))
        group.add(self.weblate_method)
        self.help = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        group.add(self.help)
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        get = Gtk.Button(label="Importera fil", css_classes=["suggested-action"])
        get.connect("clicked", lambda _: self.start(False))
        controls.append(get)
        put = Gtk.Button(label="Förhandsgranska export")
        put.set_sensitive(parent.catalog is not None)
        put.connect("clicked", lambda _: self.start(True))
        controls.append(put)
        group.add(controls)
        self.provider.connect("notify::selected", lambda *_: self.change_provider())
        self.saved.connect("notify::selected", lambda *_: self.select_profile())
        self.change_provider()

    def change_provider(self):
        provider = list(PROVIDERS)[self.provider.get_selected()]
        if not self.loading:
            self.base.set_text(DEFAULT_URLS[provider])
            self.token.set_text("")
        self.owner.set_visible(provider in {"github", "transifex"})
        self.branch.set_visible(provider == "github")
        self.message.set_visible(provider == "github")
        self.weblate_method.set_visible(provider == "weblate")
        self.project.set_title({"github": "Förråd", "weblate": "Projektets URL-namn", "transifex": "Projektets URL-namn", "crowdin": "Projekt-ID"}[provider])
        self.resource.set_title({"github": "Filens sökväg · exempel: po/sv.po", "weblate": "Komponentens URL-namn", "transifex": "Resursens URL-namn", "crowdin": "Fil-ID"}[provider])
        self.help.set_label({
            "github": "Importera en fil från valfri befintlig gren. Export skapar en commit för filen på den angivna grenen. Nyckeln behöver Contents: write för export.",
            "weblate": "Använd /api i basadressen. Export kan uppdatera översättningar eller lämna förslag; redan godkända motstridiga texter skyddas.",
            "transifex": "Organisation, projekt och resurs anges med sina URL-namn. Filen måste ha samma format som resursen. Export uppdaterar resursens svenska översättningar.",
            "crowdin": "Projekt-ID och fil-ID finns i projektets API. Export lägger in översättningar utan att godkänna dem automatiskt. Enterprise kan ange egen API-basadress."
        }[provider])

    def select_profile(self):
        index = self.saved.get_selected()
        self.current = self.profiles[index - 1] if index else None
        profile = self.current or Profile()
        self.loading = True
        self.provider.set_selected(list(PROVIDERS).index(profile.provider))
        for name in ("name", "base", "owner", "project", "resource", "branch", "language", "filename"):
            getattr(self, name).set_text(getattr(profile, "base_url" if name == "base" else name))
        self.token.set_text("")
        self.change_provider()
        self.loading = False

    def collect(self):
        provider = list(PROVIDERS)[self.provider.get_selected()]
        profile = copy.deepcopy(self.current) if self.current and self.current.provider == provider else Profile()
        profile.provider = provider
        for name in ("name", "base", "owner", "project", "resource", "branch", "language", "filename"):
            setattr(profile, "base_url" if name == "base" else name, getattr(self, name).get_text().strip())
        profile.validate()
        return profile

    def start(self, exporting):
        try:
            profile = self.collect()
        except ValueError as exc:
            self.parent.error(str(exc), parent=self)
            return
        key, remember = self.token.get_text(), self.remember.get_active()
        message = self.message.get_text().strip() or "Uppdatera svensk översättning"
        method = "suggest" if self.weblate_method.get_selected() else "translate"
        catalog = copy.deepcopy(self.parent.catalog) if exporting else None
        def prepare(cancel):
            nonlocal key
            if not key:
                key = self.parent.connection_tokens.get(profile.credential_id, "")
            if not key:
                try:
                    key = secure_keyring().get_password("ordverk", profile.credential_id) or ""
                except Exception:
                    pass
            if remember and key:
                save_key(profile.credential_id, key)
            save_profile(profile)
            client = Client(profile, key, cancel=cancel, progress=self.parent.progress_message)
            return client, prepare_export(client, catalog, self.parent.quality) if exporting else client.import_catalog()
        def done(result):
            client, value = result
            self.parent.connection_tokens[profile.credential_id] = key
            if not exporting:
                self.parent.finish_import(ImportResult(catalogs=[value]))
                return
            detail = value.preview + "\n\n" + "\n".join(i.message for i in value.issues)
            detail += "\n\nExporten skickar innehållet som visas här, inklusive osparade ändringar."
            if profile.provider != "github":
                detail += " Fjärrfilen jämförs igen före sändning; tjänsten kan fortfarande ta emot samtidiga ändringar."
            def send():
                def run(cancel):
                    client.cancel = cancel
                    return client.upload(value.data, expected_hash=value.expected_hash, revision=value.revision, message=message, method=method)
                return self.parent.job("Exporterar översättningen…", run, lambda text: self.parent.show_report("Exportresultat", [text]))
            preview(self.parent, "Granska export till " + PROVIDERS[profile.provider], detail,
                    "Skapa commit på GitHub" if profile.provider == "github" else "Skicka till " + PROVIDERS[profile.provider], send)
        if self.parent.job("Förbereder anslutning…", prepare, done):
            self.close()


class MailDialog(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Skicka översättning via e-post", default_width=680, default_height=650)
        self.parent, self.catalog = parent, copy.deepcopy(parent.catalog)
        page = Adw.PreferencesPage(title="E-post")
        self.add(page)
        group = Adw.PreferencesGroup(title="Översättningsfil som bilaga", description="Translation Projects robot är förvald. Meddelandet öppnas som utkast i ditt e-postprogram; där väljer du avsändare och skickar.")
        page.add(group)
        to, subject = tp_defaults(self.catalog)
        self.to = entry(group, "Till", to)
        self.subject = entry(group, "Ämne", subject)
        self.body = entry(group, "Meddelande", "Svensk översättning bifogas.")
        group.add(Adw.ActionRow(title="Bilaga", subtitle=Path(self.catalog.name).name))
        group.add(Adw.ActionRow(title="Translation Project", subtitle="Robotens vanliga filnamn är paket-version.sv.po. Byt filnamn med Spara som om det behövs."))
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        start = Gtk.Button(label="Förhandsgranska e-post", css_classes=["suggested-action"])
        start.connect("clicked", lambda _: self.start())
        controls.append(start)
        group.add(controls)

    def start(self):
        to, subject, body = self.to.get_text().strip(), self.subject.get_text().strip(), self.body.get_text()
        def prepare(cancel):
            issues = self.parent.quality.catalog(self.catalog, cancel=cancel, progress=self.parent.progress_message)
            if any(i.severity == "error" for i in issues):
                raise ValueError("Åtgärda kvalitetsfelen före leverans:\n" + "\n".join(i.message for i in issues if i.severity == "error")[:4000])
            return prepare_mail(self.catalog, to, subject, body)
        def done(result):
            draft, attachment = result
            text = f"Till: {to}\nÄmne: {subject}\nBilaga: {attachment.name} ({attachment.stat().st_size} byte)\n\n{body}\n\nUtkast sparat: {draft}"
            def launch():
                return self.parent.job("Öppnar e-postutkast…", lambda _: open_mail(to, subject, body, attachment),
                                       lambda _: self.parent.toast("Utkastet är öppnat. Kontrollera bilagan och skicka från e-postprogrammet."))
            preview(self.parent, "Granska e-post", text, "Öppna i e-postprogram", launch)
        if self.parent.job("Förbereder e-post…", prepare, done):
            self.close()
