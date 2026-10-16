"""NWOlogger: a minimal, cross-platform amateur radio logger."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nwologger")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.1.1"

APP_NAME = "NWOlogger"
