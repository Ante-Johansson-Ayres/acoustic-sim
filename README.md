# Acoustic Visualiser (acoustic-sim)

Host software for Heimdall: a FastAPI backend (`sim_server.py`) and a browser GUI (`app/`, Vite and
plain JavaScript). The backend communicates with the MAX78002 firmware over serial, or with an
emulator. The repository also holds the characterisation scripts and an acoustic simulator. The
system documentation is in the parent repository ([README](../README.md)). For how the backend and
the protocol operate, see [REFERENCE.md section 5](../MAX78002/docs/REFERENCE.md#5-host-software-acoustic-sim).

## Setup

Install 64-bit Python 3.14, Node.js 24 and npm 11. Then type:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd app
npm.cmd ci
```

If pip finds no wheel for pyroomacoustics, install the C++ Build Tools.

## Run

```powershell
.\.venv\Scripts\python.exe sim_server.py         # backend on 127.0.0.1:8766; keep it running
cd app; npm.cmd run dev                          # second terminal; open the URL it prints
```

To use the GUI without Node at run time, type `npm.cmd run build` one time. Then type
`.\.venv\Scripts\python.exe -m http.server 8080 --directory app\dist`.

| Screen | Address | Use |
| --- | --- | --- |
| Beam pattern | `#beam` | The array factor of a geometry (also the deployed layout), with polar cuts and the -3 dB width |
| Simulator | `#sim` | One room-acoustics trial: SRP map, estimate and error, audio, impulse response ([docs/SIMULATOR.md](docs/SIMULATOR.md)) |
| Hardware | `#hardware` | The operator console for the board, the fast emulator or the acoustic emulator. Study and Exhibition use the same hardware connection. |
| Study | `#study` | DSP parameter editors and guided measurements |
| Exhibition | `#exhibit` | A full-screen view of the Hardware session, for a booth. `Present` opens it and Esc closes it. |

## Hardware

1. Close all other programs that use the COM port.
2. Open Hardware. Select the port, or type its name. `Rescan` updates the list.
3. Select 921600 baud (115200 for a diagnostic firmware build). Click `Connect`.

At connection, the backend sends `I`. The answer of the firmware sets the grid and checks the DSP
program ([REFERENCE.md section 4.6](../MAX78002/docs/REFERENCE.md#46-records)). The first-start
checks are in [SETUP.md section 6](../MAX78002/docs/SETUP.md#6-start-and-check).

The backend uses the DSP program for which the firmware is built: `HEIMDALL_DSP_EXPORT` if it is
set, else its default in `../MAX78002/project.mk`. From that folder, it reads the contract
(`beam_table_2d.json`), the SigmaStudio export (the Study register map) and `beam_table_2d.h`
(hash check). Restart the backend after you change the selection.

| Console part | Function |
| --- | --- |
| Live | Listens in all bank directions (the fixed beams) at the same time. |
| Diagnostic scans | `Sweep once`, `Continuous` and `Track` move the steered beam in steps. `Stop` sends `X`. |
| Manual beam | This drawer holds one beam. `Hold beam` reads it again at 10, 20 or 50 Hz until you click `Release`. |
| Field | One tile for each beam, in 3D (`Show`: tiles, `Surface` or `Point cloud`) or as a flat `Map`. `Floor` is an absolute colour floor in dBFS. The GUI does not normalise the frames to their own peak, so weak frames stay dark. |
| Source | Marks its answer `LOCKED` (a tracked or Live target), `HOLDING` (a held beam) or `STRONGEST` (the strongest cell of a scan). Hatched cells are more than 5 s old. `Above background` is the level above the median of the map, not a confidence. The system gives the direction only, not the range. |
| Diagnostics | `Direction updates` (the rate of `TARGET_UPDATED`) against `Full scans` (from `TIMING`). `Freeze display` stops only the screen updates. To stop the firmware, click `Stop`. With an emulator: the true source direction (a green cross), and `Listen` for the acoustic emulator. |
| Audio | `Record`, `Stop` and `Save WAV` (48 kHz stereo, with a JSON sidecar file). `Save WAV` first stops a running scan. |

In serial mode, the grid comes from the firmware. To change it, generate the table again and flash
the firmware again. Emulator grids can have 1-20 rows and columns.

## Without hardware

The emulators answer the firmware commands through the same parser and WebSocket as the board.
Their levels are not calibrated. Use them for GUI, protocol and tracking work, not for range. Start
the backend and the GUI as usual. Open `#hardware` and select a tab in the connection panel:

| Tab | Steps | Levels come from |
| --- | --- | --- |
| `Emulator` | Select a flight profile and speed. Click `Start emulator`. | A simulated flight and a Gaussian beam model, without room acoustics |
| `Acoustic room` | Select a scenario. Click `Prepare scene` (9-94 s the first time, then cached under `.cache/heimdall_acoustic/`). Click `Start acoustic emulator`. | A room simulation of the 44 microphones, with the deployed delays |

Both emulators use the selected DSP export. Their default grid is the deployed 7 x 7 grid over
+-40 deg. `sim_server.py --emulator` starts a generic emulator with the backend instead. It uses
7 x 7 over +-70 deg azimuth and +-60 deg elevation, and a generic per-microphone program. The flight
profiles, the scenarios and the limits of both models are in
[docs/SIMULATOR.md section 5](docs/SIMULATOR.md#5-hardware-free-emulators).

## Study

- **Parameters.** The backend makes a register map from the SigmaStudio export (`dsp_registry.py`)
  and serves it at `/dsp/registry`. The editors prepare a gain taper and microphone mutes, FIR
  presets, the time constant, hold and decay of the scan detector, and single raw words. Nothing
  goes to the DSP until you click `Apply`. `Apply` needs the `Enable writes` switch and an idle
  board. The backend checks the writes against the map hash of the firmware. It sends them as
  safeloads of five consecutive words or fewer
  ([REFERENCE.md section 4.12](../MAX78002/docs/REFERENCE.md#412-parameter-access-pw-pr-and-its-gates)).
- **Measurements.** Guided procedures save each run with the settings, map hash and grid, and
  export CSV and JSON. The procedures are: microphone survey, beam cut, detector time constant,
  steering accuracy, array gain, SNR, width, dynamic range, detection range and detector scale.
  They use 5 ms windows and are for quick checks. The scripts below are the reference method.

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/characterise.py` | The tests of the characterisation plan ([CHARACTERISATION_PLAN.md](../MAX78002/docs/CHARACTERISATION_PLAN.md)) |
| `scripts/study_session.py` | Scripted health check |
| `scripts/board_regression.py` | Acceptance checks on the board, and the soak test |
| `run_comparison.py` | Batch sweep of the acoustic simulator ([docs/SIMULATOR.md](docs/SIMULATOR.md)) |

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd app; npm.cmd test; npm.cmd run build
```

## Documents

- [docs/SIMULATOR.md](docs/SIMULATOR.md): the acoustic simulator and the emulators, what they
  model and how far to trust them.
- `docs/archive/`: the early geometry comparison study and old plans, kept for reference.
