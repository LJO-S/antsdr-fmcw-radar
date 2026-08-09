# AntSDR E200 — Phase 1: build the firmware from source and explore what ADI put on it

Written to be followed at the keyboard, with checkpoints after every stage. Assumes you have an AntSDR E200, a Linux PC, the USB-C cable, and patience. Where a command needs a value from your own system (an IP, a device path, a channel name), it's flagged.

Repo: https://github.com/MicroPhase/antsdr-fw-patch — a shell wrapper that patches ADI's `plutosdr-fw` (pinned submodule), which in turn builds `hdl` (bitstream) + `linux` (kernel) + `u-boot` + `buildroot` (rootfs).

---

## The three rules (re-read these whenever something breaks)

1. Explore before you build (Stage A). The board already has working firmware.
2. Boot from SD, never DFU on the E200. SD boot leaves the factory QSPI flash intact, so a bad build is reversible by flipping the jumper. Unbrickable.
3. It is `sudo -E make`, never `sudo make`. The `-E` keeps your environment. Forget it and nothing is found.

---

## What you need on the host

- OS: Ubuntu 22.04 LTS strongly recommended (20.04 also works). Avoid 24.04 for this — its missing `libtinfo5` and `python` (python2) packages will fight you. If you only have 24.04, do it in a 22.04 VM or container.
- Free disk: ~120 GB (Vivado ~50 GB installed, plus buildroot downloads and builds another 30-50 GB). Running out mid-build produces cryptic failures.
- RAM: 8 GB minimum, 16 GB comfortable.
- Time: first build is 1-2 hours.
- Vivado 2023.2, exactly. The repo pins its submodule to this version; any other Vivado throws confusing synthesis errors deep in the HDL. The free/Standard edition is fine. During install, include Zynq-7000 (SoC) device support. The README links the exact installer.
- Linaro GCC 7.3-2018.05 `arm-linux-gnueabihf` toolchain. This is an *external* toolchain the repo uses on purpose (the Vivado/Vitis compiler is incompatible with buildroot here). From the Linaro 7.3-2018.05 release page, take the `x86_64_arm-linux-gnueabihf` build (not the `i686` one) so you don't have to deal with 32-bit multilib libraries on a 64-bit host.

---

## Stage A — Explore the stock device with libiio (~30 min, do this first)

The point of this stage: prove the hardware and your host link work, and see the AD9361 control surface that ADI exposes — before you risk anything.

### A1. Install the host tools
```
sudo apt update
sudo apt install libiio-utils
```
This gives you `iio_info`, `iio_attr`, `iio_readdev`, `iio_reg`. (Distro packages are a little old but fine for exploring. Newer builds are on the Analog Devices libiio GitHub releases if you ever need them.)

### A2. Connect over USB-C
Plug the AntSDR into your PC with USB-C. The firmware brings up two things:
- a USB network gadget — by Pluto convention the device is at `192.168.2.1`
- a USB serial console — shows up as `/dev/ttyUSB0` (or ttyUSB1)

Get a shell on the serial console to sanity-check (handy when nothing else responds):
```
sudo screen /dev/ttyUSB0 115200
```
Log in (Pluto default is user `root`, password `analog`; the AntSDR may differ — check MicroPhase docs if it rejects you). Then:
```
ip addr
```
Note the IP addresses the device is actually using. (To leave `screen`: Ctrl-A then k, then y.)

### A3. Dump everything — this is "what ADI put on this thing"
```
iio_info -u ip:192.168.2.1
```
You should see three IIO devices. Here's what each is:
- `ad9361-phy` — the transceiver's brain. Control attributes live here: `rx_lo`/`tx_lo` frequency, `sampling_frequency`, `rf_bandwidth`, `gain_control_mode`, `hardwaregain`, and the calibration knobs.
- `cf-ad9361-lpc` — the RX capture device (the DMA stream of ADC samples).
- `cf-ad9361-dds-core-lpc` — the TX device (DDS tone generator plus the DMA path for playback).

Read the channel names in this output carefully — you'll use the exact names below.

### A4. Poke a few attributes
Read a value:
```
iio_attr -u ip:192.168.2.1 -c ad9361-phy voltage0 hardwaregain
```
Set the RX local oscillator to 100 MHz (frequency is in Hz; confirm the channel name from A3 — it's usually `altvoltage0` for RX LO):
```
iio_attr -u ip:192.168.2.1 -c ad9361-phy altvoltage0 frequency 100000000
```
Set the sample rate to 4 MSPS:
```
iio_attr -u ip:192.168.2.1 -c ad9361-phy voltage0 sampling_frequency 4000000
```

### A5. Capture raw IQ and record your baseline
```
iio_readdev -u ip:192.168.2.1 -b 1000000 cf-ad9361-lpc > baseline_capture.iq
```
The file is interleaved signed 16-bit samples: I, Q, I, Q, ... (the AD9361's 12-bit data sits left/right-justified inside 16-bit containers, so expect a scaling factor when you load it). A quick look in Python:
```
import numpy as np
raw = np.fromfile("baseline_capture.iq", dtype=np.int16)
iq = raw[0::2] + 1j*raw[1::2]
print(len(iq), iq[:5])
```
Save this capture and the noise floor you observe. This is your **baseline** — you'll diff your own build against it in Stage E.

For a GUI, the IIO Oscilloscope (`iio-oscilloscope`) or GNU Radio with `gr-iio` both give you a live FFT/waterfall fed from the same device. Optional but satisfying.

**Checkpoint A:** you can list the IIO devices, set the LO and sample rate, and capture IQ to a file. Hardware and host comms are proven. Only now is it worth building.

---

## Stage B — Set up the build host

### B1. Build dependencies
```
sudo apt-get install git build-essential fakeroot libncurses5-dev libssl-dev ccache
sudo apt-get install dfu-util u-boot-tools device-tree-compiler mtools
sudo apt-get install bc python cpio zip unzip rsync file wget
sudo apt-get install libtinfo5 bison flex
sudo apt-get install libmpc-dev
```
Notes for the clumsy:
- On 22.04 these all resolve. On 24.04, `libtinfo5` and `python` are the ones that fail — another reason to use 22.04.
- The README also tells you to `apt-get purge gcc-arm-linux-gnueabihf` (remove the distro ARM gcc so it can't shadow the Linaro toolchain). Do that.

### B2. Install Vivado 2023.2
Run the installer, pick Vivado (Standard is fine), include Zynq-7000 device support. After install, confirm it sources cleanly:
```
source /opt/Xilinx/Vivado/2023.2/settings64.sh
which vivado
```
`which vivado` must print a path. If it doesn't, fix this before continuing — the build calls Vivado.

### B3. Unpack the Linaro toolchain
Extract the `x86_64_arm-linux-gnueabihf` tarball somewhere stable, e.g. `~/toolchains/`. Note the exact extracted directory name — you'll point PATH at its `bin/`.

---

## Stage C — Clone, patch, build

### C1. Clone with submodules
```
git clone -b v0.39 --recursive https://github.com/MicroPhase/antsdr-fw-patch.git
cd antsdr-fw-patch
```
If you forget `--recursive`, the `plutosdr-fw` submodule (and its sub-submodules) will be empty and the patch will fail. Fix with:
```
git submodule update --init --recursive
```

### C2. Make a sourceable environment file (so you don't fat-finger the exports every time)
Create `env.sh` in the repo root:
```
export CROSS_COMPILE=arm-linux-gnueabihf-
export PATH=$PATH:$HOME/toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf/bin
export VIVADO_SETTINGS=/opt/Xilinx/Vivado/2023.2/settings64.sh
export TARGET=e200
```
Adjust the toolchain path to match B3 exactly. Then every new terminal:
```
source env.sh
arm-linux-gnueabihf-gcc --version   # must print 7.3.1
```
If that gcc version line doesn't print, your PATH is wrong — stop and fix it.

### C3. Apply the E200 patch
```
sh patch.sh e200
```
**Checkpoint C3:** the last line is `patch finish`.
If it errors or you accidentally run it twice (double-applying conflicts), reset the submodule to clean and retry:
```
sh resetGit.sh
sh patch.sh e200
```

### C4. Build
```
cd plutosdr-fw
sudo -E make
```
Again: `-E`, not bare `sudo`. This is the 1-2 hour step. It synthesizes the bitstream in Vivado, builds the kernel and u-boot, and assembles the rootfs with buildroot.

**Checkpoint C4:** `build/` contains, among others:
- `antsdre200.frm` (the packaged firmware image)
- `boot.bin`, `u-boot.elf`, `zImage`
- `zynq-antsdre200.dtb` (your device tree)
- `system_top.bit` (your bitstream)
- `rootfs.cpio.gz`

These last two — the `.bit` and the `.dtb` — are the ones you'll be regenerating constantly once you start modifying the fabric in Phase 2.

---

## Stage D — Make an SD image and boot it (the reversible way)

### D1. Build the SD image
```
make sdimg
```
Output lands in `build_sdimg/`.

### D2. Write it to an SD card
Format a microSD as FAT32, then copy the entire contents of `build_sdimg/` onto it.

### D3. Set the board to SD boot
Set the boot-mode jumper to SD (not QSPI). Check the E200 hardware manual on the MicroPhase docs site for the exact jumper position — it's a 2-position selection. This is the step that keeps you safe: SD boot does not touch the factory firmware in QSPI.

### D4. Boot and watch
Insert the SD card, power the board, and watch the boot log on the serial console (`sudo screen /dev/ttyUSB0 115200`). You want to see u-boot load your `zImage` and the kernel come up.

**Checkpoint D:** the board boots your image and `iio_info -u ip:192.168.2.1` lists the same three devices as before.

**Recovery (if it doesn't boot):** power off, set the jumper back to QSPI, power on. You're back on the untouched factory firmware. Nothing is bricked. Then go read the serial boot log to see where your SD image failed.

---

## Stage E — Verify against your baseline

Re-run your Stage A exploration on the freshly booted build:
- list devices (A3)
- set the same LO and sample rate (A4)
- capture IQ the same way (A5)

Compare the new capture and noise floor against the `baseline_capture.iq` from Stage A. If behaviour matches, your from-source build is good and **Phase 1 is complete** — you now own the toolchain end to end and can start modifying the fabric (Phase 2).

---

## Troubleshooting — the clumsy-mistake catalogue

- `vivado: command not found` or toolchain errors during make → you ran `sudo make` instead of `sudo -E make`, or didn't `source env.sh` in this terminal.
- Synthesis errors deep inside the HDL → wrong Vivado version. It must be 2023.2.
- `libtinfo5` / `python` won't install → you're on Ubuntu 24.04. Use 22.04.
- The cross-compiler won't run (`No such file or directory` on a file that exists) → you grabbed the 32-bit `i686` toolchain on a 64-bit host. Use the `x86_64` build (or install i386 multilib libs).
- `patch.sh` fails immediately → submodules are empty; run `git submodule update --init --recursive`.
- `patch.sh` fails with conflicts → it was already applied; run `sh resetGit.sh`, then patch again.
- Build dies cryptically around buildroot → almost always out of disk. Confirm ~120 GB free.
- Tempted to use DFU on the E200 → don't, it's unsupported on this model. SD boot only.
- IQ file looks like noise/garbage in Python → remember it's interleaved int16 (I,Q,I,Q) with 12-bit data scaled inside 16-bit words; you may also be tuned somewhere empty — set a known LO and feed a known tone.

---

## Why this order matters

Stage A first means that if Stage C/D go wrong, you already know the hardware and host link are fine, so you can focus on the build. SD boot means a broken build costs you a jumper flip, not a board. And the baseline capture turns "did my build work?" from a guess into a diff. Once you trust this loop, the Phase 2 work — dropping a custom block into the `axi_ad9361 → axi_dmac` datapath and regenerating `system_top.bit` + the `.dtb` — is just repeating Stages C through E with your own HDL in the mix.
