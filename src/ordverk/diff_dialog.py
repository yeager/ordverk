"""Review or edit each changed translation before merging a unified diff."""
import copy

from .dialogs import Adw, Gtk


class DiffDialog(Adw.Window):
    def __init__(self, parent, proposal):
        super().__init__(transient_for=parent, title="Importera diff · " + proposal.path, default_width=760, default_height=720)
        self.parent, self.proposal = parent, proposal
        self.unit, self.loading, self.variant = None, False, 0
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.append(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=24, margin_end=24, margin_bottom=24, vexpand=True)
        root.append(content)
        content.append(Gtk.Label(label=f"{len(proposal.changed)} ändrade strängar · {proposal.removed} borttagna", xalign=0, css_classes=["title-2"]))
        content.append(Gtk.Label(label="Hela diffen, även metadata och källtexter, följer med. Du kan ändra varje översättning här. Filer skrivs först när du sparar.", wrap=True, xalign=0))
        self.all_status = Gtk.DropDown.new_from_strings(["Alla ändringar: luddiga (fuzzy)", "Alla ändringar: granskade"])
        self.all_status.connect("notify::selected", self.set_all)
        content.append(self.all_status)
        self.units = Gtk.DropDown(model=Gtk.StringList.new([u.source[:120] for u in proposal.changed]), enable_search=True)
        self.units.connect("notify::selected", self.select_unit)
        content.append(self.units)
        self.variants = Gtk.DropDown()
        self.variants.connect("notify::selected", self.select_variant)
        content.append(self.variants)
        self.source = Gtk.Label(xalign=0, wrap=True, selectable=True)
        content.append(self.source)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=12, right_margin=12, top_margin=12, bottom_margin=12)
        self.text.get_buffer().set_enable_undo(True)
        self.text.get_buffer().connect("changed", self.edit)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=180)
        scroll.set_child(self.text)
        content.append(scroll)
        self.status = Gtk.DropDown.new_from_strings(["Luddig (fuzzy) · behöver granskas", "Godkänd och granskad"])
        self.status.connect("notify::selected", self.set_status)
        content.append(self.status)
        apply = Gtk.Button(label="Inkludera ändringarna", css_classes=["suggested-action"], halign=Gtk.Align.END)
        apply.connect("clicked", lambda _: self.apply())
        content.append(apply)
        self.set_content(root)
        self.select_unit()

    def select_unit(self, *_):
        i = self.units.get_selected()
        self.unit = self.proposal.changed[i] if i < len(self.proposal.changed) else None
        self.loading = True
        self.variants.set_model(Gtk.StringList.new(self.unit.variants if self.unit else []))
        self.variants.set_selected(0)
        self.variant = 0
        self.loading = False
        self.show_unit()

    def select_variant(self, *_):
        if not self.loading and self.unit:
            self.variant = min(self.variants.get_selected(), len(self.unit.targets) - 1)
            self.show_unit()

    def show_unit(self):
        self.loading = True
        self.text.get_buffer().set_enable_undo(False)
        self.text.get_buffer().set_text(self.unit.targets[self.variant] if self.unit else "")
        self.text.get_buffer().set_enable_undo(True)
        self.text.set_sensitive(self.unit is not None)
        self.source.set_label(self.unit.source_for(self.variant) if self.unit else "Diffen ändrar endast metadata eller tar bort strängar.")
        self.status.set_selected(1 if self.unit and self.unit.reviewed else 0)
        self.loading = False

    def edit(self, buffer):
        if self.unit and not self.loading:
            self.unit.edit(self.variant, buffer.get_text(*buffer.get_bounds(), True))
            self.status.set_selected(0)

    def set_status(self, *_):
        if self.unit and not self.loading:
            self.unit.reviewed = bool(self.status.get_selected())

    def set_all(self, *_):
        for unit in self.proposal.changed:
            unit.reviewed = bool(self.all_status.get_selected())
        if hasattr(self, "text"):
            self.show_unit()

    def apply(self):
        proposal = copy.deepcopy(self.proposal)
        proposal.original = self.proposal.original
        def check(cancel):
            errors = []
            for unit in proposal.changed:
                if cancel.is_set():
                    return proposal, ["Granskningen avbröts. Dina diffändringar finns kvar här."]
                if unit.reviewed:
                    if not all(unit.targets):
                        errors.append(f"{unit.source}: alla varianter behöver översättas innan de godkänns.")
                    for variant in range(len(unit.targets)):
                        errors.extend(i.message for i in self.parent.quality.check(unit, variant) if i.severity == "error")
            return proposal, errors
        def done(result):
            self.set_sensitive(True)
            ready, errors = result
            if errors:
                self.parent.error("Åtgärda felen eller välj luddig status:\n" + "\n".join(errors), parent=self)
                return
            ready.apply()
            self.parent.marked = {key for key in self.parent.marked if key[0] != id(ready.original)}
            self.parent.refresh_files()
            self.parent.filter_units()
            self.parent.update_statistics()
            self.parent.toast("Diffens ändringar är inkluderade. Spara filen när du är klar.")
            self.close()
        if self.parent.job("Kontrollerar diffens granskade strängar…", check, done):
            self.set_sensitive(False)
