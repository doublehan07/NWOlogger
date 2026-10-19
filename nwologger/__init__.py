"""NWOlogger: a minimal, cross-platform amateur radio logger."""

import sys
from importlib.metadata import PackageNotFoundError, version

# The one number to bump with pyproject.toml (tests/test_version.py keeps them equal). A packaged build reports it
# as is: it bundles no package metadata, so a stale nwologger-<old>.dist-info left in an upgraded install folder
# can never speak for the app (0.1.9 on Windows said 0.1.8 that way). From a source tree the installed metadata
# wins, as before.
__version__ = "0.2.0"
if not getattr(sys, "frozen", False):
    try:
        __version__ = version("nwologger")
    except PackageNotFoundError:  # running from a source tree without install
        pass

APP_NAME = "NWOlogger"
