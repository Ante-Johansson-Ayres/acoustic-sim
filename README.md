# Acoustic Visualiser (acoustic-sim)

Host software for Heimdall: a FastAPI backend (`sim_server.py`) that talks to the MAX78002
firmware over serial or to an emulator, and a browser GUI (`app/`, Vite and plain JavaScript).
It also holds the characterisation scripts and an acoustic simulator. The system documentation
lives in the parent repository ([README](../README.md)); how the backend and protocol work is
in [REFERENCE.md section 5](../MAX78002/docs/REFERENCE.md#5-host-software-acoustic-sim).

## Setup

64-bit Python 3.14, Node.js 24 and npm 11:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
cd app
npm.cmd ci
```

The C++ Build Tools are needed only if pip finds no wheel for pyroomacoustics.

## Run

```powershell
.\.venv\Scripts\python.exe sim_server.py         # backend on 127.0.0.1:8766; keep it running
cd app; npm.cmd run dev                          # second terminal; open the URL it prints
```

Without Node at run time: `npm.cmd run build` once, then
`.\.venv\Scripts\python.exe -m http.server 8080 --directory app\dist`.

| Screen | Address | For |
| --- | --- | --- |
| Beam pattern | `#beam` | Array factor of a geometry (including the deployed layout), polar cuts with the -3 dB width |
| Simulator | `#sim` | One room-acoustics trial: SRP map, estimate and error, audio, impulse response ([docs/SIMULATOR.md](docs/SIMULATOR.md)) |
| Hardware | `#hardware` | Operator console for the board, the fast emulator or the acoustic room emulator |
| Study | `#study` | DSP parameter editing and guided measurements |
| Exhibition | `#exhibit` | Full-screen view of the Hardware session for a booth; `Present` opens it, Esc returns |

The hardware connection is shared by Hardware, Study and Exhibition.

## Hardware

Open Hardware, pick the port (`Rescan` refreshes the list; a name can be typed), choose 921600
baud (115200 for a diagnostic firmware build) and `Connect`. No other program may hold the COM
port. On connect the host sends `I`, and the firmware's answer sets the grid and checks the DSP
program ([REFERENCE.md section 4.6](../MAX78002/docs/REFERENCE.md#46-records)). First-light
checks are in [SETUP.md section 6](../MAX78002/docs/SETUP.md#6-start-and-check).

The backend follows the DSP program the firmware is built for: `HEIMDALL_DSP_EXPORT`, else its
default in `../MAX78002/project.mk`. From that folder it reads the contract
(`beam_table_2d.json`), the SigmaStudio export (the Study register map) and `beam_table_2d.h`
(hash check). Restart the backend after changing the selection.

Using the console:

- **Live** listens in every bank direction at once. **Diagnostic scans** (`Sweep once`,
  `Continuous`, `Track`) step the fine beam; `Stop` sends `X`. The **Manual beam** drawer holds
  one beam; `Hold beam` re-reads it at 10, 20 or 50 Hz until `Release`.
- The **Field** shows one tile per beam in 3D (`Show`: tiles, `Surface` or `Point cloud`) or as a
  flat `Map`. `Floor` is an absolute dBFS colour floor: frames are not normalised to their own
  peak, so weak frames stay dark.
- The **Source** panel tags its answer `LOCKED` (a tracked or Live target), `HOLDING` (a held
  beam) or `STRONGEST` (the strongest cell of a scan). Hatched cells are more than 5 s old.
  `Above background` is the level above the map's median, not a confidence. The system gives
  direction only, not range.
- **Diagnostics**: `Direction updates` (rate of `TARGET_UPDATED`) against `Full scans` (from
  `TIMING`); `Freeze display` pauses the screen only (use `Stop` to stop the firmware); with an
  emulator, the true source direction (a green cross) and, for the acoustic emulator, `Listen`.
- **Audio**: `Record`, `Stop`, `Save WAV` (48 kHz stereo with a JSON sidecar; the GUI stops a
  running scan first).
- In serial mode the grid is the one the firmware reports; changing it means regenerating the
  table and reflashing. Emulator grids can be 1-20 rows and columns.

## Without hardware

The emulators answer the firmware commands through the same parser and WebSocket as the board.
Their levels are not calibrated: use them for GUI, protocol and tracking work, not for range.
Start the backend and the GUI as usual, open `#hardware` and pick a tab in the connection panel:

| Tab | Steps | Levels come from |
| --- | --- | --- |
| `Emulator` | Pick a flight profile and speed, then `Start emulator` | A simulated flight and a Gaussian beam model; no room acoustics |
| `Acoustic room` | Pick a scenario, `Prepare scene` (9-94 s the first time, then cached under `.cache/heimdall_acoustic/`), then `Start acoustic emulator` | A room simulation of the 44 microphones with the deployed delays |

Both use the selected DSP export and default to the deployed 7 x 7 grid over +-40 deg.
`sim_server.py --emulator` instead starts a generic emulator with the backend (7 x 7 over +-70
deg azimuth and +-60 deg elevation, generic per-microphone program). Flight profiles, scenarios
and the limits of both models are in
[docs/SIMULATOR.md section 5](docs/SIMULATOR.md#5-hardware-free-emulators).

## Study

- **Parameters.** The backend turns the SigmaStudio export into a register map
  (`dsp_registry.py`) served at `/dsp/registry`. Editors stage a gain taper and microphone
  mutes, FIR presets, the scan detector's time constant, hold and decay, and single raw words.
  Nothing is written until `Apply`, which needs the `Enable writes` switch and an idle device.
  Writes are checked against the firmware's map hash and go out as safeloads of at most five
  consecutive words ([REFERENCE.md section 4.12](../MAX78002/docs/REFERENCE.md#412-parameter-access-pw-pr-and-its-gates)).
- **Measurements.** Guided procedures (microphone survey, beam cut, detector time constant,
  steering accuracy, array gain, SNR, width, dynamic range, detection range, detector scale)
  save their runs with the settings, map hash and grid and export CSV and JSON. They use 5 ms
  windows and suit quick checks; the scripts below are the reference method.

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/characterise.py` | The tests of the characterisation plan ([CHARACTERISATION_PLAN.md](../MAX78002/docs/CHARACTERISATION_PLAN.md)) |
| `scripts/study_session.py` | Scripted health check (test H1) |
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
