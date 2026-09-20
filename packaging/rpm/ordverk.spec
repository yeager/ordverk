Name:           ordverk
Version:        0.3
Release:        1%{?dist}
Summary:        Swedish translation workbench for Linux
License:        GPL-3.0-or-later
URL:            https://github.com/yeager/ordverk
Source0:        https://github.com/yeager/ordverk/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-setuptools >= 77
BuildRequires:  desktop-file-utils
Requires:       python3dist(pygobject)
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.4
Requires:       glibc-langpack-sv
Requires:       gettext
Requires:       python3dist(polib) >= 1.2
Requires:       python3dist(lxml) >= 5
Requires:       python3dist(l10n-lint) >= 1.21.4
Requires:       python3dist(svlang) >= 0.2.2
Recommends:     hunspell
Recommends:     aspell
Recommends:     python3-keyring
Recommends:     xdg-utils

%description
Native GTK application for translating and reviewing PO, Qt TS, XLIFF and
JSON files in Swedish, with translation memory, terminology, spellchecking,
optional LLM assistance and translation-platform file transfers.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files ordverk

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.yeager.Ordverk.desktop

%files -f %{pyproject_files}
%doc README.md THIRD_PARTY.md
%license LICENSE
%{_bindir}/ordverk
%{_datadir}/applications/io.github.yeager.Ordverk.desktop
%{_datadir}/pixmaps/io.github.yeager.Ordverk.png
%{_datadir}/metainfo/io.github.yeager.Ordverk.metainfo.xml
%{_mandir}/man1/ordverk.1*

%changelog
* Sun Sep 20 2026 Daniel Nylander <daniel@danielnylander.se> - 0.3-1
- Add translation providers, context, exports and PO/POT maintenance.
- Improve Swedish localization, unsaved-change protection and large catalogs.

* Sun Sep 20 2026 Daniel Nylander <daniel@danielnylander.se> - 0.2-1
- Initial release.
