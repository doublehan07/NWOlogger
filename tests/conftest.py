import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QHLOGGER_OFFLINE", "1")
os.environ.setdefault("QHLOGGER_SETTINGS", os.path.join(os.path.dirname(__file__), ".pytest-settings.ini"))
