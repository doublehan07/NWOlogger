# QHlogger

A minimal, cross-platform amateur radio logger for macOS and Windows. It is modelled on
**MacLoggerDX** for everyday logging and on **N1MM Logger+** for contesting, with its own
modern UI, and built with Python and Qt (PySide6).

> **Status: planning.** No code yet. The design is in [PLAN.md](PLAN.md) and the work list in [TODO.md](TODO.md).

## Planned features

- **One SQLite file** holds every QSO. Columns are plain ADIF names, so the log can be queried with any SQLite tool.
- **ADIF import and export.** Export all, selected, or filtered QSOs. Fields QHlogger doesn't know are kept and written back out.
- **MacLoggerDX migration** straight from its database, keeping the LoTW, eQSL, QRZ and HRDLog upload history that MLDX's own ADIF export drops.
- **Search** across several criteria at once (call, band, mode, satellite, time range, country, confirmation state, …).
  - `?` and `*` wildcards on callsigns.
  - Mode search is vague by default: `SSB` finds USB/LSB, `DIGI` finds FT8. An *Exact* option makes it strict.
- **Sorting** by any column, including satellite name, time on and time off.
- **Bulk edit** of a multi-row selection after logging, with preview and undo.
- **Electronic confirmations, tracked per service:**
  - LoTW (via TQSL), eQSL and QRZ.com Logbook: upload and download
  - HRDLog.net: upload (download if the website allows it)
- **Paper QSL cards** tracked separately (sent and received dates, via bureau, direct or manager), never mixed with electronic records.
- **Callsign lookup:** offline `cty.csv` for country, DXCC and zones; QRZ.com XML, with HamQTH as fallback.
- **Satellite logging:** pick a satellite and its uplink/downlink, band and mode are filled in; fields stay set for the whole pass. Satellite names are translated per service.
- **Strict dupe checking.**
  - Only the same call, band and exact mode within 30 seconds blocks logging.
  - The same mode later, or a related mode (SSB vs USB, USB vs LSB, FT8 vs FT4), only raises a warning.
  - ADIF import keeps every record and marks suspicious dupes.
- **Contest mode (N1MM style):**
  - contest chosen from a dropdown
  - serial numbers
  - live dupe check, Check Partial and N+1
  - live score, multipliers and rate
  - user-defined contests
  - Cabrillo export
- **Modern UI** with light and dark themes, keyboard-first, in **English and Simplified Chinese**.
- **Later:** WSJT-X / JTDX and N1MM Logger+ auto-logging over UDP.

No rig control, by design: QHlogger never talks to a radio.

## Development (planned)

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # create the venv and install dependencies
uv run qhlogger    # start the app
uv run pytest      # run tests
uv run ruff check  # lint
```

Never commit real log files: `*.sql`, `*.sqlite`, `*.adi` and similar are git-ignored because they contain other operators' personal data. Test fixtures under `tests/fixtures/` must be synthetic.

## Contributing

Every commit message is a single brief sentence.

## License

[MIT](LICENSE)
