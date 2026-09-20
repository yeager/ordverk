#!/usr/bin/env python3
"""Prepare pinned upstream sources and standard dependency-package recipes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = {
    "l10n-lint": ("1.21.4", "27ee6ae7860514ddbaaaa08d3cb06c8ead28f69f", "29000e6426d8d72af520586b94ca710e2197ad45579484e132afbe9b68186180", "GPL-3.0-or-later", "l10n_lint l10n_lint_gtk l10n_project print_helper", "l10n-lint l10n-lint-gtk"),
    "svlang": ("0.2.2", "5c54d9ec29a3e8dd0158ed6d33498830f823617f", "df560284453b337ef2ccdb823039bf3afd71042b9808d325a1603fbb04dd56e2", "MIT", "svlang", "svlang"),
}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def debian(folder, name, version, license_name):
    source, binary = "python-" + name, "python3-" + name
    write(folder / "debian/control", f'''Source: {source}
Section: python
Priority: optional
Maintainer: Daniel Nylander <daniel@danielnylander.se>
Build-Depends: debhelper-compat (= 13), dh-sequence-python3, pybuild-plugin-pyproject,
 python3-all, python3-setuptools (>= 77), python3-setuptools-scm, python3-build, python3-installer
Standards-Version: 4.7.2
Rules-Requires-Root: no
Homepage: https://github.com/yeager/{name}

Package: {binary}
Architecture: all
Depends: ${{python3:Depends}}, ${{misc:Depends}}
Description: Swedish localization support library {name}
 Python library used by Ordverk for language and localization quality checks.
 Includes the upstream command-line entry points.
''')
    write(folder / "debian/rules", f'''#!/usr/bin/make -f
export PYBUILD_NAME={name}
export PYBUILD_DISABLE=test

%:
\tdh $@ --buildsystem=pybuild
''')
    (folder / "debian/rules").chmod(0o755)
    write(folder / "debian/source/format", "3.0 (quilt)\n")
    write(folder / "debian/changelog", f'''{source} ({version}-1) unstable; urgency=medium

  * Package pinned upstream release as an Ordverk dependency.

 -- Daniel Nylander <daniel@danielnylander.se>  Sun, 20 Sep 2026 12:00:00 +0200
''')
    # Preserve the upstream license statement verbatim in DEP-5 format.
    license_text = (folder / "LICENSE").read_text()
    write(folder / "debian/copyright", f'''Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {name}
Source: https://github.com/yeager/{name}

Files: *
Copyright: 2026 Daniel Nylander
License: {"GPL-3+" if license_name.startswith("GPL") else "Expat"}
''' + "\n".join(" " + (line or ".") for line in license_text.splitlines()) + "\n")
    if license_name.startswith("GPL"):
        with (folder / "debian/copyright").open("a") as file:
            file.write(" .\n On Debian systems, the full license is available in\n /usr/share/common-licenses/GPL-3.\n")
    write(folder / "debian/docs", "README.md\n")
    pages = "man/en/l10n-lint.1\nman/en/l10n-lint-gtk.1\n" if name == "l10n-lint" else "data/man/svlang.1\n"
    write(folder / "debian/manpages", pages)
    write(folder / f"debian/{binary}.lintian-overrides", "# Distributed by upstream; there is no Debian ITP bug.\ninitial-upload-closes-no-bugs\n")
    if name == "l10n-lint":
        with (folder / "debian/copyright").open("a") as file:
            file.write("\nFiles: *.metainfo.xml\nCopyright: 2026 Daniel Nylander\nLicense: CC0-1.0\n The metadata is dedicated to the public domain under CC0-1.0.\n See https://creativecommons.org/publicdomain/zero/1.0/legalcode\n")


def rpm_spec(name, version, license_name, modules, commands, revision):
    return f'''Name:           python-{name}
Version:        {version}
Release:        1%{{?dist}}
Summary:        Swedish localization support library
License:        {license_name}
URL:            https://github.com/yeager/{name}
Source0:        https://github.com/yeager/{name}/archive/{revision}.tar.gz#/{name}-%{{version}}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools >= 77
BuildRequires:  python3-setuptools_scm

%description
Python library used by Ordverk for Swedish localization quality checks.

%package -n python3-{name}
Summary:        Swedish localization support library for Python 3

%description -n python3-{name}
Python library used by Ordverk for Swedish localization quality checks.
Includes upstream command-line entry points.

%prep
%autosetup -n {name}-{revision}
''' + ("sed -i '1{/^#!/d;}' l10n_lint.py l10n_lint_gtk.py\n" if name == "l10n-lint" else "") + f'''

%generate_buildrequires
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files {modules}
''' + ("install -Dpm 644 man/en/l10n-lint.1 %{buildroot}%{_mandir}/man1/l10n-lint.1\ninstall -Dpm 644 man/en/l10n-lint-gtk.1 %{buildroot}%{_mandir}/man1/l10n-lint-gtk.1\n" if name == "l10n-lint" else "install -Dpm 644 data/man/svlang.1 %{buildroot}%{_mandir}/man1/svlang.1\n") + f'''

%check
%pyproject_check_import {"-e l10n_lint_gtk" if name == "l10n-lint" else ""}

%files -n python3-{name} -f %{{pyproject_files}}
%doc README.md
%license LICENSE
''' + "\n".join("%{_bindir}/" + c for c in commands.split()) + '''
%{_mandir}/man1/*

%changelog
* Sun Sep 20 2026 Daniel Nylander <daniel@danielnylander.se> - ''' + version + '''-1
- Package pinned upstream dependency for Ordverk.
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    destination = args.directory.absolute()
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (version, revision, sha, license_name, modules, commands) in LIBRARIES.items():
        url = f"https://codeload.github.com/yeager/{name}/tar.gz/{revision}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != sha:
            raise SystemExit(f"Checksum mismatch: {name}")
        folder = destination / f"{name}-{version}"
        if folder.exists():
            raise SystemExit(f"Directory already exists: {folder}")
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(destination, filter="data")
        (destination / f"{name}-{revision}").rename(folder)
        # Upstream may carry older downstream packaging; use the reviewable recipe above.
        if (folder / "debian").exists():
            shutil.rmtree(folder / "debian")
        tarpath = destination / f"{name}-{version}.tar.gz"
        tarpath.write_bytes(data)
        with tarfile.open(destination / f"python-{name}_{version}.orig.tar.gz", "w:gz") as archive:
            archive.add(folder, arcname=folder.name, filter=lambda entry: None if "/.git/" in entry.name else entry)
        debian(folder, name, version, license_name)
        write(destination / f"python-{name}.spec", rpm_spec(name, version, license_name, modules, commands, revision))
        records.append({"name": name, "version": version, "revision": revision, "sha256": sha, "url": url})
    subprocess.run([sys.executable, "-m", "build", "--sdist", "--outdir", str(destination), str(ROOT)], check=True)
    with tarfile.open(destination / "ordverk-0.2.tar.gz") as archive:
        archive.extractall(destination, filter="data")
    with tarfile.open(destination / "ordverk_0.2.orig.tar.gz", "w:gz") as archive:
        archive.add(destination / "ordverk-0.2", arcname="ordverk-0.2",
                    filter=lambda entry: None if entry.name == "ordverk-0.2/debian" or entry.name.startswith("ordverk-0.2/debian/") else entry)
    write(destination / "sources.json", json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()
