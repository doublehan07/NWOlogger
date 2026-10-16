"""``nwologger`` / ``python -m nwologger`` entry point."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="nwologger", description=f"{APP_NAME}: a minimal amateur radio logger")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--db", help="open this log file (SQLite)")
    p.add_argument("--lang", choices=["en", "zh_CN"], help="UI language for this run")
    p.add_argument("--theme", choices=["system", "light", "dark"], help="theme for this run")
    args, qt_args = p.parse_known_args(argv)
    from .app import run  # noqa: PLC0415 - keep --version fast and Qt-free

    return run([sys.argv[0], *qt_args], db_path=args.db, lang=args.lang, theme_mode=args.theme)


if __name__ == "__main__":
    sys.exit(main())
