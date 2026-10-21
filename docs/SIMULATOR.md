# Acoustic simulator

The acoustic simulator ranks microphone array geometries and direction-finding settings in a
simulated exhibition hall. Work on the Heimdall hardware, firmware and console does not depend on
it. This page is for engineers who run the simulator or judge how far to trust it.

The simulator has three parts. The live simulator and the batch sweep share one physical model, in
[acoustic_utils.py](../acoustic_utils.py):

| Part | Code | Output |
| --- | --- | --- |
| Live simulator, `Simulator` screen (`#sim`) | [sim_server.py](../sim_server.py) (`POST /simulate`), [app/js/sim/](../app/js/sim/) | One trial per run: estimate and error, SRP map, audio, impulse response, 3D scene |
| Batch sweep | [run_comparison.py](../run_comparison.py) | Four built-in geometries over a fixed set of conditions: CSV and plots |
| `Beam pattern` screen (`#beam`) | [app/js/beam/pattern.js](../app/js/beam/pattern.js) | Theoretical narrowband array response, calculated in the browser |

The emulators of the Hardware screen use their own models ([section 5](#5-hardware-free-emulators)).

## 1. What it is for

Use the simulator to rank array layouts (number of microphones, aperture, shape) under the same
conditions. It shows how the direction-of-arrival (DOA) error changes with reverberation, drone
level, crowd and PA noise, frequency band, integration time and hardware impairments. It also shows
spatial aliasing and beam width against spacing and frequency, and lets you hear what the array
locks onto.

Do not use it for absolute accuracy, detection range or detection probability at a real venue. Do
not use it for classifier accuracy, latency or tracking of several sources. It does not reproduce
the deployed ADAU1467 program and its 2-D beam table.
[Section 6](#6-limitations-and-how-far-to-trust-results) gives the limits and the offsets to apply.

## 2. How to run it

### Live simulator

Start the backend from the repository root. It loads `audio/drone.wav` and `audio/crowd.wav`
relative to the working directory, and uses synthetic signals if they are missing. Then start the
GUI in a second terminal. The setup is in the [README](../README.md).

```powershell
.\.venv\Scripts\python.exe sim_server.py
cd app
npm.cmd run dev
```

Open the URL that Vite shows, with `#sim` added (http://localhost:5173/#sim by default). Set the
scenario and click `Run simulation`. Rarely used settings are under `More settings`. `Save preset`
and `Load preset` use JSON files. `Copy link` puts the settings into a `#preset=…` URL. The
`Beam pattern` screen does not need the backend.

The backend listens on `127.0.0.1:8766`. `POST /simulate` takes the fields of `SimRequest` in
[sim_server.py](../sim_server.py). All fields are optional, with the defaults given below. With
`seed` −1 (the default), the backend uses a new random seed and returns it as `seed_used`.
`GET /materials` lists the material names.

### Batch sweep

```powershell
.\.venv\Scripts\python.exe run_comparison.py --test
```

The sweep runs the four built-in geometries with 12 microphones. It runs each condition with seeds
0 and 1:

- RT60 0, 0.5, 0.8, 1.0 and 1.5 s, at azimuths 0-350° in 10° steps,
- elevations 10-70° in 10° steps,
- drone levels 85, 80, 78, 75 and 70 dB SPL,
- azimuths 0-350°, with crowd and PA on and the drone at 70 dB SPL.

The values that a condition does not change are: drone azimuth 60°, elevation 30°, RT60 1.0 s,
78 dB SPL and no interference. The full sweep has 1,808 trials and takes hours. `--test` runs 76
trials in minutes, with seed 0. It uses azimuths 0, 60, 90, 180 and 270°, RT60 0 and 1.0 s, levels
80 and 70 dB, and elevations 20 and 50°.

The results go to `results/` in the working directory. `metrics.csv` has one row per trial:
geometry, conditions, true and estimated angles, azimuth, elevation and total angular error, seed
and the two ML columns. `spectra.json` has the SRP grids of selected trials. The PNG plots are error
polar plots and heat maps, a performance summary, SRP spectra and the room scene. The script adds
to the CSV and skips the trials that are already in it. It matches trials only by geometry, RT60,
level, interference, angles and seed. Thus, before you run it again with other flags, move or
delete the CSV.

| Flag | Default | Effect |
| --- | --- | --- |
| `--test` | off | Reduced sweep, 76 trials |
| `--plots-only` | off | Draw the plots again from the existing CSV |
| `--materials-profile exhibition_hall` | off | Materials for each wall instead of RT60. The RT60 axis has only one value: the measured RT60 of the profile (approximately 0.73 s at the default atmosphere). Output names get the suffix `_exhibition_hall`. |
| `--temperature`, `--humidity`, `--temp-gradient` | 20 °C, 50 %, 0 °C/m | Atmosphere (section 3.1) |
| `--crowd-model`, `--n-plane-waves` | `point_source`, 64 | `plane_wave` gives a diffuse crowd field (section 3.4) |
| `--crosstalk-model` | `simple` | `simple` applies no crosstalk in the batch sweep. `fir_capacitive` adds the high-pass leak from the neighbours, with `--crosstalk-corner-hz` (500 Hz) and `--crosstalk-coupling-db` (−40 dB). |
| `--crosstalk-fir-path` | none | A measured FIR (`.json` list or `{"fir": [...]}`, `.npz`/`.npy` with `fir`) instead of the high-pass. The sweep ignores an unreadable file and gives no warning. |
| `--ml-preview`, `--ml-bit-depth`, `--ml-feature-bit-depth`, `--ml-n-mels` | off, 8, 8, 64 | Fill the `ml_path_snr_db` and `feature_snr_db` columns (section 3.9) |
| `--fmin`, `--fmax`, `--harmonic-comb`, `--drone-fundamental` | 200 Hz, 2000 Hz, off, 200 Hz | SRP-PHAT band and harmonic comb (section 3.6) |

### Tests

[tests/test_parity.py](../tests/test_parity.py) checks that the live and batch paths agree. Run it
with `.\.venv\Scripts\python.exe -m pytest -q tests\test_parity.py`. It runs the same clean trial
through `sim_server.run_live_trial()` and `run_comparison.run_single_trial()`: stacked rings, batch
room, 4 m, synthetic drone, seed 0, no interference or impairments. Both estimates must agree within
1° in azimuth and elevation in three cases: RT60 1.0 s, anechoic, and a non-default atmosphere. A
failure usually means that one path changed its random-number order, STFT settings, geometry
defaults or absorption model.

The file also checks that the live result does not change when the default crowd model or band is
set explicitly. It also checks these model invariants:

- The trajectory chunks add up to the drone signal.
- A moving source at 0 m/s gives the static result.
- The plane-wave crowd is less coherent between microphones than a single source.
- Capacitive crosstalk leaks less at low frequencies, and equals flat crosstalk at a 0.1 Hz corner.
- 16-bit ML audio is better than 8-bit audio by more than 30 dB.
- A narrower band or the comb removes bins, and the comb still finds a harmonic source.
- Custom coordinates reproduce the ring.
- The simulator moves microphones that are outside the room into the room.
- The delay-and-sum modes give estimates within 22.5° of the source and report clamped delays.

[tests/_smoke_live.py](../tests/_smoke_live.py) tests the same features over HTTP, against a running
server: `.\.venv\Scripts\python.exe tests\_smoke_live.py`.

## 3. What is modelled

Coordinates are in metres, with z up. Azimuth goes counter-clockwise from +x, seen from above.
Elevation is the angle up from the horizontal plane. The array centre is at the room centre, 1.0 m
above the floor. The audio sample rate is 16 kHz. The values are live defaults, with the GUI range
in brackets. Section 4 lists the differences in the batch sweep.

### 3.1 Room and atmosphere

Rooms are pyroomacoustics shoeboxes (rectangular rooms), simulated with the image-source method
(ISM).

| Setting | Default (GUI range) |
| --- | --- |
| Length × width × height | 50 × 40 × 12 m (10-100, 10-80, 4-15 m) |
| Absorption | `From RT60`, 1.5 s (0-2.5 s), or `Per-wall materials` |
| Temperature, humidity, vertical gradient | 20 °C (0-40), 50 % (10-90), 0 °C/m (−5 to 5) |

In RT60 mode, `pra.inverse_sabine` converts RT60 into one absorption coefficient and an ISM order.
The order has a cap (section 4), which removes late reflections. Thus, above approximately 1.5 s,
the live reverberation tail can be shorter than the nominal RT60. RT60 0 gives a free field: no
reflections, no air absorption, and c = 343 m/s.

In materials mode, each surface gets a pyroomacoustics material with octave-band absorption
(125 Hz-8 kHz). An unknown material name gives a uniform 0.3. The model has no scattering. The
response gives `rt60_actual`, estimated from a 20 dB decay of the impulse response from the drone to
microphone 1. The `Exhibition hall` button (shown in materials mode) loads the known baseline. It
has a hairy carpet floor, a fissured-tile ceiling, and plasterboard on the east, west and north
walls. The south wall has cotton curtains (`curtains_cotton_0.5`). The atmosphere is 22 °C, 55 % RH
and +1.5 °C/m. The button also sets the band to 200-2000 Hz flat, the crowd to point sources, the
crosstalk to flat and the ML preview to off.

Shoebox rooms use the pyroomacoustics air absorption. This is an octave-band table with two
temperature classes (below 15 °C, else 20 °C) and three humidity classes (below 50 %, 50-70 %,
70 % and above). Sound travels at the speed of sound for the set temperature and humidity
(344.0 m/s at 20 °C and 50 %). The estimators assume 343 m/s. A vertical temperature gradient bends
the sound path. The model moves the drone height by

$$\Delta z = -\frac{0.6\,\mathrm{d}T/\mathrm{d}z}{2c}\,L^2\sin\alpha$$

where L is the horizontal range and α is the true elevation. For a source above the array, a
positive gradient (warmer ceiling) decreases the apparent elevation. `Run details` shows the
resulting bias when it is 0.05° or more. At 1-2 °C/m over approximately 8 m, the bias is a fraction
of a degree. This is much less than the 10° step of the elevation grid.

### 3.2 Array geometries

| Geometry (GUI name) | Layout | `radius` means |
| --- | --- | --- |
| `UCA` (Circular ring) | N microphones on a horizontal ring | ring radius |
| `CROSS` (Standing cross) | N/2 along x and N/2 along z through the centre, with no centre point | half arm length |
| `ULA` (Linear) | N microphones evenly along x | half length |
| `CYLINDER` (Stacked rings) | two horizontal rings of N/2, `ring_separation` apart, top ring turned by half the angular spacing | ring radius |
| `HEIMDALL` (Heimdall, 44 microphones) | deployed layout in the vertical y-z plane, facing 0° azimuth, so the right of Heimdall is at negative azimuth; sent to the backend as `CUSTOM` | — |
| `CUSTOM` (Custom coordinates) | offsets [x, y, z] in metres from the centre; two or more, all different | — |
| `MICCANVAS` (API only) | 15 microphones: centre, 6 on a 10 mm radius, 8 on a 20 mm radius turned by 22.5° | — |

The defaults are 12 microphones (4-32), radius 0.15 m (0.03-1.0 m) and ring separation 0.12 m
(0.02-0.5 m). The batch sweep always uses these defaults. The default ring has 7.8 cm between
neighbours and aliases above approximately 2.2 kHz. The custom editor adds and removes microphones
and starts from any built-in geometry. It imports JSON (`[[x, y, z], ...]`), comma lists or one
`x y z` per line. The simulator moves microphones that are outside the room to 0.3 m from the
walls, and lists them in `mic_clip_notes`. The Heimdall positions are a copy of those in
`beam_table_2d.json`, kept in [app/js/shared/heimdall-array.js](../app/js/shared/heimdall-array.js).
Update the copy when the contract changes.

### 3.3 Drone source

The drone is one omnidirectional point at the set azimuth, elevation and distance from the array
centre (60°, 30° and 6 m; distance 2-20 m). It stays 0.3 m from the walls. The error is always
against the requested direction, also when the walls move the source. Thus, keep the source inside
the room.

The live simulator plays a random segment of `audio/drone.wav` (29.8 s, 16 kHz mono, repeated if too
short). Without this file, and always in the batch sweep, the drone plays a synthetic signal. It
has tones at 150, 300, 600 and 900 Hz with random phases, plus Gaussian noise at 0.3 relative
amplitude. The simulator mixes WAV files to mono, resamples them to 16 kHz by linear interpolation
and normalises them to unit RMS. It scales each source signal to its level in dB SPL at 1 m
(reference 20 µPa; drone 78 dB, GUI 60-95 dB). The synthetic signal is peak-normalised, so its RMS
level is approximately 9 dB below the set level. Thus the batch drone levels are lower than their
labels by this value. The direct path decreases as 1/r: 78 dB at 1 m arrives at approximately 62 dB
at 6 m. Each run simulates 100 ms more than the window and discards the start, to remove the onset
artefacts of the impulse response.

`Moving source` (live only) moves the drone during the window, from the set position:

| Setting | Default (GUI range) | Meaning |
| --- | --- | --- |
| `Trajectory` | `straight` | `straight`: constant horizontal velocity along the heading, at constant height; `arc`: horizontal circle around the array at the start radius |
| `Speed` | 5 m/s (0-30) | along the path; a negative value (API only) reverses the direction |
| `Heading` | 0° (0-355) | straight line only; 0° = +x, 90° = +y |
| `Trajectory chunks` K | 8 (4-16) | number of static positions, one at the middle time of each chunk |

The simulator divides the drone signal into K time-gated copies, joined by 10 ms sin²/cos²
crossfades that add up to exactly one. Each copy plays from its own static position in one room
simulation. Thus the arrival delay changes in steps, which approximates the Doppler shift and the
direction drift in the window. Few chunks at high speed cause staircase artefacts: increase K.
Speed 0 gives the static result.

### 3.4 Crowd and PA speakers

`Crowd and PA speakers` (on by default) adds interference:

| Setting | Default (GUI range) |
| --- | --- |
| Crowd talkers | 30 (0-100), 1.5 m high, 67 dB SPL at 1 m (55-80) |
| PA speakers | 8 (0-12), 3.0 m high, 80 dB SPL at 1 m (70-100) |
| Plane waves, in the plane-wave model | 64 (8-128) |

The seed of the run sets the positions. 60 % of the talkers are in groups around 4-8 random booth
centres (Gaussian spread 2 m). The other talkers have a uniform spread. No talker is within 2 m of
the array horizontally, and all are 0.3 m from the walls. The PA speakers are at random points along
random walls. Talker i plays the i-th segment of `audio/crowd.wav` (180 s), with the length of the
window. After the end of the file, the segments are circularly shifted copies. The PA speakers use
segments from the start of the same file, so they partly repeat the audio of the first talkers.
Without `crowd.wav`, each source plays independent Gaussian noise.

With `Crowd model` = `Plane-wave diffuse field`, N plane waves from random directions replace the
talkers. Each wave plays its own crowd segment. The simulator adds the waves to the microphone
signals after the room simulation, with frequency-domain fractional delays. Each wave has the
amplitude 1/√N, so the field arrives at the array at the crowd level (67 dB SPL) for all numbers of
talkers. Thus the crowd noise levels of the two models are different. When N increases, the
coherence between microphones approaches the isotropic sinc(kd) curve. The PA speakers stay point
sources.

### 3.5 Microphones and impairments

The microphones are omnidirectional points. The self-noise is independent white Gaussian noise on
each channel, at the `Microphone noise floor` (30 dB SPL, 20-50). The optional impairments (live
only, under `Hardware impairments`) apply after the room simulation, in this order:

| Impairment | Model | Parameters |
| --- | --- | --- |
| Gain and phase mismatch | for each channel, new random values for each run: gain, fractional delay (31-tap Blackman-windowed sinc) and DC offset | σ = 0.5 dB, 0.2 samples (12.5 µs), 10 µPa |
| Crosstalk, `Flat` | each channel receives its two neighbours by channel index (with wrap-around), times the coupling | −40 dB (−60 to −20) |
| Crosstalk, `Capacitive (FIR)` | as flat, through a first-order high-pass (6 dB/octave below the corner); a measured FIR in `crosstalk_fir_path` (API or batch flag) replaces the high-pass | corner 500 Hz (20-4000) |
| Codec quantisation | each channel rounded to N bits; full scale is the joint peak of all channels | 24, 16 (default), 12 or 8 bits |

`Apply the hardware-realistic set` switches on mismatch, crosstalk at −40 dB and 16-bit
quantisation. In the Heimdall and custom layouts, neighbours by channel index are not always
physical neighbours. A corner near 0 Hz makes the capacitive model equal to the flat model.

### 3.6 SRP-PHAT and the DOA band

The reference estimator is the pyroomacoustics SRP-PHAT. For each candidate direction, it adds the
phase-only (PHAT-weighted) cross-spectra of the microphone pairs, steered to that direction. The
strongest direction is the estimate.

| Setting | Value |
| --- | --- |
| STFT | 1024-point FFT (64 ms), hop 512, bin spacing 15.625 Hz |
| Search grid | 72 azimuths (5° steps) × 19 colatitudes (10° steps, 0-180°) = 1,368 directions; elevation = 90° − colatitude |
| Band | bins centred in [fmin, fmax]; default 200-2000 Hz = 117 bins; GUI 50-4000 Hz and 200-7000 Hz in 50 Hz steps |
| Harmonic comb | only bins within ±10 Hz of n·f0, n = 1-20, inside the band; f0 200 Hz (80-400); 14 bins at the defaults |
| Integration window | live 50-1000 ms (default 1000); batch 1 s |
| Outputs | estimate, 19 × 72 power grid, top 3 peaks at least 15° apart with their level relative to the first |

The 200 Hz lower edge removes frequencies where small arrays have almost no resolution. The 2000 Hz
upper edge stays below the aliasing frequency of the default ring. A typical band for small drones
is 300-2500 Hz, which removes HVAC rumble. The comb imitates a matched filter on a known blade-pass
frequency, with rectangular weights. If no bin remains (for example, f0 outside the band), the
estimator uses the flat band and gives no warning. `n_freq_bins` in the response and in
`Run details` shows the bins that it used. The live and batch paths select bins with the same
function, `build_freq_bin_mask`. If fmax ≤ fmin, the GUI sets fmax to fmin + 50 Hz.

### 3.7 Delay-and-sum modes

`Direction estimate` has two delay-and-sum modes that imitate a DSP beamformer:

| Mode | Beams | Operations per sample |
| --- | --- | --- |
| `steered_das`, scan and lock | listed angles, default 0-315° in 45° steps; latency = angles × integration window | microphones × taps |
| `beam_bank_das`, bank of K fixed beams | K = 8 (4-16), equally spaced over 360° | microphones × taps × K |

Both modes steer in azimuth only, at 0° elevation. The delays come from the x-y microphone
coordinates relative to the centroid (z is ignored), shifted so that the smallest delay is zero.
Each delay is a windowed-sinc fractional delay (16 taps, 8-32). The modes clamp and list the delays
above the FracDelay maximum (8 samples, 4-64). The strongest beam (RMS) is the estimate, with a
reported elevation of 0°, and its output is the beamformed audio. The delays and the clamping use
the 16 kHz simulation rate. The `DSP sample rate` (48 kHz) only converts the operations estimate,
shown as a fraction of the ADAU1467 budget of 6,144 operations per sample. These modes do not
reproduce the deployed 2-D steering table. The acoustic emulator does (section 5).

### 3.8 Audio outputs

Each run returns up to four 16-bit WAV clips, as long as the integration window. The clips are
`One microphone` (microphone 1), `Unsteered sum` (mean of all microphones), `Beamformed` and
`ML input` (section 3.9). In SRP-PHAT mode, the beamformed clip is an integer-sample delay-and-sum
toward the estimated direction, not the true direction. In the delay-and-sum modes, it is the
strongest beam.

With `Normalise downloaded audio` on (the default), the first three clips share one peak scale at
0.9 of full scale. Thus the beamforming gain stays audible. The ML clip has its own scale. With the
setting off, full scale is a fixed 1 Pa (approximately 94 dB SPL). Thus you can compare levels
across runs, for example in a 1/r check. The download names are `raw_<geometry>_<seed>.wav`,
`unsteered_…`, `beamformed_…` and `ml_preview_…`.

### 3.9 ML-path preview

`Render the ML input` (batch: `--ml-preview`) shows what an audio classifier on the MAX78002 would
receive:

1. The simulator quantises the beamformed audio to 8 or 16 bits (default 8), with its own peak as
   full scale. `ml_path_snr_db` is the ratio of the signal to the quantisation error.
2. It calculates log-mel features from the original and the quantised audio: Hann window, 512-point
   FFT, hop 256, triangular mel filters, 10·log10 power, 64 bands (32-96). In the live simulator,
   the mel range follows the DOA band. The batch sweep uses 200-2000 Hz.
3. It quantises the features of the quantised audio to 8 bits (16 bits does not change them).
   `feature_snr_db` compares them with the features of the original audio.

In the live simulator, the response adds the `ML input` clip and a spectrogram image. The batch
sweep fills two CSV columns. This is a proxy for a classifier front end: it shows the quantisation
loss, not the classification accuracy.

### 3.10 Beam pattern screen

The `Beam pattern` screen calculates the narrowband delay-and-sum response of each geometry above,
also Heimdall and custom coordinates:

$$g(\hat u) = \frac{1}{M}\left|\sum_{m=1}^{M} e^{\,jk\,\mathbf r_m\cdot(\hat u-\hat u_0)}\right|,\qquad k=\frac{2\pi f}{c},\quad c = 343\ \mathrm{m/s}$$

where $\hat u_0$ is the steering direction. The inputs are the steering azimuth and elevation, and
the frequency (50 Hz-20 kHz, default 2 kHz). The screen shows the pattern in 3D (5° grid, −30 to
0 dB). It also shows azimuth and elevation cuts through the steered beam, with their −3 dB widths
(2° steps). The other outputs are the aperture, the nearest spacing, the wavelength, the
spatial-alias limit $c/(2d_{\min})$ with a warning above it, and the beam solid angle.

### 3.11 What the Simulator screen shows

The result column shows:

- the estimate and its error against the requested direction,
- the SRP map (azimuth × elevation, truth as a circle, estimate as a cross), with the top three
  candidates,
- for the delay-and-sum modes, a polar plot of the beam powers and the DSP budget,
- the first 125 ms of the impulse response from the drone to microphone 1,
- the audio player and the downloads, and the ML spectrogram,
- `Run details`: seed used, levels, band and bin count, measured RT60, atmospheric bias, ML SNRs.

The 3D scene shows the room, scaled so that its longest side is 6 units. It shows the array, the
drone with its true direction, and the estimated direction (dashed). Crowd talkers are dots and PA
speakers are cubes. The scene also shows up to six image sources of the drone, and the trajectory.
It has an `Angled` and a `Plan` view. Before a run and after each change, the scene shows the new
setup, and marks the previous result as stale.

## 4. Batch and live differences

The two paths agree only in the clean case of the parity test, not with their defaults.

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
| Impairments | none, except capacitive crosstalk when `--crosstalk-model fir_capacitive` is set | mismatch, flat or capacitive crosstalk, quantisation, all optional |
| Estimators | SRP-PHAT | SRP-PHAT and two delay-and-sum modes |
| ML preview mel band | 200-2000 Hz | the DOA band |
| Seeds | 0 and 1 per condition (0 with `--test`) | new random seed per run (reported), or a fixed seed 0-999 |

To reproduce a batch row live, set the room to 20 × 15 × 10 m and the distance to 4 m. Use the same
RT60, angles and seed, with interference off. While `drone.wav` is present, the drone signal is
still different.

## 5. Hardware-free emulators

The Hardware screen can use two emulators instead of the board. They answer the firmware commands
through the same parser and WebSocket path as the serial hardware. They are for tests of the
console, the protocol and the tracker. They do not use the room simulator above. The steps to start
them are in the [README](../README.md#without-hardware).

The fast emulator ([heimdall_transport.py](../heimdall_transport.py); `sim_server.py --emulator`,
or `Emulator` in the connection panel) calculates detector levels from a simulated flight. The
levels come from a Gaussian beam response (σ = 18°) around the target direction, plus a little
random noise. The amplitude is proportional to 1/r, referenced to 30 m, with a cap at close range.
Each sector takes 2.8 ms. The grid can be 1×1 to 20×20 with any limits, with the cell-centre
convention of `beamforming_mathematics/hemisphere_scan_visualizer.py`. The connection panel uses
the deployed 7×7 grid over ±40° by default, and shows the selected DSP export. `--emulator` starts
with 7×7 over ±70° azimuth and ±60° elevation, and a generic per-microphone program. The tracker
receives only levels, never the trajectory.

| Flight profile at 1× | Motion | Speed | Range |
| --- | --- | --- | --- |
| Crossing pass | smooth lateral pass | approximately 14 m/s | 36-60 m |
| Approach and retreat | — | approximately 13 m/s | 16-83 m |
| Orbit | offset orbit | approximately 9 m/s | 27-52 m |
| Patrol | slower multi-axis route | approximately 8 m/s | 31-68 m |
| Evasive stress | wide angular motion, a 0.9 s dropout every 17 s | up to approximately 27 m/s | — |
| Legacy angular sweep | sinusoidal angles, for regression comparison | — | fixed 30 m |

`Flight speed` (0.25-3×) scales the time: at 2×, the speeds double and the dropout intervals halve.
Adaptive `Track` follows the firmware policy. It uses one complete search, two local cross
confirmations, and five-sector tracking with two-pass movement hysteresis. It does a local 3×3
reacquisition after three failed passes, and a global search every 50 tracking passes.

The acoustic emulator ([heimdall_acoustic.py](../heimdall_acoustic.py); `Acoustic room` tab,
`Prepare scene`, then `Start acoustic emulator`) plays back cached detector levels. They come from
`drone.wav` and `crowd.wav` at 48 kHz in a pyroomacoustics room. The room has order-2 reflections,
plus a deterministic diffuse tail calibrated to RT60 1.2 s (measured 1.18-1.20 s). The model uses
the 44 microphones in DSP order, the deployed beam-table delays and a 1-4 kHz detector band-pass.
This band-pass has two 129-tap linear-phase FIR stages. It is the intended detector band, not
necessarily the filter in the deployed DSP program. All sectors come from one acoustic scene. The
levels are drone 78, crowd 67 and speech 72 dB SPL, with 30 dB SPL microphone noise.

| Scenario | Contents |
| --- | --- |
| Conference room, evasive drone 2× | 20 × 40 × 10 m, vertical array at [10, 0.5, 1] m, 30 s room-bounded evasive loop, diffuse crowd from 12 directions, speech at +25°, 12 m, 1.5 m high |
| Handheld drone at 2 m, drone only | slow 12 s figure-eight at 2 m broadside depth, ±0.75 m sideways, 1.2-2.0 m high |
| Handheld drone at 2 m, crowd and speech | the same motion with the conference crowd and speech |
| Stationary speaker at 2 m, broadside | drone recording only, time-averaged spectrum, for comparison with a physical speaker |

The preparation took 9-94 s on the development machine. The caches (approximately 1.18 MB) are
stored by content under `.cache/heimdall_acoustic/` and load again in tens of milliseconds. If only
the grid changes, the emulator uses the room result again (0.4-1.0 s). Grids other than the default
7×7 over ±40° use quantised delays that exist only in the simulation. In the handheld scenarios,
85.8 % (drone only) and 79.8 % (crowd and speech) of the 49-sector sweeps were within one grid cell
of the truth. The coherent all-sector references gave 98.2 % and 91.4 %. Adaptive `Track` had
median errors of 7.0° and 8.6°. `Listen` makes jointly normalised 3 s clips: filtered mix, one
microphone, unsteered sum, beam steered to the truth, and selected beam. These clips are not
sample-exact ADAU1467 output. Both emulators show the true direction under `Diagnostics`. The true
direction never goes to the tracker.

Use the acoustic emulator for relative sector ranking, reflection and interference stress, and
tracking behaviour. Its SPL-to-digital scaling is nominal, so it does not predict the detection
range or probability. A moving source interpolates cached waypoint power responses, without
continuous Doppler phase.

## 6. Limitations and how far to trust results

The simulator is reliable for relative comparisons and trends. It does not predict the absolute
accuracy or the range at a venue. Calibrate it against recordings as early as possible.

### What the results support

- The ranking of geometries under the same conditions. Differences of 10° or more in mean total
  angular error should also occur on the hardware.
- Trends. More reverberation, a quieter drone or more interference increase the error. Geometries
  with vertical aperture (stacked rings, standing cross) resolve elevation. A flat ring and a line
  do not.
- Mirror ambiguities of planar arrays. These come from physics, not from the software. A horizontal
  ring cannot tell +θ from −θ elevation, and SRP-PHAT selects one. A line has a cone of confusion
  around its axis. A vertical planar array such as Heimdall cannot tell front from back.
- Spatial aliasing and sidelobe structure against spacing and frequency. The effect of the number
  of microphones, the radius and the ring separation on the beam width. The relative gains from a
  longer integration window, and from beamforming compared with the unsteered sum.

### What they do not support

- Absolute DOA error, SNR or detection range. The levels are in dB SPL, but the drone, the crowd and
  the microphone chain are not calibrated against measurements.
- Behaviour at a specific venue or outdoors. An empty shoebox is not a hall: a hall needs measured
  impulse responses. There is no ground model, wind noise, turbulence or long-range propagation.
- Latency, or DSP load beyond the coarse operations estimate. The simulation runs offline.
- Several sources at the same time. The estimator assumes one source. The top-3 list shows
  ambiguity, not tracking.
- Statistical confidence from one run. Each run is one realisation of noise and positions. Repeat
  with several seeds. The batch sweep uses two.

### Simplifications

| Area | Model | Consequence and check |
| --- | --- | --- |
| Microphones | omnidirectional points | MEMS packages are almost omnidirectional below approximately 2 kHz. Above that, the package, the board and the housing add directivity. Check that the datasheet directivity stays within ±2 dB over the band in use. |
| Distance | exact path lengths, 1/r; the estimators assume plane waves | Ignore results with the source closer than 2 m. |
| Room | empty shoebox, octave-band absorption, no scattering, ISM order ≤ 6 live and ≤ 10 batch | No booths, trusses, people or PA rigs. The late reverberation is cut. Test in a booth-like room before the venue. |
| Atmosphere | uniform temperature and humidity; tabulated air absorption; first-order vertical gradient | No HVAC pockets, horizontal gradients, wind or turbulence. The absorption changes only across 15 °C, 50 % and 70 % RH. Add ±1-2° of systematic elevation error to the error budget. |
| Drone | one omnidirectional point that plays a recording or four tones | Real drones radiate from several rotors, tonal and broadband, not omnidirectionally, and change with thrust. Record your drone at a fixed RPM and distance, and use it as `drone.wav`. |
| Motion | static chunks, live only | The delay changes in steps. There is no rotor radiation pattern. On moving drones, integrate for less than 200 ms. Expect more elevation jitter on the hardware. Check against a real fly-past. |
| Crowd | talkers from one 180 s file, which the PA speakers also use, or N plane waves | Point sources are somewhat over-correlated. Plane waves leave a finite-N residual and are stationary over the window. Use 64-128 plane waves. Treat crowd results as a lower bound of the real error. |
| Noise and mismatch | white Gaussian self-noise; static gain, delay and DC offset | No PDM clock noise, 1/f drift, EMI or time-varying jitter. Increase the noise floor to agree with the measured pickup. Measure the real chain with white noise, and set the `apply_mic_mismatch_v2` parameters from it. |
| Crosstalk and arithmetic | neighbours by channel index, flat or first-order; double precision, except codec and ML quantisation | No multi-pole, non-neighbour, supply or EMI coupling. No fixed-point DSP stages, dither, codec pre-filter or companding. Load measured crosstalk FIRs of the PCB when they exist. |
| DOA band | rectangular ±10 Hz comb on a fixed f0 | No shaped lobes, f0 tracking, sub-band voting or MVDR. Set the band from recorded drone spectra. Track f0 if the RPM changes. |
| Delay-and-sum modes | azimuth only, delays at 16 kHz, windowed-sinc approximation of FracDelay | Not the deployed 2-D table. No fixed-point MAC stages, SafeLoad write timing, MUX switching artefacts or exact SigmaStudio FracDelay response. |
| ML preview | log-mel proxy | No real MAX78002 network, ADC INL, clock jitter or accumulator widths. Allow a few dB of extra loss beyond the preview SNR. |
| Grid and truth | 5° × 10° grid; error against the requested direction | Errors less than half a grid step are not visible. A source that the walls move gives a wrong error. |

### Calibration offsets

These are general rules from the literature, not measurements on Heimdall. Apply them to all
numbers from the simulator.

| Quantity | Simulation | Expect in a real hall |
| --- | --- | --- |
| DOA error | for example, 3° mean azimuth error | 2-3 times larger (6-10°) |
| Beamforming SNR gain in diffuse noise | 10-15 dB | 3-8 dB, because of self-noise, mismatch and crosstalk |
| Range at which the DOA error stays below 5° | R | 40-60 % of R, because there is no booth scattering and the crowd is milder |
| Elevation error of a flat ring or line | limited by the mirror ambiguity | 5-10° more from backplane reflections and near-field effects. Stacked rings are the most robust. |

### Validation against hardware

1. Record the drone at 1, 3, 5 and 10 m in a quiet room, with a reference SPL meter.
2. Use the recordings as `audio/drone.wav` in the live simulator. The batch sweep always uses its
   synthetic signal. Check that the geometry rankings and trends stay the same.
3. Measure the DOA error in a reverberant room (atrium, warehouse, empty canteen). Compare it with
   the simulator at the RT60 of that room. Expect an error 2-3 times larger than in the simulator.
4. Rehearse in a large crowded space, for example on the set-up day of a trade show. Record the
   sound. Use the recordings in the simulator, and decide the remaining changes.
5. Plan 2-3 working days at the venue for integration: PA pickup, EMI from nearby equipment and
   mounting acoustics. The simulator does not replace this.
