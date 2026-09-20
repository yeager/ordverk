"""Edit a PO header without saving the translation file implicitly."""
from .dialogs import Adw, Gtk, entry
from .po_header import translator_identity, update_header

FIELDS = (
    ("Project-Id-Version", "Projekt och version"),
    ("Report-Msgid-Bugs-To", "Adress för fel i källtexter"),
    ("POT-Creation-Date", "Mallens skapandedatum"),
    ("PO-Revision-Date", "Översättningens revisionsdatum"),
    ("Last-Translator", "Senaste översättare"),
    ("Language-Team", "Översättningsgrupp"),
)


def text_view(group, title, text):
    group.add(Adw.ActionRow(title=title))
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                        left_margin=10, right_margin=10)
    view.get_buffer().set_text(text)
    scroll = Gtk.ScrolledWindow(min_content_height=110)
    scroll.set_child(view)
    group.add(scroll)
    return view


def content(view):
    buffer = view.get_buffer()
    return buffer.get_text(*buffer.get_bounds(), True)


class HeaderDialog(Adw.PreferencesWindow):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Redigera PO-huvud", default_width=740, default_height=760)
        self.parent, self.catalog = parent, parent.catalog
        self.expected = (dict(self.catalog.po.metadata), self.catalog.po.header)
        page = Adw.PreferencesPage(title="PO-huvud")
        self.add(page)
        group = Adw.PreferencesGroup(title=self.catalog.name,
                                     description="Ändringarna läggs i arbetskopian. Välj Spara för att skriva filen.")
        page.add(group)
        self.fields = {key: entry(group, title, self.catalog.po.metadata.get(key, "")) for key, title in FIELDS}
        identity = Gtk.Button(label="Använd översättare från inställningarna", halign=Gtk.Align.START)
        identity.connect("clicked", lambda _: self.use_identity())
        group.add(identity)
        self.fixed = {key: self.catalog.po.metadata.get(key, default) for key, default in (
            ("Language", "sv"), ("Content-Type", f"text/plain; charset={self.catalog.po.encoding}"),
            ("MIME-Version", "1.0"), ("Content-Transfer-Encoding", "8bit"),
            ("Plural-Forms", "nplurals=2; plural=(n != 1);"))}
        group.add(Adw.ActionRow(title="Språk, teckenkodning och pluralformer",
                               subtitle=f"Svenska · {self.catalog.po.encoding} · ental och flertal"))
        self.comment = text_view(group, "Kommentar i PO-huvudet", self.catalog.po.header)
        other = "\n".join(f"{key}: {value}" for key, value in self.catalog.po.metadata.items()
                          if key not in self.fields and key not in self.fixed)
        self.other = text_view(group, "Övriga huvudfält · ett Namn: värde per rad", other)
        apply = Gtk.Button(label="Tillämpa huvudändringar", css_classes=["suggested-action"], halign=Gtk.Align.CENTER)
        apply.connect("clicked", lambda _: self.apply())
        group.add(apply)

    def use_identity(self):
        value = translator_identity(self.parent.settings.translator_name, self.parent.settings.translator_email)
        if value:
            self.fields["Last-Translator"].set_text(value)
        else:
            self.parent.error("Fyll i översättarens namn och e-postadress i inställningarna först.", parent=self)

    def apply(self):
        if self.parent.busy:
            self.parent.toast("Låt det pågående arbetet bli klart innan du ändrar huvudet.")
            return
        if self.expected != (dict(self.catalog.po.metadata), self.catalog.po.header):
            self.parent.error("PO-huvudet har ändrats. Öppna huvudredigeraren på nytt.", parent=self)
            return
        try:
            metadata = dict(self.fixed)
            metadata.update({key: row.get_text() for key, row in self.fields.items() if row.get_text()})
            for line in content(self.other).splitlines():
                if not line.strip():
                    continue
                key, separator, value = line.partition(":")
                key = key.strip()
                if not separator or key in metadata or key in self.fields:
                    raise ValueError("Skriv övriga huvudfält som Namn: värde, utan dubbletter.")
                metadata[key] = value.strip()
            update_header(self.catalog, metadata, content(self.comment))
        except ValueError as exc:
            self.parent.error(str(exc), parent=self)
            return
        self.parent.refresh_files()
        self.parent.update_editor_status()
        self.parent.toast("PO-huvudet har uppdaterats i arbetskopian.")
        self.close()
