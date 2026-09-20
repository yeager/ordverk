"""Preview a POT update before applying it to the open PO work copy."""
from pathlib import Path

from .dialogs import Adw, Gtk
from .importers import MAX_FILE_BYTES
from .pot_merge import prepare_merge


def choose_template(parent):
    catalog = parent.catalog
    if not catalog or catalog.ext != ".po":
        parent.toast("Välj en redan inläst PO-fil först.")
        return
    def selected(path):
        dialog = Adw.MessageDialog(transient_for=parent, modal=True, heading="Uppdatera PO från POT",
                                   body=f"PO-fil: {catalog.name}\nPOT-fil: {Path(path).name}\n\nBefintliga översättningar bevaras där källtexterna matchar. Resultatet förhandsgranskas innan det tillämpas.")
        fuzzy = Gtk.CheckButton(label="Använd liknande träffar som luddiga (fuzzy)", active=True)
        dialog.set_extra_child(fuzzy)
        dialog.add_response("cancel", "Avbryt")
        dialog.add_response("preview", "Förhandsgranska uppdatering")
        dialog.set_default_response("preview")
        dialog.set_close_response("cancel")
        def respond(_dialog, response):
            if response == "preview":
                load_template(parent, catalog, path, fuzzy.get_active())
        dialog.connect("response", respond)
        dialog.present()
    parent.file_dialog("Välj POT-fil", selected, patterns=["*.pot"])


def load_template(parent, catalog, path, fuzzy=True):
    if parent.busy:
        parent.toast("Låt det pågående arbetet bli klart innan du uppdaterar från POT.")
        return
    parent.stack.set_sensitive(False)
    def operation(cancel):
        with Path(path).open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("POT-filen är större än importgränsen på 32 MiB.")
        return prepare_merge(catalog, Path(path).name, data, cancel=cancel, fuzzy=fuzzy)
    def done(proposal):
        date = proposal.template_date or "Saknas i mallen; tidigare datum behålls."
        dialog = Adw.MessageDialog(transient_for=parent, modal=True, heading="Granska POT-uppdateringen",
            body=f"{proposal.retained} matchande strängar\n{proposal.added} nya eller ändrade källtexter\n"
                 f"{proposal.removed} tidigare källtexter tas ur bruk\n{proposal.fuzzy} luddiga strängar att granska\n\n"
                 f"POT-Creation-Date: {date}\n\nBorttagna poster bevaras som föråldrade i PO-filen. Originalfilen skrivs först när du sparar.")
        dialog.add_response("cancel", "Avbryt")
        dialog.add_response("apply", "Uppdatera arbetskopian")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        def respond(_dialog, response):
            if response != "apply":
                return
            if parent.busy:
                parent.toast("Låt det pågående arbetet bli klart och öppna uppdateringen igen.")
                return
            try:
                proposal.apply()
            except ValueError as exc:
                parent.error(str(exc))
                return
            parent.marked = {key for key in parent.marked if key[0] != id(catalog)}
            parent.refresh_files()
            parent.filter_units()
            parent.update_statistics()
            parent.update_editor_status()
            parent.toast("PO-filen har uppdaterats från POT. Granska luddiga strängar och spara när du är klar.")
        dialog.connect("response", respond)
        parent.pot_preview = dialog
        dialog.present()
    return parent.job("Sammanfogar PO-filen med den nya POT-mallen…", operation, done)
