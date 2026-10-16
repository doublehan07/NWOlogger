"""QHlogger: a minimal, cross-platform amateur radio logger."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("qhlogger")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.1.0"

APP_NAME = "QHlogger"
