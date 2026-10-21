# Acoustic simulator

The acoustic simulator is for ranking microphone-array geometries and direction-finding settings in a simulated exhibition hall; the Heimdall hardware, firmware and console work does not depend on it.

This page is for engineers who want to run the simulator or judge how far to trust it. It has three parts; the live simulator and the batch sweep share one physical model in [acoustic_utils.py](../acoustic_utils.py):

| Part | Code | Output |
| --- | --- | --- |
| Live simulator, `Simulator` screen (`#sim`) | [sim_server.py](../sim_server.py) (`POST /simulate`), [app/js/sim/](../app/js/sim/) | One trial per run: estimate and error, SRP map, audio, impulse response, 3D scene |
| Batch sweep | [run_comparison.py](../run_comparison.py) | Four built-in geometries over a fixed set of conditions: CSV and plots |
| `Beam pattern` screen (`#beam`) | [app/js/beam/pattern.js](../app/js/beam/pattern.js) | Theoretical narrowband array response, computed in the browser |

The emulators behind the Hardware screen use their own models; they are summarised in [section 5](#5-hardware-free-emulators).

## 1. What it is for

Use it to rank array layouts (microphone count, aperture, shape) under matched conditions; to see how the direction-of-arrival (DOA) error changes with reverberation, drone level, crowd and PA noise, frequency band, integration time and hardware impairments; to check spatial aliasing and beam width against spacing and frequency; and to hear what the array locks onto.

Do not use it for absolute accuracy, detection range or detection probability at a real venue, classifier accuracy, latency, tracking of several sources, or parity with the deployed ADAU1467 program and its 2-D beam table. [Section 6](#6-limitations-and-how-far-to-trust-results) gives the limits and the offsets to apply.

## 2. How to run it

### Live simulator

Start the backend from the repository root (it loads `audio/drone.wav` and `audio/crowd.wav` relative to the working directory and uses synthetic signals when they are missing), then the frontend in a second terminal. Setup is in the [README](../README.md).

```powershell
.\.venv\Scripts\python.exe sim_server.py
cd app
npm.cmd run dev
```

Open the URL Vite prints with `#sim` appended (http://localhost:5173/#sim by default), set the scenario and press `Run simulation`. Rarely used settings are under `More settings`. `Save preset` and `Load preset` use JSON files; `Copy link` puts the settings into a `#preset=…` URL. The `Beam pattern` screen needs no backend.

The backend listens on `127.0.0.1:8766`. `POST /simulate` takes the fields of `SimRequest` in [sim_server.py](../sim_server.py), all optional with the defaults given below; `seed` −1 (the default) draws a new seed and returns it as `seed_used`. `GET /materials` lists the material names.

### Batch sweep

```powershell
.\.venv\Scripts\python.exe run_comparison.py --test
```

The sweep runs the four built-in geometries with 12 microphones, every condition with seeds 0 and 1: RT60 0, 0.5, 0.8, 1.0 and 1.5 s at azimuths 0-350° in 10° steps; elevations 10-70° in 10° steps; drone levels 85, 80, 78, 75 and 70 dB SPL; and azimuths 0-350° with crowd and PA on and the drone at 70 dB SPL. Unless varied, the drone is at azimuth 60° and elevation 30°, with RT60 1.0 s, 78 dB SPL and no interference. The full sweep is 1,808 trials and takes hours. `--test` runs 76 trials in minutes: azimuths 0, 60, 90, 180 and 270°, RT60 0 and 1.0 s, levels 80 and 70 dB, elevations 20 and 50°, seed 0.

Results go to `results/` in the working directory: `metrics.csv` (one row per trial: geometry, conditions, true and estimated angles, azimuth, elevation and total angular error, seed, and the two ML columns), `spectra.json` (SRP grids of selected trials) and PNG plots (error polar plots and heat maps, performance summary, SRP spectra, room scene). The script appends to the CSV and skips trials already in it. Trials are matched by geometry, RT60, level, interference, angles and seed only, so move or delete the CSV before rerunning with other flags.

| Flag | Default | Effect |
| --- | --- | --- |
| `--test` | off | Reduced sweep, 76 trials |
| `--plots-only` | off | Redraw the plots from the existing CSV |
| `--materials-profile exhibition_hall` | off | Per-wall materials instead of RT60; the RT60 axis collapses to the measured RT60 of the profile (about 0.73 s at the default atmosphere); output names get the suffix `_exhibition_hall` |
| `--temperature`, `--humidity`, `--temp-gradient` | 20 °C, 50 %, 0 °C/m | Atmosphere (section 3.1) |
| `--crowd-model`, `--n-plane-waves` | `point_source`, 64 | `plane_wave` gives a diffuse crowd field (section 3.4) |
| `--crosstalk-model` | `simple` | `simple` applies no crosstalk in the batch; `fir_capacitive` adds the high-pass neighbour leak with `--crosstalk-corner-hz` (500 Hz) and `--crosstalk-coupling-db` (−40 dB) |
| `--crosstalk-fir-path` | none | Measured FIR (`.json` list or `{"fir": [...]}`, `.npz`/`.npy` with `fir`) instead of the high-pass; an unreadable file is ignored silently |
| `--ml-preview`, `--ml-bit-depth`, `--ml-feature-bit-depth`, `--ml-n-mels` | off, 8, 8, 64 | Fill the `ml_path_snr_db` and `feature_snr_db` columns (section 3.9) |
| `--fmin`, `--fmax`, `--harmonic-comb`, `--drone-fundamental` | 200 Hz, 2000 Hz, off, 200 Hz | SRP-PHAT band and harmonic comb (section 3.6) |

### Tests

Run `.\.venv\Scripts\python.exe -m pytest -q tests\test_parity.py`. [tests/test_parity.py](../tests/test_parity.py) keeps the live and batch paths in step. It runs the same clean trial (stacked rings, batch room, 4 m, synthetic drone, seed 0, no interference or impairments) through `sim_server.run_live_trial()` and `run_comparison.run_single_trial()` and requires both estimates to agree within 1° in azimuth and elevation at RT60 1.0 s, anechoic, and with a non-default atmosphere. A failure usually means one path changed its random-number order, STFT settings, geometry defaults or absorption model. The file also checks that setting the default crowd model or band explicitly leaves the live result unchanged, and these model invariants: trajectory chunks add up to the drone signal; a moving source at 0 m/s equals the static case; the plane-wave crowd is less coherent between microphones than a single source; capacitive crosstalk leaks less at low frequencies and equals flat crosstalk at a 0.1 Hz corner; 16-bit ML audio beats 8-bit by more than 30 dB; narrowing the band or adding the comb removes bins, and the comb still finds a harmonic source; custom coordinates reproduce the ring; microphones outside the room are clipped; the delay-and-sum modes land within 22.5° of the source and report clamped delays.

[tests/_smoke_live.py](../tests/_smoke_live.py) exercises the same features over HTTP against a running server: `.\.venv\Scripts\python.exe tests\_smoke_live.py`.

## 3. What is modelled

Coordinates are in metres with z up. Azimuth runs counter-clockwise from +x seen from above; elevation is measured up from the horizontal plane. The array centre is at the room centre, 1.0 m above the floor. Audio runs at 16 kHz. Values are live defaults with the UI range in brackets; section 4 lists where the batch differs.

### 3.1 Room and atmosphere

Rooms are pyroomacoustics shoeboxes simulated with the image-source method (ISM).

| Setting | Default (UI range) |
| --- | --- |
| Length × width × height | 50 × 40 × 12 m (10-100, 10-80, 4-15 m) |
| Absorption | `From RT60`, 1.5 s (0-2.5 s), or `Per-wall materials` |
| Temperature, humidity, vertical gradient | 20 °C (0-40), 50 % (10-90), 0 °C/m (−5 to 5) |

In RT60 mode, `pra.inverse_sabine` converts RT60 into one absorption coefficient and an ISM order, and the order is capped (section 4). The cap drops late reflections, so above about 1.5 s the live reverberation tail can be shorter than the nominal RT60. RT60 0 gives a free field: no reflections, no air absorption, c = 343 m/s.

In materials mode each surface gets a pyroomacoustics material with octave-band absorption (125 Hz-8 kHz; unknown names fall back to a uniform 0.3). Scattering is not modelled. The response reports `rt60_actual`, estimated from a 20 dB decay of the drone-to-microphone-1 impulse response. The `Exhibition hall` button (shown in materials mode) is the known baseline: hairy carpet floor, fissured-tile ceiling, plasterboard on the east, west and north walls, cotton curtains (`curtains_cotton_0.5`) on the south wall, 22 °C, 55 % RH and +1.5 °C/m. It also resets the band to 200-2000 Hz flat, the crowd to point sources, crosstalk to flat and the ML preview to off.

Shoebox rooms apply pyroomacoustics air absorption, an octave-band table with two temperature classes (below 15 °C, otherwise 20 °C) and three humidity classes (below 50 %, 50-70 %, 70 % and above), and propagate at the speed of sound for the set temperature and humidity (344.0 m/s at 20 °C and 50 %); the estimators assume 343 m/s. A vertical temperature gradient bends the sound path. The model shifts the drone height by

$$\Delta z = -\frac{0.6\,\mathrm{d}T/\mathrm{d}z}{2c}\,L^2\sin\alpha$$

with L the horizontal range and α the true elevation; for a source above the array, a positive gradient (warmer ceiling) lowers the apparent elevation. `Run details` shows the resulting bias when it reaches 0.05°. At 1-2 °C/m over about 8 m it is a fraction of a degree, well below the 10° elevation grid step.

### 3.2 Array geometries

| Geometry (UI name) | Layout | `radius` means |
| --- | --- | --- |
| `UCA` (Circular ring) | N microphones on a horizontal ring | ring radius |
| `CROSS` (Standing cross) | N/2 along x and N/2 along z through the centre, centre point excluded | half arm length |
| `ULA` (Linear) | N microphones evenly along x | half length |
| `CYLINDER` (Stacked rings) | two horizontal rings of N/2, `ring_separation` apart, top ring rotated by half the angular spacing | ring radius |
| `HEIMDALL` (Heimdall, 44 microphones) | deployed layout in the vertical y-z plane, facing 0° azimuth, so Heimdall's right is negative azimuth; sent to the backend as `CUSTOM` | — |
| `CUSTOM` (Custom coordinates) | offsets [x, y, z] in metres from the centre; at least two, all distinct | — |
| `MICCANVAS` (API only) | 15 microphones: centre, 6 on a 10 mm radius, 8 on a 20 mm radius rotated 22.5° | — |

Defaults are 12 microphones (4-32), radius 0.15 m (0.03-1.0 m) and ring separation 0.12 m (0.02-0.5 m); the batch always uses them. The default ring has 7.8 cm between neighbours and aliases above about 2.2 kHz. The custom editor adds and removes microphones, starts from any built-in geometry and imports JSON (`[[x, y, z], ...]`), comma lists or one `x y z` per line. Microphones outside the room are moved to 0.3 m from the walls and listed in `mic_clip_notes`. The Heimdall positions are a copy of those in `beam_table_2d.json`, kept in [app/js/shared/heimdall-array.js](../app/js/shared/heimdall-array.js); update the copy when the contract changes.

### 3.3 Drone source

The drone is one omnidirectional point at the set azimuth, elevation and distance from the array centre (60°, 30° and 6 m; distance 2-20 m), kept 0.3 m from the walls. The error is computed against the requested direction even when the walls move the source, so keep the source inside the room.

The live simulator plays a random segment of `audio/drone.wav` (29.8 s, 16 kHz mono, repeated if too short). Without that file, and always in the batch, the drone plays tones at 150, 300, 600 and 900 Hz with random phases plus Gaussian noise at 0.3 relative amplitude, peak-normalised. WAV files are mixed to mono, resampled to 16 kHz by linear interpolation and normalised to unit RMS. Every source signal is scaled to its level in dB SPL at 1 m (reference 20 µPa; drone 78 dB, UI 60-95 dB). The synthetic signal is peak-normalised, so its RMS level is about 9 dB below the set level; batch drone levels are lower than their labels by that much. The direct path falls as 1/r, so 78 dB at 1 m arrives at about 62 dB at 6 m. Each run simulates 100 ms more than the window and discards the start to remove impulse-response onset artefacts.

`Moving source` (live only) moves the drone during the window, starting at the set position:

| Setting | Default (UI range) | Meaning |
| --- | --- | --- |
| `Trajectory` | `straight` | `straight`: constant horizontal velocity along the heading at constant height; `arc`: horizontal circle around the array at the starting radius |
| `Speed` | 5 m/s (0-30) | along the path; a negative value (API only) reverses the direction |
| `Heading` | 0° (0-355) | straight line only; 0° = +x, 90° = +y |
| `Trajectory chunks` K | 8 (4-16) | number of static positions, one per chunk mid-time |

The drone signal is split into K time-gated copies joined by 10 ms sin²/cos² crossfades that add up to exactly one, and each copy plays from its own static position within one room simulation. The arrival delay therefore changes in steps, which approximates Doppler shift and direction drift within the window. Few chunks at high speed produce staircase artefacts; raise K. Speed 0 reproduces the static result.

### 3.4 Crowd and PA speakers

`Crowd and PA speakers` (on by default) adds interference:

| Setting | Default (UI range) |
| --- | --- |
| Crowd talkers | 30 (0-100), 1.5 m high, 67 dB SPL at 1 m (55-80) |
| PA speakers | 8 (0-12), 3.0 m high, 80 dB SPL at 1 m (70-100) |
| Plane waves, in the plane-wave model | 64 (8-128) |

Placement follows the run's seed. 60 % of the talkers cluster around 4-8 random booth centres (Gaussian spread 2 m), the rest spread uniformly; none is within 2 m horizontally of the array, and all are 0.3 m from the walls. PA speakers stand at random points along random walls. Talker i plays the i-th window-length segment of `audio/crowd.wav` (180 s); beyond the end of the file, segments are circularly shifted copies. PA speakers take segments from the start of the same file, so they partly repeat the first talkers' audio. Without `crowd.wav` every source plays independent Gaussian noise.

`Crowd model` = `Plane-wave diffuse field` replaces the talkers with N plane waves from random directions, each fed its own crowd segment and added to the microphone signals after the room simulation with frequency-domain fractional delays. Each wave has amplitude 1/√N, so the field reaches the array at the crowd level (67 dB SPL) whatever the talker count; crowd noise levels therefore differ between the two models. The coherence between microphones approaches the isotropic sinc(kd) curve as N grows. PA speakers stay point sources.

### 3.5 Microphones and impairments

Microphones are omnidirectional points. Self-noise is independent white Gaussian noise on each channel at the `Microphone noise floor` (30 dB SPL, 20-50). Optional impairments (live only, under `Hardware impairments`) are applied after the room simulation in this order:

| Impairment | Model | Parameters |
| --- | --- | --- |
| Gain and phase mismatch | per channel: gain, fractional delay (31-tap Blackman-windowed sinc) and DC offset, drawn each run | σ = 0.5 dB, 0.2 samples (12.5 µs), 10 µPa |
| Crosstalk, `Flat` | each channel picks up its two neighbours by channel index, wrapping round, times the coupling | −40 dB (−60 to −20) |
| Crosstalk, `Capacitive (FIR)` | as flat, through a first-order high-pass (6 dB/octave below the corner); a measured FIR in `crosstalk_fir_path` (API or batch flag) replaces the high-pass | corner 500 Hz (20-4000) |
| Codec quantisation | each channel rounded to N bits; full scale is the joint peak of all channels | 24, 16 (default), 12 or 8 bits |

`Apply the hardware-realistic set` turns on mismatch, crosstalk at −40 dB and 16-bit quantisation. Channel-index neighbours are not necessarily physical neighbours in the Heimdall and custom layouts. A corner near 0 Hz makes the capacitive model equal to the flat one.

### 3.6 SRP-PHAT and the DOA band

The reference estimator is pyroomacoustics SRP-PHAT. For each candidate direction it adds up the phase-only (PHAT-weighted) cross-spectra of the microphone pairs steered to that direction; the strongest direction is the estimate.

| Setting | Value |
| --- | --- |
| STFT | 1024-point FFT (64 ms), hop 512, bin spacing 15.625 Hz |
| Search grid | 72 azimuths (5° steps) × 19 colatitudes (10° steps, 0-180°) = 1,368 directions; elevation = 90° − colatitude |
| Band | bins centred in [fmin, fmax]; default 200-2000 Hz = 117 bins; UI 50-4000 Hz and 200-7000 Hz in 50 Hz steps |
| Harmonic comb | only bins within ±10 Hz of n·f0, n = 1-20, inside the band; f0 200 Hz (80-400); 14 bins at the defaults |
| Integration window | live 50-1000 ms (default 1000); batch 1 s |
| Outputs | estimate, 19 × 72 power grid, top 3 peaks at least 15° apart with their level relative to the first |

The 200 Hz lower edge drops frequencies where small arrays have almost no resolution; the 2000 Hz upper edge stays below the aliasing frequency of the default ring. A typical band for small drones is 300-2500 Hz, which drops HVAC rumble. The comb imitates a matched filter on a known blade-pass frequency, with rectangular weights. If no bin survives (for example f0 outside the band) it silently uses the flat band; `n_freq_bins` in the response and in `Run details` shows the bins actually used. Live and batch pick bins with the same function, `build_freq_bin_mask`. If fmax ≤ fmin the UI sets fmax to fmin + 50 Hz.

### 3.7 Delay-and-sum modes

`Direction estimate` offers two delay-and-sum modes that imitate a DSP beamformer:

| Mode | Beams | Operations per sample |
| --- | --- | --- |
| `steered_das`, scan and lock | listed angles, default 0-315° in 45° steps; latency = angles × integration window | microphones × taps |
| `beam_bank_das`, bank of K fixed beams | K = 8 (4-16), equally spaced over 360° | microphones × taps × K |

Both steer in azimuth only, at 0° elevation, with delays from the x-y microphone coordinates relative to the centroid (z is ignored), shifted so that the smallest is zero. Each delay is a windowed-sinc fractional delay (16 taps, 8-32). Delays above the FracDelay maximum (8 samples, 4-64) are clamped and listed. The strongest beam (RMS) is the estimate, its elevation is reported as 0°, and its output is the beamformed audio. Delays and clamping are counted at the 16 kHz simulation rate; the `DSP sample rate` (48 kHz) only converts the operations estimate, shown as a share of the ADAU1467 budget of 6,144 operations per sample. These modes do not reproduce the deployed 2-D steering table; the acoustic emulator does (section 5).

### 3.8 Audio outputs

Each run returns up to four 16-bit WAV clips as long as the integration window: `One microphone` (microphone 1), `Unsteered sum` (mean of all microphones), `Beamformed` and `ML input` (section 3.9). In SRP-PHAT mode the beamformed clip is an integer-sample delay-and-sum toward the estimated direction, not the true one; in the delay-and-sum modes it is the strongest beam.

With `Normalise downloaded audio` on (default), the first three clips share one peak scale at 0.9 of full scale, so the beamforming gain stays audible; the ML clip is scaled on its own. With it off, full scale is a fixed 1 Pa (about 94 dB SPL), so levels compare across runs, for example in a 1/r check. Downloads are named `raw_<geometry>_<seed>.wav`, `unsteered_…`, `beamformed_…` and `ml_preview_…`.

### 3.9 ML-path preview

`Render the ML input` (batch: `--ml-preview`) previews what an audio classifier on the MAX78002 would receive:

1. The beamformed audio is quantised to 8 or 16 bits (default 8) with its own peak as full scale. `ml_path_snr_db` is the ratio of signal to quantisation error.
2. Log-mel features are computed from the original and the quantised audio: Hann window, 512-point FFT, hop 256, triangular mel filters, 10·log10 power, 64 bands (32-96). Live, the mel range follows the DOA band; the batch uses 200-2000 Hz.
3. The features of the quantised audio are quantised to 8 bits (16 bits leaves them unchanged). `feature_snr_db` compares them with the features of the original audio.

Live, the response adds the `ML input` clip and a spectrogram image; the batch fills two CSV columns. This is a proxy for a classifier front end: it shows quantisation loss, not classification accuracy.

### 3.10 Beam pattern screen

The `Beam pattern` screen computes the narrowband delay-and-sum response of any geometry above, including Heimdall and custom coordinates:

$$g(\hat u) = \frac{1}{M}\left|\sum_{m=1}^{M} e^{\,jk\,\mathbf r_m\cdot(\hat u-\hat u_0)}\right|,\qquad k=\frac{2\pi f}{c},\quad c = 343\ \mathrm{m/s}$$

with $\hat u_0$ the steering direction. Inputs are the steering azimuth and elevation and the frequency (50 Hz-20 kHz, default 2 kHz). The screen shows the pattern in 3D (5° grid, −30 to 0 dB), azimuth and elevation cuts through the steered beam with their −3 dB widths (2° steps), the aperture, the nearest spacing, the wavelength, the spatial-alias limit $c/(2d_{\min})$ with a warning above it, and the beam solid angle.

### 3.11 What the Simulator screen shows

The result column shows the estimate and its error against the requested direction; the SRP map (azimuth × elevation, truth as a circle, estimate as a cross) with the top three candidates; for the delay-and-sum modes, a polar plot of beam powers and the DSP budget; the first 125 ms of the drone-to-microphone-1 impulse response; the audio player and downloads; the ML spectrogram; and `Run details` (seed used, levels, band and bin count, measured RT60, atmospheric bias, ML SNRs). The 3D scene shows the room scaled so that its longest side is 6 units, the array, the drone with its true direction, the estimated direction (dashed), crowd talkers (dots), PA speakers (cubes), up to six image sources of the drone, and the trajectory, in `Angled` or `Plan` view. Before a run and after any change, the scene previews the new set-up and the previous result is marked stale.

## 4. Batch and live differences

The two paths agree only in the clean case the parity test covers. With their defaults they do not reproduce each other.

| Aspect | Batch (`run_comparison.py`) | Live (`sim_server.py`) |
| --- | --- | --- |
| Room, array centre | 20 × 15 × 10 m, (10, 7.5, 1.0) m | 50 × 40 × 12 m, room centre at 1.0 m |
| Geometries | four built-in, 12 microphones | also Heimdall, custom and `MICCANVAS`; 4-32 microphones |
| Absorption | RT60 sweep, or `--materials-profile` | RT60 1.5 s, or materials |
| ISM order cap | 10 (6 with materials) | 6 |
| Inverse Sabine failure | trial skipped | absorption 0.5, order 3 |
| Source distance | 4 m | 6 m |
| Drone signal | synthetic tones | `drone.wav`, synthetic fallback |
| Moving source | no, by design: the sweep ranks geometries on static sources | optional |
| Crowd, PA | 12 Gaussian-noise talkers at 1.2 m, 4 Gaussian-noise PA speakers 1 m below the ceiling, same placement rules | 30 `crowd.wav` talkers at 1.5 m, 8 PA speakers at 3 m |
| Integration window | 1 s | 50-1000 ms |
| Impairments | none, except capacitive crosstalk whenever `--crosstalk-model fir_capacitive` | mismatch, flat or capacitive crosstalk, quantisation, all optional |
| Estimators | SRP-PHAT | SRP-PHAT and two delay-and-sum modes |
| ML preview mel band | 200-2000 Hz | the DOA band |
| Seeds | 0 and 1 per condition (0 with `--test`) | new random seed per run (reported), or a fixed seed 0-999 |

To reproduce a batch row live, set the room to 20 × 15 × 10 m, distance 4 m, the same RT60 and angles, interference off and the same seed; the drone signal still differs while `drone.wav` is present.

## 5. Hardware-free emulators

The Hardware screen can run against two emulators instead of the board. They answer the firmware commands through the same parser and WebSocket path as serial hardware and are meant for testing the console, the protocol and the tracker. They do not use the room simulator above. The steps to start them are in the [README](../README.md#without-hardware).

The fast emulator ([heimdall_transport.py](../heimdall_transport.py); `sim_server.py --emulator`, or `Emulator` in the connection panel) makes up detector levels from a simulated flight: a Gaussian beam response (σ = 18°) around the target direction, amplitude proportional to 1/r referenced to 30 m and capped at close range, a little random noise, and 2.8 ms per sector. The grid can be 1×1 to 20×20 with any limits, using the cell-centre convention of `beamforming_mathematics/hemisphere_scan_visualizer.py`. The connection panel defaults to the deployed 7×7 grid over ±40° and reports the selected DSP export; `--emulator` starts with 7×7 over ±70° azimuth and ±60° elevation and a generic per-microphone program. The tracker receives levels only, never the trajectory. Flight profiles at 1×: crossing pass (smooth lateral pass, about 14 m/s, 36-60 m), approach and retreat (about 13 m/s, 16-83 m), orbit (offset orbit, about 9 m/s, 27-52 m), patrol (slower multi-axis route, about 8 m/s, 31-68 m), evasive stress (up to about 27 m/s, wide angular motion, a 0.9 s dropout every 17 s) and the legacy angular sweep (fixed 30 m range, sinusoidal angles, for regression comparison).

`Flight speed` (0.25-3×) scales time: at 2×, speeds double and dropout intervals halve. Adaptive `Track` follows the firmware policy: one complete search, two local cross confirmations, five-sector tracking with two-pass movement hysteresis, a local 3×3 reacquisition after three failed passes, and a global search every 50 tracking passes.

The acoustic emulator ([heimdall_acoustic.py](../heimdall_acoustic.py); `Acoustic room` tab, `Prepare scene`, then `Start acoustic emulator`) replays cached detector levels. They come from `drone.wav` and `crowd.wav` at 48 kHz in a pyroomacoustics room with order-2 reflections plus a deterministic diffuse tail calibrated to RT60 1.2 s (measured 1.18-1.20 s), the 44 microphones in DSP order, the deployed beam-table delays, and a 1-4 kHz detector band-pass made of two 129-tap linear-phase FIR stages. That band-pass is the intended detector band, not necessarily the filter in the deployed DSP program. All sectors come from one acoustic scene. Levels are drone 78, crowd 67 and speech 72 dB SPL, with 30 dB SPL microphone noise.

| Scenario | Contents |
| --- | --- |
| Conference room, evasive drone 2× | 20 × 40 × 10 m, vertical array at [10, 0.5, 1] m, 30 s room-bounded evasive loop, diffuse crowd from 12 directions, speech at +25°, 12 m, 1.5 m high |
| Handheld drone at 2 m, drone only | slow 12 s figure-eight at 2 m broadside depth, ±0.75 m sideways, 1.2-2.0 m high |
| Handheld drone at 2 m, crowd and speech | the same motion with the conference crowd and speech |
| Stationary speaker at 2 m, broadside | drone recording only, time-averaged spectrum, for comparison with a physical speaker |

Preparation took 9-94 s on the development machine. Caches (about 1.18 MB) are stored by content under `.cache/heimdall_acoustic/` and reload in tens of milliseconds; changing only the grid reuses the room result (0.4-1.0 s). Grids other than the default 7×7 over ±40° use quantised simulation-only delays. In the handheld scenarios, 85.8 % (drone only) and 79.8 % (crowd and speech) of the 49-sector sweeps landed within one grid cell of the truth (coherent all-sector references: 98.2 % and 91.4 %), and adaptive `Track` had median errors of 7.0° and 8.6°. `Listen` renders 3 s jointly normalised clips (filtered mix, one microphone, unsteered sum, truth-steered beam, selected beam); they are not sample-exact ADAU1467 output. Both emulators show the true direction under `Diagnostics`; it never reaches the tracker.

Use the acoustic emulator for relative sector ranking, reflection and interference stress, and tracking behaviour. Its SPL-to-digital scaling is nominal, so it does not predict detection range or probability, and a moving source interpolates cached waypoint power responses without continuous Doppler phase.

## 6. Limitations and how far to trust results

The simulator is reliable for relative comparisons and trends. It does not predict absolute accuracy or range at a venue; calibrate it against recordings as early as possible.

### What the results support

- Geometry ranking under matched conditions. Differences of 10° or more in mean total angular error should hold on hardware.
- Trends: more reverberation, a quieter drone or more interference increase the error; geometries with vertical aperture (stacked rings, standing cross) resolve elevation, a flat ring and a line do not.
- Mirror ambiguities of planar arrays, which are physics, not software: a horizontal ring cannot tell +θ from −θ elevation and SRP-PHAT picks one; a line has a cone of confusion around its axis; a vertical planar array such as Heimdall cannot tell front from back.
- Spatial aliasing and side-lobe structure against spacing and frequency, the effect of microphone count, radius and ring separation on beam width, and the relative gains from a longer integration window and from beamforming over the unsteered sum.

### What they do not support

- Absolute DOA error, SNR or detection range: levels are in dB SPL, but the drone, crowd and microphone chain are not calibrated against measurements.
- Venue-specific or outdoor behaviour: an empty shoebox is not a hall (that needs measured impulse responses), and there is no ground model, wind noise, turbulence or long-range propagation.
- Latency or DSP load beyond the coarse operations estimate; the simulation runs offline.
- Several simultaneous sources: the estimator assumes one; the top-3 list shows ambiguity, not tracking.
- Statistical confidence from one run: each run is one realisation of noise and placement. Repeat with several seeds; the batch uses two.

### Simplifications

| Area | Model | Consequence and check |
| --- | --- | --- |
| Microphones | omnidirectional points | MEMS packages are close to omnidirectional below about 2 kHz; above that, package, board and housing add directivity. Check that the datasheet directivity stays within ±2 dB over the band used. |
| Distance | exact path lengths, 1/r; estimators assume plane waves | Ignore results with the source closer than 2 m. |
| Room | empty shoebox, octave-band absorption, no scattering, ISM order ≤ 6 live and ≤ 10 batch | No booths, trusses, people or PA rigs; late reverberation is cut. Test in a booth-like room before the venue. |
| Atmosphere | uniform temperature and humidity; tabulated air absorption; first-order vertical gradient | No HVAC pockets, horizontal gradients, wind or turbulence, and absorption changes only across 15 °C, 50 % and 70 % RH. Budget an extra ±1-2° systematic elevation error. |
| Drone | one omnidirectional point playing a recording or four tones | Real drones radiate from several rotors, tonal and broadband, not omnidirectionally, and change with thrust. Record your drone at a fixed RPM and distance and use it as `drone.wav`. |
| Motion | static chunks, live only | The delay changes in steps; there is no rotor radiation pattern. Integrate for less than 200 ms on moving drones, expect extra elevation jitter on hardware, and check against a real fly-past. |
| Crowd | talkers from one 180 s file, reused by the PA speakers, or N plane waves | Point sources are somewhat over-correlated; plane waves leave a finite-N residual and are stationary over the window. Use 64-128 plane waves and treat crowd results as a lower bound on the real error. |
| Noise and mismatch | white Gaussian self-noise; static gain, delay and DC offset | No PDM clock noise, 1/f drift, EMI or time-varying jitter. Raise the noise floor to match measured pickup; measure the real chain with white noise and set the `apply_mic_mismatch_v2` parameters from it. |
| Crosstalk and arithmetic | channel-index neighbours, flat or first-order; double precision except codec and ML quantisation | No multi-pole, non-neighbour, supply or EMI coupling; no fixed-point DSP stages, dither, codec pre-filter or companding. Load measured crosstalk FIRs when they exist. |
| DOA band | rectangular ±10 Hz comb on a fixed f0 | No shaped lobes, f0 tracking, sub-band voting or MVDR. Set the band from recorded drone spectra and track f0 if the RPM varies. |
| Delay-and-sum modes | azimuth only, delays at 16 kHz, windowed-sinc approximation of FracDelay | Not the deployed 2-D table; no fixed-point MAC stages, SafeLoad write timing, MUX switching artefacts or exact SigmaStudio FracDelay response. |
| ML preview | log-mel proxy | No real network, ADC INL, clock jitter or accumulator widths. Budget a few dB beyond the preview SNR. |
| Grid and truth | 5° × 10° grid; error against the requested direction | Errors below half a grid step are invisible; a source moved by the walls gives a wrong error. |

### Calibration offsets

These are rules of thumb from the literature, not measurements on Heimdall. Add them to any number taken from the simulator.

| Quantity | Simulation | Expect in a real hall |
| --- | --- | --- |
| DOA error | for example 3° mean azimuth error | 2-3 times larger (6-10°) |
| Beamforming SNR gain in diffuse noise | 10-15 dB | 3-8 dB, because of self-noise, mismatch and crosstalk |
| Range at which the DOA error stays below 5° | R | 40-60 % of R, because there is no booth scattering and the crowd is milder |
| Elevation error of a flat ring or line | limited by the mirror ambiguity | 5-10° more from backplane reflections and near-field effects; stacked rings are the most robust |

### Validation against hardware

1. Record the drone at 1, 3, 5 and 10 m in a quiet room, with a reference SPL meter.
2. Use the recordings as `audio/drone.wav` in the live simulator (the batch sweep always uses its synthetic signal) and check that geometry rankings and trends hold.
3. Measure the DOA error in a reverberant room (atrium, warehouse, empty canteen) and compare with the simulator at that room's RT60; expect the factor of 2-3 above.
4. Rehearse in a large crowded space, such as a trade-show set-up day. Record, feed the recordings back into the simulator and settle the remaining changes.
5. Reserve 2-3 working days at the venue for integration: PA pickup, EMI from nearby equipment, mounting acoustics. The simulator does not replace this.

### Not modelled

- Inference of a real MAX78002 network; only the log-mel proxy exists.
- Measured crosstalk FIRs from the PCB, and coupling beyond index neighbours.
- Measured room impulse responses in place of the shoebox.
- Fundamental tracking for the harmonic comb, sub-band SRP-PHAT voting and MVDR beamforming.
