# dmgbuild settings: the app and a link to Applications side by side, with an arrow between, so it is dragged in.
# Build (after PyInstaller):
#   uv run --with dmgbuild dmgbuild -s packaging/dmg_settings.py -D app=dist/NWOlogger.app NWOlogger out.dmg
# dmgbuild writes the window layout itself (no Finder), so it also runs on a build server.
import os.path

app = defines.get("app", "dist/NWOlogger.app")  # noqa: F821 - dmgbuild provides ``defines``
name = os.path.basename(app)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [name]
background = "builtin-arrow"  # 640 × 240, an arrow in the middle
window_rect = ((200, 160), (640, 280))
default_view = "icon-view"
icon_size = 112
text_size = 13
icon_locations = {name: (140, 120), "Applications": (500, 120)}
