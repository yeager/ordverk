"""Install the user's launcher and original PNG without changing system files."""
import os
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
user_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
applications = user_data / "applications"
icons = user_data / "icons/hicolor/512x512/apps"
applications.mkdir(parents=True, exist_ok=True)
icons.mkdir(parents=True, exist_ok=True)
icon = icons / "io.github.yeager.Ordverk.png"
shutil.copy2(root / "data" / icon.name, icon)
desktop = (root / "data/io.github.yeager.Ordverk.desktop").read_text()
executable = str(root / ".venv/bin/ordverk")
quoted = '"' + ''.join('\\' + c if c in '\\"`$' else c for c in executable) + '"'
desktop = desktop.replace("Exec=ordverk %U", f"Exec={quoted} %U")
desktop = desktop.replace("Icon=io.github.yeager.Ordverk", f"Icon={icon}")
(applications / "io.github.yeager.Ordverk.desktop").write_text(desktop)
if shutil.which("update-desktop-database"):
    subprocess.run(["update-desktop-database", str(applications)], check=False)
print(f"Startprogram installerat: {applications / 'io.github.yeager.Ordverk.desktop'}")
