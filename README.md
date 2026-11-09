# Solis Modbus Config

Desktop tool to **read and write every documented Solis inverter setting**
(protocol **V3.5**, supplemented by **V3.2**) and to **monitor** live values,
status, alarms and faults.

This is *not* the EN50549-2-only helper
([solis-en50549-mv-config](https://github.com/nhsqr/solis-en50549-mv-config)).
It exposes the full holding-register map (43xxx) and the input-register map
(33xxx / 25xxx), grouped, with Bulgarian explanations, appendix enums and
bitfields.

Програма за **четене и запис на всички настройки** от протоколите Solis
**V3.5** (основен) и **V3.2** (допълнение) и за **мониторинг** на текущите
стойности, статус, аларми и грешки. Не е фокусирана върху EN50549-2.

Uses **Modbus TCP** (`pymodbus`): FC03/FC04 read, FC06 write of one register
(recommended by V3.5 §3.4), FC16 only for 32-bit values. UI: CustomTkinter
5.2 (forced Light theme).

## Features / Възможности

- All writable settings from V3.5 + V3.2-only supplements, organised in groups.
- Bulgarian name + detailed explanation (hover tooltip and a side panel) for
  every setting: meaning, range, unit, scaling, raw value, document page.
- Dropdowns for enums and grid standards (appendices); checkboxes for
  bitfields; high/low-byte dropdowns where a register packs two codes.
- Monitor pages: realtime values, status/alarms/faults decoded from the
  appendices, battery/BMS, device info, energy counters, …
- Per-setting read and write with read-back verify. Group read.
- Batch apply of a saved profile: different IPs in parallel, IDs on the same
  IP serially; up to 2 retries; verify / verify-only / dry-run; timeout in
  `settings.json`.
- **Connect never reads or writes** — it only opens the TCP session.

## Install & Run

### Windows

1. **Double-click `Start.bat`** (needs [Python 3](https://www.python.org/downloads/);
   enable “Add python.exe to PATH”).
2. **Or download the `.exe`** (no Python required) from
   [GitHub Actions → Windows Build](https://github.com/nhsqr/solis-modbus-config/actions),
   artifact `SolisModbusConfig-windows`.

   Local packaging: double-click `build-windows.bat`. Output:
   `dist\SolisModbusConfig.exe`.

### macOS

Double-click `Start.command` (needs Python 3). If macOS blocks it:
**System Settings → Privacy & Security → Open Anyway**, or:

```bash
chmod +x Start.command
xattr -d com.apple.quarantine Start.command
```

### Linux

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk
chmod +x Start.sh
./Start.sh
```

On GNOME / Zorin (Wayland + IBus) the app sets `XMODIFIERS=@im=none` for its
own process so start-up stays fast. Set `SOLIS_KEEP_XIM=1` to keep IBus.

Manual:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app
```

Defaults: host `192.168.85.104`, port `502`, unit id `1`, timeout 3 s
(0.5–60, stored in `settings.json`).

## Safety

Writing a setting changes the inverter. Dangerous/service registers
(restart, restore, tests, …) ask for confirmation. **Dry-run** and
**Verify only** never send writes. Changing the national grid standard
(43068) can reset protection thresholds to that standard’s defaults.

Записът променя инвертора. Сервизните регистри искат потвърждение.
„Dry-run“ и „Само проверка“ никога не записват.

## Register map

The catalog `data/registers.json` is generated from the official PDFs
(V3.5 primary; V3.2 fills addresses V3.5 does not define) by
`tools/extract_tables.py`, `tools/extract_appendices.py` and
`tools/build_catalog.py`. See `docs/COMPLETENESS.md` for per-table counts.

Scaling of 43090–43105 depends on **43081** (V3.2 “Grid standard parameters
accuracy”): mode 1 = 0.1 V / 10 ms / 0.01 Hz (V3.5 table); mode 0 = 1 V /
100 ms / 0.1 Hz. The app uses the documented V3.5 scale until 43081 has
been read.

U32/S32 values are **high word first**. Query interval > 300 ms; control
interval > 700 ms (V3.5 §3). One TCP session per logger.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Unit tests plus end-to-end tests against an in-memory fake Modbus server
(no network, no real inverter).

## License / disclaimer

The Solis protocol documents remain the property of Ginlong / Solis.
This tool is an independent helper; use it at your own risk and only on
inverters you are authorised to configure.
