"""Shared GTK imports: all entry points must configure the language first."""
from .localization import configure_language

configure_language()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402, F401
