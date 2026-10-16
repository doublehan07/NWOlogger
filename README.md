# QHlogger

A minimal, cross-platform amateur radio logger for macOS and Windows. It is modelled on
**MacLoggerDX** for everyday logging and on **N1MM Logger+** for contesting, with its own
modern UI, and built with Python and Qt (PySide6).

> **Status: v0.1.0, first working version.**
> - Logging, search, bulk edit, paper QSL tracking, ADIF, MacLoggerDX migration and contest mode work and are tested.
> - The LoTW, eQSL, QRZ.com and HRDLog clients are written to the services' published interfaces, but have not yet been tried with real accounts.
>
> The design is in [PLAN.md](PLAN.md) and the remaining work in [TODO.md](TODO.md).

## Features

- **One SQLite file** holds every QSO (default `~/Documents/QHlogger/qhlog.sqlite`).
  - Columns are plain ADIF names, so the log can be queried with any SQLite tool.
  - The log is backed up automatically on open and before every import or bulk edit.
- **ADIF import and export.** Export all, selected, or filtered QSOs. Fields QHlogger doesn't know are kept and written back out.
- **MacLoggerDX migration** straight from its `.sql` database. It keeps the LoTW, eQSL, QRZ and HRDLog upload history and the paper card dates that MLDX's own ADIF export drops. Re-running it updates QSOs instead of duplicating them.
- **Search** across several criteria at once (call, band, mode, satellite, time range, country, confirmation state, …).
  - `?` and `*` wildcards on callsigns.
  - Mode search is vague by default: `SSB` finds USB/LSB, `DIGI` finds FT8. An *Exact* option makes it strict.
  - Searches can be saved by name.
- **Sorting** by any column, including satellite name, time on and time off. Columns can be shown, hidden and moved.
- **Bulk edit** of a multi-row selection: set, clear, replace text, or fill from the country file. Shows a preview first; undo with ⌘Z / Ctrl+Z.
- **Electronic confirmations, tracked per service:**
  - LoTW (signed with TQSL), eQSL and QRZ.com Logbook: upload and download
  - HRDLog.net: upload only (it has no download API)
  - Satellite names are translated per service, e.g. ISS: `ARISS` for LoTW, `ISS` for eQSL.
- **Paper QSL cards** tracked separately (sent and received dates, via bureau, direct or manager), with a "to send" queue. Never mixed with electronic records.
- **Callsign lookup:**
  - Offline country file (`cty.csv`, updated weekly) for country, DXCC and zones.
  - QRZ.com XML, with HamQTH as fallback. Lookups fill empty fields only, and filled fields are tinted.
  - Chinese provinces are normalised to standard codes (BJ, SC, …) and guessed from the call district.
- **Satellite logging:** pick a satellite and its frequencies and mode come from your last QSO on it. Fields stay set for the whole pass.
- **Strict dupe checking.**
  - Only the same call, band and exact mode within 30 seconds blocks logging.
  - The same mode later, or a related mode (SSB vs USB, USB vs LSB, FT8 vs FT4), only raises a warning.
  - ADIF import keeps every record and marks suspicious dupes for review.
- **Contest mode (N1MM style):**
  - Contest chosen from a dropdown: CQ WW, CQ WPX, IARU HF, ARRL DX, ARRL 10 m, All Asian, CQ WW VHF and two generic templates, plus your own.
  - Automatic serial numbers.
  - Live dupe check, Check Partial (MASTER.SCP + your log) and N+1.
  - Live score, multipliers and rate in dockable panes.
  - Cabrillo export.
- **Modern UI** with light and dark themes, keyboard-first (command palette ⌘K / Ctrl+K), in **English and Simplified Chinese**.

No rig control, by design: QHlogger never talks to a radio.

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # create the venv and install dependencies
uv run qhlogger    # start the app   (--db FILE, --lang zh_CN, --theme dark, --version)
uv run pytest      # run tests
uv run ruff check  # lint
```

On first start, QHlogger asks for a station profile (your callsign and grid). Then use **File → Import** for an ADIF file or your `MacLoggerDX.sql`.

To build a stand-alone app (`dist/QHlogger.app` on macOS, `dist/QHlogger/` on Windows):

```sh
uv run --with pyinstaller pyinstaller packaging/qhlogger.spec --noconfirm
```

Pushing a tag `vX.Y.Z` that matches the `pyproject.toml` version makes GitHub Actions build a macOS `.dmg` and a Windows installer.

## Keyboard

| Keys | Action |
|---|---|
| Enter | Log the QSO (DX); in contest mode, go to the next empty field and then log |
| Esc | Clear the form / wipe the contest entry line |
| Space (contest) | Next field |
| `14025` + Enter, `CW` + Enter (contest call field) | Set the frequency or mode |
| ⌘K / Ctrl+K | Command palette |
| ⌘L / Ctrl+L | Focus the call field |
| ⌘F / Ctrl+F | Search log |
| ⌘B / Ctrl+B | Bulk edit the selection |
| ⌘Z / Ctrl+Z | Undo (log, edit, bulk edit, delete, import) |
| ⌘1–3 / Ctrl+1–3 | Log · Paper QSL · Sync history tabs |
| ⇧⌘1 / ⇧⌘2 (Ctrl+Shift+1 / 2) | DX view / Contest view |

## Privacy

- Passwords, upload codes and API keys go to the system keychain only (macOS Keychain / Windows Credential Manager).
- Never commit real log files. `*.sql`, `*.sqlite`, `*.adi` and similar are git-ignored because they contain other operators' personal data. Test fixtures must be synthetic.

## Contributing

Every commit message is a single brief sentence.

## License

[MIT](LICENSE). Country data: AD1C, country-files.com. Icons: Lucide (ISC).
