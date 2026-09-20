# Tredjepartsresurser

Ordverks kod distribueras under GPL-3.0-or-later. Licenstext finns i `LICENSE`.

Programmet integrerar följande projekt via installerade bibliotek eller
externa stavningsmotorer. Deras egna licenser gäller för respektive komponent.

| Projekt | Licens / ursprung |
| --- | --- |
| [l10n-lint](https://github.com/yeager/l10n-lint) | GPL-3.0-or-later, Daniel Nylander och projektets bidragsgivare |
| [svlang](https://github.com/yeager/svlang) | MIT, Daniel Nylander och projektets bidragsgivare |
| [polib](https://pypi.org/project/polib/) | MIT |
| [lxml](https://lxml.de/) | BSD, med libxml2/libxslt enligt respektive licens |
| [GTK](https://www.gtk.org/) / [libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/) / PyGObject | LGPL enligt respektive komponent |
| [keyring](https://pypi.org/project/keyring/) | MIT |

De stora språkfilerna ingår inte i Ordverks källarkiv. De hämtas till användarens
cache från nedanstående projekt; revisionsidentifierare, originaladress och
SHA-256 sparas i `provenance.json`. Hämtade originalfiler ändras inte.

- **[Swedish Translation Memory](https://github.com/yeager/swedish-tm)**:
  CC BY 4.0, svensk FOSS-översättning från de ursprungsprojekt som anges i
  exporterna. Underhålls av Daniel Nylander / yeager. Ordverk omvandlar
  exporterna till ett sökindex och visar ekosystem och tillgängliga referenser.
- **[Swedish FOSS Terminology](https://github.com/yeager/swedish-foss-terminology)**:
  CC BY 4.0, svenska FOSS-översättningsgemenskapen / yeager. Ordverk indexerar
  källterm, kanonisk målterm och angiven konsensus utan att göra egna språkliga ändringar.
- **[hunspell-sv](https://github.com/yeager/hunspell-sv)**: LGPL-3.0 enligt
  projektets licens, med källor och tillskrivning enligt dess README/LICENSE.
  Ordverk hämtar både `.aff` och `.dic` samt licenstexten.
- **[aspell-sv](https://github.com/yeager/aspell-sv/tree/master/swedish)**:
  ordlisteprojektets README beskriver LGPL/GPL-villkor per beståndsdel och
  förhandsstatus. Ordverk hämtar tillhörande licensfiler och bygger `sv.rws`
  med systemets Aspell. Källdata och licensfiler behålls i cachen.

Ordverks svenska presentation av l10n-lints diagnostik matchar meddelandemallar
från det integrerade GPL-projektet. Regel-ID:n behålls för spårbarhet.

Ordverk-ikonen genererades med det inbyggda imagegen-verktyget för detta projekt.
Originalbildens alfakanal har bevarats. Prompten finns i `data/icon-prompt.txt`.
