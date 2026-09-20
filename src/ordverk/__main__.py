from __future__ import annotations

import argparse
import sys

from . import __version__


def main():
    if len(sys.argv) > 1 and sys.argv[1] in {"--version", "--update-resources", "--check"}:
        parser = argparse.ArgumentParser(description="Ordverk — svensk översättningsverkstad")
        parser.add_argument("--version", action="version", version=__version__)
        parser.add_argument("--update-resources", nargs="*", choices=["tm", "tm-small", "terms", "hunspell", "aspell"])
        parser.add_argument("--check", nargs="+")
        args = parser.parse_args()
        if args.update_resources is not None:
            from .resources import ResourceStore
            store = ResourceStore()
            errors = store.update(args.update_resources or ["tm", "terms", "hunspell", "aspell"], progress=print)
            for error in errors:
                print(error, file=sys.stderr)
            return 1 if errors else 0
        if args.check:
            from .importers import import_sources
            from .quality import Quality
            from .resources import ResourceStore
            from .settings import Settings
            imported = import_sources(args.check)
            quality = Quality(Settings.load(), ResourceStore())
            failed = bool(imported.errors)
            for error in imported.errors:
                print(error, file=sys.stderr)
            for catalog in imported.catalogs:
                for issue in quality.catalog(catalog):
                    severity = {"error": "fel", "warning": "varning", "info": "information"}[issue.severity]
                    print(f"{catalog.name}: {severity}: {issue.rule}: {issue.message}")
                    failed |= issue.severity == "error"
            return int(failed)
    from .ui import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
