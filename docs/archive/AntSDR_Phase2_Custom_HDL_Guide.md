# AntSDR E200 — Phase 2: put your own VHDL into the firmware

Follow-up to the Phase 1 guide. Assumes Phase 1 is complete: you built `v0.39` from source with the E200 patch, booted it from SD, and verified against your baseline IQ capture. Everything in this guide was checked against the actual repo tree at `~/work/projects/sdr/antsdr-fw-patch` — file paths and line references are real, not generic.

Written for someone fluent in VHDL and Python, new to make and to script-driven Vivado flows.

---

## The three rules of Phase 2

1. **The Tcl scripts are the design. The Vivado project is a disposable build product.** Anything you change in the Vivado GUI is destroyed on the next build. You *look* in the GUI; you *change* in `system_bd.tcl`.
2. **Your custom files live inside the `plutosdr-fw` submodule, and `resetGit.sh` deletes them.** Commit your work to a local git branch inside the submodule before you ever run that script again (Section 7.1).
3. **Same as Phase 1: SD boot, QSPI untouched.** A broken bitstream costs you a jumper flip. This is what makes casual HDL experimentation safe.

---

## 0. Orientation — why this is not an ordinary Vivado project

You suspected the build flow "is not some ordinary Vivado project". Correct. There is **no hand-maintained `.xpr` anywhere in the repo**. What's checked in for the E200 is just four small files in `plutosdr-fw/hdl/projects/e200/`:

| File | Role |
|---|---|
| `system_project.tcl` | Creates a Vivado project from nothing, adds top-level files, runs synthesis+implementation |
| `system_bd.tcl` | ~400 lines of Tcl that **builds the entire block design programmatically** — every IP instance, every wire, every address assignment |
| `system_top.v` | Thin Verilog top-level: instantiates the generated block design wrapper, plus I/O buffers |
| `system_constr.xdc` | Pin locations and timing constraints |

When the build runs, Vivado is launched **in batch mode** (no GUI): `vivado -mode batch -source system_project.tcl`. That script calls ADI's helper procs (from `hdl/projects/scripts/adi_project_xilinx.tcl`), which create the project, execute `create_bd_design "system"`, then `source system_bd.tcl` — and the whole block design materializes from script. Then synthesis and implementation run, and the result is exported as `e200.sdk/system_top.xsa`.

Why ADI does it this way: they maintain ~100 boards from one IP library (you saw the enormous `hdl/projects/` listing). Binary `.xpr` projects and `.bd` files diff terribly in git and rot across Vivado versions; Tcl generation is reproducible and reviewable. The cost: a GUI-centric workflow doesn't apply, and you have to learn where changes actually go. That's this guide.

### 0.1 A make primer (you've never used it — here's the whole idea)

`make` is a 1970s tool that does one thing: **rebuild files from other files, but only when needed.** A `Makefile` is a list of rules shaped like:

```makefile
target: prerequisite1 prerequisite2
	recipe-command-that-produces-target
```

Read it as: "*to produce `target`, you need `prerequisite1` and `prerequisite2`; if `target` is missing or **older than any prerequisite** (file timestamps), run the recipe.*" That's the entire mental model. Three refinements you'll meet in this repo:

- **Rules chain.** If a prerequisite is itself a target of another rule, make recurses: `firmware ← kernel ← compiler`. You ask for the top target; make walks the graph and rebuilds only the stale parts. This is why your interrupted Phase 1 build resumed in minutes instead of restarting.
- **`.PHONY` targets** are names that aren't real files ("clean", "all") — their recipes always run.
- **`make -C somedir`** means "run make in that directory" — this is how the top-level Makefile delegates to the kernel's own Makefile, buildroot's, and the HDL project's. The build is a tree of Makefiles calling Makefiles.
- **Variables**: `$(TARGET)` expands like a shell variable. Your `env.sh` sets `TARGET=e200` in the environment, and the Makefile picks it up — that's how one Makefile builds five different boards.

And the eternal reminder from Phase 1: `sudo -E make`. The `-E` carries your environment (`TARGET`, `PATH` with the Linaro toolchain, `VIVADO_SETTINGS`) through sudo. Without it, the Makefile sees none of your variables. Side effect you already ran into: everything the build writes is **root-owned**, which matters for the GUI (Section 1.1) and for cleaning (Section 7.2).

### 0.2 The dependency chain, concretely

What actually happens when you run `sudo -E make` in `plutosdr-fw/` (all from the real `Makefile`):

```
all
 ├── clean-build              # wipes build/* EVERY run (cheap; just staging files)
 ├── build/e200.frm           # the final firmware image
 │    └── build/e200.itb      # FIT image = zImage + dtb + rootfs + bitstream
 │         ├── build/zImage           ← make -C linux        (kernel)
 │         ├── build/zynq-e200.dtb    ← make -C linux        (device tree)
 │         ├── build/rootfs.cpio.gz   ← make -C buildroot    (root filesystem)
 │         └── build/system_top.bit   ← extracted from system_top.xsa
 │              └── build/system_top.xsa
 │                   └── make -C hdl/projects/e200   ← THE VIVADO BUILD
 └── build/boot.frm, zips, legal-info ...
```

Two mechanics worth knowing precisely, because they explain every "why didn't it rebuild?" you will ever hit:

1. **`clean-build` wipes `build/` on every run**, so the top-level make always *re-asks* every sub-build for its output. The sub-builds (kernel, buildroot, HDL) do their own change detection internally and return in seconds if nothing changed. This is why a no-change `sudo -E make` takes a few minutes, not two hours.
2. **The HDL sub-make has its own dependency list** in `hdl/projects/e200/Makefile`: the `M_DEPS` variable (`system_project.tcl`, `system_bd.tcl`, `system_top*.v`, `system_constr*.xdc`, the ADI library IPs...). Vivado reruns **only if one of those files is newer than the existing `e200.sdk/system_top.xsa`**. Consequence: if you add your own `rx_tap.vhd` and edit *only that file*, make does **not** see it — your `.vhd` isn't in `M_DEPS`. Section 4.5 handles this.

And one brutal detail from `hdl/projects/scripts/project-xilinx.mk` (line 127): when the HDL rebuild *does* trigger, the recipe starts with `rm -rf` of the entire generated project — `e200.xpr`, `e200.runs`, `e200.srcs`, `e200.sdk`, all of it — then reruns `system_project.tcl` from scratch. **Every HDL change costs a full re-synthesis** (roughly the HDL share of your Phase 1 build time, ~40–60 min on a typical machine). This is also the mechanical proof of Rule 1: the project you open in the GUI literally gets deleted and regenerated.

> Mitigation for iteration speed: the HDL makefile supports incremental compile. `cd plutosdr-fw/hdl/projects/e200 && source ../../../../env.sh && bash -c "source $VIVADO_SETTINGS && make MODE=incr"` reuses the previous routed checkpoint as a starting point. Useful once your changes are small deltas.

---

## 1. See the design — block diagram and schematics in the Vivado GUI

Here's the good news you were hoping for: **your Phase 1 build left behind a complete, openable Vivado project** at `plutosdr-fw/hdl/projects/e200/e200.xpr`, including the block design and the fully synthesized+implemented design. You can inspect everything visually today, without rebuilding anything.

### 1.1 Fix ownership first

The project was written by root. Vivado running as you needs to write locks, journals and caches into the project directory, so:

```bash
cd ~/work/projects/sdr/antsdr-fw-patch
sudo chown -R $USER:$USER plutosdr-fw/hdl
```

(Future `sudo -E make` runs will re-create root-owned files; just re-run the chown before GUI sessions. Harmless.)

Also: **never open the project while a `make` is running** — the batch build and the GUI will fight over the same directory.

### 1.2 Open it

```bash
cd ~/work/projects/sdr/antsdr-fw-patch
source env.sh
source $VIVADO_SETTINGS
cd plutosdr-fw/hdl/projects/e200
vivado e200.xpr &
```

### 1.3 The block design — this is the ".bd" you asked about

In the **Flow Navigator** (left panel): **IP INTEGRATOR → Open Block Design**. The design is named `system` — this is the diagram that `system_bd.tcl` generates. First actions:

- Click the **Regenerate Layout** button (circular-arrows icon in the diagram toolbar) — the auto-placed layout becomes far more readable.
- Zoom around. Find `axi_ad9361` (the transceiver core), the two `axi_dmac` blocks, `rx_fir_decimator` / `tx_fir_interpolator` (hierarchy blocks — double-click to descend into them), `cpack` / `tx_upack`, `sys_ps7` (the ARM processing system), and `axi_vcxo_ctrl` (MicroPhase's own IP).
- Open the **Address Editor** tab (next to the Diagram tab): the complete memory map of every fabric peripheral as seen by the ARM. Compare it with the table in Section 2 — it will match, because both come from the same `ad_cpu_interconnect` lines in the Tcl.
- Right-click any ADI IP block → **View Product Guide** works for Xilinx IP; for ADI IP, the documentation lives at wiki.analog.com (search e.g. "axi_ad9361 wiki").

**Export the diagram as a PDF** (your printable schematic): with the block design open, type in the **Tcl Console** at the bottom:

```tcl
write_bd_layout -force -format pdf -orientation landscape ~/e200_system_bd.pdf
```

(`-format svg` also works if you want to zoom losslessly.)

### 1.4 The real schematics

The block design is the architectural view. For actual gate/netlist views:

- **Flow Navigator → SYNTHESIS → Open Synthesized Design → Schematic**: the post-synthesis netlist. The full design is enormous — don't open the whole thing flat; instead use the **Netlist** panel to select one module of interest, right-click → Schematic.
- **Flow Navigator → IMPLEMENTATION → Open Implemented Design**: same, plus **Report Utilization** (how much of the Zynq-7020's fabric is used — important headroom check before you add your own logic) and **Report Timing Summary** (all user clocks and their slack; your custom block will show up here later).
- `system_top.v` (Sources panel, top of the hierarchy): see how thin it is — it instantiates the generated `system_wrapper` and a handful of `ad_iobuf`s. The design *is* the block design.

### 1.5 The one warning, again

You can experiment freely in this GUI — add blocks, rewire, even re-run synthesis. It's a great sandbox and the recommended way to *prototype* (Section 4.7). But the moment the make flow rebuilds HDL, this entire project directory is `rm -rf`ed and regenerated from `system_bd.tcl`. **A change is only real when it's in the Tcl.**

---

## 2. Guided tour — what's actually in the E200 fabric

Everything below is read directly out of `hdl/projects/e200/system_bd.tcl`. The E200 is configured **2R2T** (both AD9361 channels; `MODE_1R1T 0`, line 217) on a Zynq **xc7z020clg400-2**.

### 2.1 The RX datapath (antenna → Python)

```
                        AD9361 chip (outside the FPGA)
                              │ CMOS interface: rx_clk, rx_frame, rx_data[11:0]
                              ▼
                        ┌───────────┐
                        │ axi_ad9361 │  deframes the interface, produces 4 parallel
                        └───────────┘  sample streams @ l_clk: i0,q0 (RX1) i1,q1 (RX2)
                   RX1 i0,q0 │              │ RX2 i1,q1
                             ▼              │
                   ┌──────────────────┐     │
                   │ rx_fir_decimator │     │   optional /8 FIR (runtime bypassable)
                   └──────────────────┘     │
                             ▼              ▼
                        ┌─────────────────────┐
                        │     util_cpack2     │  packs enabled channels into one
                        │       "cpack"       │  64-bit word stream
                        └─────────────────────┘
                              ▼
                     ┌────────────────────┐
                     │ axi_ad9361_adc_dma │  AXI DMA controller (ADI axi_dmac)
                     └────────────────────┘
                              ▼  AXI HP1 port (S_AXI_HP1)
                        ┌───────────┐
                        │ DDR (PS)  │ → kernel driver → libiio → iio_readdev → you
                        └───────────┘
```

The TX path is the mirror image: `axi_ad9361_dac_dma` reads DDR via **HP2** → `tx_upack` unpacks → `tx_fir_interpolator` (×8, bypassable) → `axi_ad9361` → chip.

**The signal convention in the sample path** (this is what your block must speak, Section 3): it is *not* AXI-Stream. It's ADI's simpler parallel convention — per channel a 16-bit `data` bus (12-bit samples sign-extended) plus an `enable` (quasi-static: "this channel is selected"), and one shared `valid` strobe that pulses when a new sample set is present. Data is held stable between valid pulses. No backpressure/ready — upstream never waits. This makes inserting a block trivial compared to full AXI-Stream.

**Clocks:** the whole sample path runs on `axi_ad9361/l_clk` — the interface clock recovered from the AD9361, sample-rate dependent, up to 61.44 MHz. The AXI/control side runs on `sys_cpu_clk` (FCLK0, 100 MHz). A 200 MHz FCLK1 feeds IDELAY calibration and the ethernet PHY logic. **Your inline block lives in the `l_clk` domain** — at ≤61.44 MHz on a -2 Zynq, timing is forgiving.

### 2.2 The memory map and interrupts (from `system_bd.tcl` lines 372–376, 403–404)

| Fabric peripheral | Address | IRQ | Linux driver / DT node |
|---|---|---|---|
| `axi_ad9361` | `0x7902_0000` | — | `cf-ad9361-lpc@79020000` in `zynq-e200.dtsi` |
| `axi_ad9361_adc_dma` | `0x7C40_0000` | ps-13 | `dma@7c400000` |
| `axi_ad9361_dac_dma` | `0x7C42_0000` | ps-12 | `dma@7c420000` |
| `axi_tdd_0` | `0x7C44_0000` | — | TDD engine |
| `axi_vcxo_ctrl` | `0x43C0_0000` | — | MicroPhase clock-discipline IP |

This table is the device-tree connection made concrete: `linux/arch/arm/boot/dts/zynq-e200.dtsi` contains nodes whose `reg = <...>` addresses are exactly these values. The BD assigns the address; the DT tells the kernel about it. Two views of one contract. (Note the AD9361 *phy* itself — `ad9361-phy` in your Stage A dumps — is controlled over **SPI** from the PS, not through this memory map. The `axi_ad9361` core at 0x79020000 is the *data interface*, which is why the DT calls it `cf-ad9361-lpc`.)

### 2.3 What MicroPhase added vs. a stock Pluto

Comparing `e200/system_bd.tcl` with `pluto/system_bd.tcl`: the E200 adds **gigabit ethernet** (`gmii_to_rgmii` + external PHY — that's why your E200 has an RJ45), **`axi_vcxo_ctrl`** (disciplines the 40 MHz VCXO against an external 10 MHz reference / GPS PPS — the `CLKIN_10MHz`, `PPS_*` ports), an **`axi_tdd_0`** engine (hardware TX/RX time-division switching with external sync), and runs **2R2T** where Pluto ships 1R1T. Useful context: when you read Pluto documentation, everything about the AD9361/DMA datapath applies; these extras are E200-specific.

---

## 3. Your first custom block — design decisions before code

### 3.1 What we'll build

A **4-channel RX tap**: a VHDL block inserted between the decimator/AD9361 outputs and `cpack`, so **every RX sample on its way to Linux flows through your logic**. It has two modes via a generic:

- `TEST_RAMP = 0`: registered pass-through (one `l_clk` cycle of latency, functionally invisible),
- `TEST_RAMP = 1`: replaces the RX1-I channel with an incrementing counter — an *unambiguous* proof your block is in the path (a captured ramp cannot be produced by anything else; comparing noise floors can deceive you).

Why all 4 channels instead of just RX1? Because the design strobes `cpack` with a single shared `valid`. If you register RX1's data and the valid but let RX2 bypass, RX1 and RX2 skew by one sample whenever valid pulses on consecutive clocks. Snapshotting all four channels plus the valid in one register stage keeps everything aligned at any rate. It also gives you the template: this entity is the skeleton every future DSP block of yours starts from.

Why the **module reference flow** and not "package an IP"? Vivado's IP Integrator can instantiate a plain VHDL entity directly into a block design (`create_bd_cell -type module -reference`). No IP packaging, no wrapper, no Verilog. Mixed VHDL-entity-inside-Verilog-project is fully supported. Packaging as real IP only becomes worthwhile when you need an AXI-Lite register bank (Section 6).

### 3.2 Where files go — and the resetGit landmine

Your VHDL file will live at `plutosdr-fw/hdl/projects/e200/rx_tap.vhd` — **inside the `plutosdr-fw` submodule**. Remember what `resetGit.sh` does: `git reset --hard` + `git clean -xf/-df` in that submodule. It will **delete your file and revert your Tcl edits without asking**. Before anything else, do Section 7.1 (make a git branch and commit as you go). Seriously — do it now, it's four commands.

---

## 4. Step by step: insert the block

### 4.1 Write the VHDL

Create `plutosdr-fw/hdl/projects/e200/rx_tap.vhd`:

```vhdl
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity rx_tap is
  generic (
    -- 0 = transparent pass-through; 1 = replace RX1-I with a sample counter
    TEST_RAMP : integer := 0
  );
  port (
    clk : in std_logic;  -- axi_ad9361 l_clk

    -- RX1 I/Q, from rx_fir_decimator
    data_in_0    : in  std_logic_vector(15 downto 0);
    enable_in_0  : in  std_logic;
    data_in_1    : in  std_logic_vector(15 downto 0);
    enable_in_1  : in  std_logic;
    valid_in     : in  std_logic;

    -- RX2 I/Q, straight from axi_ad9361
    data_in_2    : in  std_logic_vector(15 downto 0);
    enable_in_2  : in  std_logic;
    data_in_3    : in  std_logic_vector(15 downto 0);
    enable_in_3  : in  std_logic;

    -- towards util_cpack2
    data_out_0   : out std_logic_vector(15 downto 0);
    enable_out_0 : out std_logic;
    data_out_1   : out std_logic_vector(15 downto 0);
    enable_out_1 : out std_logic;
    data_out_2   : out std_logic_vector(15 downto 0);
    enable_out_2 : out std_logic;
    data_out_3   : out std_logic_vector(15 downto 0);
    enable_out_3 : out std_logic;
    valid_out    : out std_logic
  );
end entity rx_tap;

architecture rtl of rx_tap is
  signal ramp : unsigned(15 downto 0) := (others => '0');
begin

  process (clk)
  begin
    if rising_edge(clk) then
      -- snapshot all channels + strobe together: alignment is preserved,
      -- total pipeline latency +1 l_clk cycle (harmless)
      data_out_1   <= data_in_1;
      data_out_2   <= data_in_2;
      data_out_3   <= data_in_3;
      enable_out_0 <= enable_in_0;
      enable_out_1 <= enable_in_1;
      enable_out_2 <= enable_in_2;
      enable_out_3 <= enable_in_3;
      valid_out    <= valid_in;

      if TEST_RAMP = 0 then
        data_out_0 <= data_in_0;
      else
        data_out_0 <= std_logic_vector(ramp);
        if valid_in = '1' then
          ramp <= ramp + 1;
        end if;
      end if;
    end if;
  end process;

end architecture rtl;
```

Notes: the ADI convention (Section 2.1) has no ready/backpressure, so a pure pipeline register is a *correct* insertion, not an approximation. The samples are 12-bit sign-extended into 16-bit words — when you later do real DSP here, treat them as `signed(15 downto 0)`.

### 4.2 Add the file + instance to `system_bd.tcl`

Open `plutosdr-fw/hdl/projects/e200/system_bd.tcl`. Find the line `ad_ip_instance util_cpack2 cpack` (line ~248, just before the `# connections` comment). **Insert after it:**

```tcl
# ---- custom RX tap (VHDL module reference) ----------------------------
add_files -norecurse "$ad_hdl_dir/projects/e200/rx_tap.vhd"
create_bd_cell -type module -reference rx_tap rx_tap_0
set_property CONFIG.TEST_RAMP {1} [get_bd_cells rx_tap_0]
# -----------------------------------------------------------------------
```

Why `add_files` *here* and not in `system_project.tcl`: the ADI flow sources `system_bd.tcl` **during** project creation, *before* `adi_project_files` runs (verified in `adi_project_xilinx.tcl`, lines 290–291 vs. 327). A module reference can only resolve entities already added to the project, so the `add_files` must precede the `create_bd_cell` — inside the BD script is the reliable place. `$ad_hdl_dir` is the absolute path to `hdl/`, set up by the ADI environment scripts.

### 4.3 Rewire the connections

Still in `system_bd.tcl`, in the `# connections` region, you'll find these six original lines (~279–288):

```tcl
ad_connect axi_ad9361/adc_enable_i1 cpack/enable_2
ad_connect axi_ad9361/adc_data_i1 cpack/fifo_wr_data_2
ad_connect axi_ad9361/adc_enable_q1 cpack/enable_3
ad_connect axi_ad9361/adc_data_q1 cpack/fifo_wr_data_3

ad_connect cpack/enable_0 rx_fir_decimator/enable_out_0
ad_connect cpack/enable_1 rx_fir_decimator/enable_out_1
ad_connect cpack/fifo_wr_data_0 rx_fir_decimator/data_out_0
ad_connect cpack/fifo_wr_data_1 rx_fir_decimator/data_out_1
```

and, a few lines down (~288):

```tcl
ad_connect rx_fir_decimator/valid_out_0 cpack/fifo_wr_en
```

**Replace all of those** (comment them out with `#` rather than deleting — easier to diff and revert) with the routed version:

```tcl
# clock
ad_connect axi_ad9361/l_clk rx_tap_0/clk

# inputs: RX1 from the decimator
ad_connect rx_tap_0/data_in_0   rx_fir_decimator/data_out_0
ad_connect rx_tap_0/enable_in_0 rx_fir_decimator/enable_out_0
ad_connect rx_tap_0/data_in_1   rx_fir_decimator/data_out_1
ad_connect rx_tap_0/enable_in_1 rx_fir_decimator/enable_out_1
ad_connect rx_tap_0/valid_in    rx_fir_decimator/valid_out_0

# inputs: RX2 straight from axi_ad9361
ad_connect rx_tap_0/data_in_2   axi_ad9361/adc_data_i1
ad_connect rx_tap_0/enable_in_2 axi_ad9361/adc_enable_i1
ad_connect rx_tap_0/data_in_3   axi_ad9361/adc_data_q1
ad_connect rx_tap_0/enable_in_3 axi_ad9361/adc_enable_q1

# outputs: everything into cpack
ad_connect cpack/fifo_wr_data_0 rx_tap_0/data_out_0
ad_connect cpack/enable_0       rx_tap_0/enable_out_0
ad_connect cpack/fifo_wr_data_1 rx_tap_0/data_out_1
ad_connect cpack/enable_1       rx_tap_0/enable_out_1
ad_connect cpack/fifo_wr_data_2 rx_tap_0/data_out_2
ad_connect cpack/enable_2       rx_tap_0/enable_out_2
ad_connect cpack/fifo_wr_data_3 rx_tap_0/data_out_3
ad_connect cpack/enable_3       rx_tap_0/enable_out_3
ad_connect cpack/fifo_wr_en     rx_tap_0/valid_out
```

(`ad_connect` is ADI's helper — argument order doesn't matter, it just wires two pins.)

Leave every other connection alone — in particular `ad_connect axi_ad9361_adc_dma/fifo_wr cpack/packed_fifo_wr` (cpack→DMA) is untouched; we only intercepted cpack's *inputs*.

### 4.4 No device-tree change — and know *why*

This block has no AXI-Lite interface, no address, no interrupt. Linux cannot see it and doesn't need to: the kernel talks to `axi_ad9361` and the DMAs at unchanged addresses. The memory map (Section 2.2) is untouched, so `zynq-e200.dtsi` stays untouched. The device tree describes *the software-visible surface*, not the plumbing. (The day this block grows registers, Section 6 applies.)

### 4.5 Tell make about your file

Remember from Section 0.2: `M_DEPS` in `hdl/projects/e200/Makefile` is the HDL rebuild trigger list, and your `.vhd` isn't in it. Since you edited `system_bd.tcl` (which *is* in `M_DEPS`), the *first* rebuild triggers fine. But later, when you edit **only** `rx_tap.vhd`, make would conclude "nothing changed" and skip Vivado. Fix it once — add to `hdl/projects/e200/Makefile` after the existing `M_DEPS` lines:

```makefile
M_DEPS += rx_tap.vhd
```

(The file header says "Auto-generated, do not modify!" — that refers to ADI's upstream generator; for your local fork this edit is exactly right, and it's now protected by your git branch anyway. The zero-edit alternative is `touch system_bd.tcl` before each build — workable, easy to forget.)

### 4.6 Commit, then build

```bash
cd ~/work/projects/sdr/antsdr-fw-patch/plutosdr-fw
git add hdl/projects/e200/rx_tap.vhd
git commit -am "e200: insert rx_tap module-reference block in RX path"

cd ~/work/projects/sdr/antsdr-fw-patch
source env.sh
arm-linux-gnueabihf-gcc --version   # ritual check: must print 7.3.1
cd plutosdr-fw
sudo -E make
```

Expect the long haul (~40–60+ min): `system_bd.tcl` changed → the HDL sub-make wipes the Vivado project and re-synthesizes everything. Kernel/buildroot parts will fly by (unchanged). Watch the early HDL log for your block: any elaboration error in `rx_tap.vhd` (typo, port mismatch in the `ad_connect` lines) aborts within the first ~2 minutes, so it's worth watching the start. Full log: `hdl/projects/e200/e200_vivado.log`.

When it finishes, confirm timing closed: check `hdl/projects/e200/timing_impl.log` — you want no negative slack (at ≤61.44 MHz for one register stage, failure would be surprising).

Then the SD image:

```bash
sudo -E make sdimg
```

Copy the contents of `build_sdimg/` onto the FAT32 SD card (same as Phase 1 Stage D), jumper on SD, boot, watch the serial console.

### 4.7 The GUI-first prototyping loop (optional but recommended)

For future, more complex insertions, don't hand-edit Tcl blind. Do it visually first:

1. Open `e200.xpr` in the GUI (Section 1), open the block design.
2. Sources panel → **Add Sources** → add your `.vhd`. Then right-click the BD canvas → **Add Module** — your entity appears as a block. Wire it with the mouse. Vivado validates widths and clocks as you go (F6 = validate design).
3. When happy, transcribe what you did into `system_bd.tcl` (every GUI action echoes its Tcl into the Tcl Console — copy from there if unsure of syntax, then convert plain `connect_bd_net` calls to the `ad_connect` style for consistency).
4. Discard the GUI session; rebuild through make. The Tcl is the deliverable — the GUI was scaffolding.

---

## 5. Verify on hardware

Boot the SD build, then from the host (device on USB at `192.168.5.10`, or use the E200's ethernet IP):

```bash
iio_info -u ip:192.168.5.10 | head -30        # sanity: the three IIO devices, as always
iio_readdev -u ip:192.168.5.10 -b 100000 -s 100000 cf-ad9361-lpc > tap_test.iq
```

Then in Python — with `TEST_RAMP = 1`, RX1-I must be a perfect incrementing ramp:

```python
import numpy as np

raw = np.fromfile("tap_test.iq", dtype=np.int16)
# capture layout follows the *enabled* channels; with RX1 I/Q enabled: I,Q,I,Q,...
i = raw[0::2].astype(np.int32)
q = raw[1::2]

d = np.diff(i) % 65536      # counter wraps at 16 bits
print("RX1-I is a clean ramp:", np.all(d == 1))
print("RX1-Q looks like normal RF (std):", q.std())
```

`True` on the first print is the proof: **every sample Linux receives passed through your VHDL.** No noise-floor squinting, no ambiguity. (If you have all four channels enabled in the capture, the interleave is I1,Q1,I2,Q2 — adjust the stride to `raw[0::4]`.)

Now make it disappear: set `CONFIG.TEST_RAMP {0}` in `system_bd.tcl`, rebuild, re-flash the SD, and redo your **Phase 1 Stage E baseline comparison**. With the pass-through in place, captures must be statistically identical to your `baseline_capture.iq`. That pair of results — *provably present, provably transparent* — is Phase 2's checkpoint, and your license to start putting real DSP in the block.

**Recovery, as always:** bad bitstream → board dark or no IIO devices → jumper back to QSPI, boot factory, read the serial log, fix, retry. Nothing is ever bricked.

---

## 6. Next step (roadmap): making the block controllable from Linux

Rebuilding a bitstream to change a parameter gets old fast. The upgrade path, in order of effort:

1. **Give the block an AXI-Lite register interface.** The clean route is packaging your VHDL as an IP with an AXI-Lite slave (Vivado: Tools → Create and Package New IP → "with AXI4 peripheral" generates the boilerplate bus logic; your registers cross into the `l_clk` domain — that CDC is your responsibility, a two-flop synchronizer per quasi-static config register is the standard answer). Study `hdl/library/axi_vcxo_ctrl/` — MicroPhase's IP in this very project is a complete worked example of the pattern, AXI-Lite + packaging scripts included.
2. **Map it** in `system_bd.tcl`: `ad_cpu_interconnect 0x43C10000 my_block_0` (0x43C1_0000 is free — check against Section 2.2 and the Address Editor). This one line does all interconnect plumbing.
3. **First contact without any driver:** from the booted board's shell, `devmem 0x43c10000` reads register 0. Perfect smoke test; not a production interface (no ownership, one typo = bus hang/crash).
4. **Then the device tree earns its keep** — add to the fabric section of `linux/arch/arm/boot/dts/zynq-e200.dtsi`:

   ```dts
   my_block: my_block@43c10000 {
       compatible = "generic-uio";
       reg = <0x43c10000 0x1000>;
   };
   ```

   The E200 kernel already ships UIO support (`CONFIG_UIO_PDRV_GENIRQ=y` in `zynq_e200_defconfig` — verified). One catch: the generic UIO driver only binds to `compatible = "generic-uio"` if told so via kernel command line — append `uio_pdrv_genirq.of_id=generic-uio` to the bootargs (for SD boot: in the `uEnv.txt` on the card). Then the block appears as `/dev/uio0`, and userspace can `mmap` it.
5. **Python over the network** closes the loop: a tiny script on the board (or pylibiio + ssh) reads/writes your registers while `iio_readdev` streams data your logic is processing. At that point you have the full professional loop: VHDL → registers → Python, no rebuild for parameter changes.

A worked version of this section is its own guide — ask for it when the dumb block is boringly reliable.

---

## 7. Troubleshooting & traps (Phase 2 edition)

### 7.1 THE big one: protect your work from `resetGit.sh` — do this first

Your VHDL and Tcl edits live inside the `plutosdr-fw/hdl` submodule, and `resetGit.sh` hard-resets and `git clean`s **five repos independently**: `plutosdr-fw` itself plus its nested `hdl`, `linux`, `u-boot-xlnx`, and `buildroot` submodules. Uncommitted work in any of them is **gone** if it runs. Crucially, committing in `plutosdr-fw` does *not* protect files inside `hdl` — the parent only records which commit `hdl` points at (a "gitlink"), never its file contents. So make a branch in **both** repos you'll touch:

```bash
# hdl: where rx_tap.vhd, system_bd.tcl and the e200 Makefile edits live
cd ~/work/projects/sdr/antsdr-fw-patch/plutosdr-fw/hdl
git checkout -b e200-custom      # branch from the patched state
git add -A && git commit -m "checkpoint: patched v0.39 baseline (hdl)"

# plutosdr-fw: top-level Makefile edits
cd ~/work/projects/sdr/antsdr-fw-patch/plutosdr-fw
git checkout -b e200-custom
git add -A && git commit -m "checkpoint: patched v0.39 baseline"
```

(A submodule is already a complete git repository — a full clone with its own `.git`, history, and remote. "Owned by Analog Devices" only restricts *pushing to their server*; local branches and commits are entirely yours and need no permission. Both repos start in detached HEAD — normal for submodules, which check out a pinned commit rather than a branch — and `git checkout -b` simply gives your current position a name.)

Commit after every working milestone, in whichever of the two repos the changed files live. A branch survives `git reset --hard <pinned-sha>` (it moves HEAD, but your branch ref keeps the commits — `git checkout e200-custom` brings everything back). Uncommitted files do not survive. If you ever need the reset+repatch dance again: commit first, reset, repatch, then `git cherry-pick` or rebase your commits back on top.

### 7.2 The rest of the catalogue

- **"I changed the GUI and my change vanished after make"** → Rule 1. The HDL rebuild `rm -rf`s the project and regenerates from Tcl. Only `system_bd.tcl` / `system_project.tcl` / your committed sources persist.
- **"I edited rx_tap.vhd and make skipped Vivado entirely"** → your file isn't in `M_DEPS` (Section 4.5). Add it, or `touch system_bd.tcl`.
- **"Vivado won't open the project / permission errors"** → root-owned artifacts from `sudo -E make`. Re-run the chown from Section 1.1.
- **Cleaning truly, when needed** → artifacts are root-owned, so `sh resetGit.sh` as your user silently fails on them (you saw this in Phase 1). Use sudo for cleaning: `sudo -E make clean` in `plutosdr-fw`, or `sudo sh resetGit.sh` for the nuclear option — **but only after 7.1**. Keep `buildroot/dl/` (just cached downloads, saves an hour).
- **Elaboration error mentioning `rx_tap` within minutes of the build** → VHDL syntax/port mismatch. Read `hdl/projects/e200/e200_vivado.log`, fix, rebuild — a fast fail costs only minutes.
- **`ERROR: [BD 5-390] ... rx_tap.vhd` / "module not found" on `create_bd_cell`** → the `add_files` line is missing or placed after the `create_bd_cell`, or the path is wrong. It must run before the module reference (Section 4.2).
- **Timing failure (negative slack) in `timing_impl.log` after adding real DSP** → you're in the `l_clk` domain (≤61.44 MHz). Pipeline your arithmetic (more register stages between operations); a multiply-add per stage is comfortable at this speed on -2 silicon. The added latency is invisible to the protocol — there's no backpressure.
- **Board boots but `iio_readdev` hangs / DMA never completes** → usually a broken valid/strobe path — check you connected `cpack/fifo_wr_en` to `rx_tap_0/valid_out` and not left it dangling. The BD validate step (F6 in GUI, or the batch log's critical warnings) catches unconnected pins.
- **Boot hangs right after the kernel starts probing fabric drivers** → memory map and device tree disagree (you moved/removed something with a DT node). Re-check Section 2.2 against your Address Editor. Recovery: QSPI jumper.
- **Vivado version drift** → the top Makefile hard-errors on anything but 2023.2 (checked at line ~18 of `plutosdr-fw/Makefile`). Don't fight it.
- **Utilization creep** → the 7020 is mid-sized and the stock design plus ethernet already occupies a chunk. Check Report Utilization (Section 1.4) before planning a monster FFT.

---

## Why this order matters (the Phase 2 loop)

The ramp test gives you something rare in FPGA work: **absolute certainty about the integration** before you write any interesting DSP. Once "my entity, in the real datapath, on real hardware, visible from Python" is proven, every subsequent iteration is pure VHDL work inside a solved framework: edit `rx_tap.vhd` → `sudo -E make` (+`sdimg`) → capture → analyze in Python. Each loop is under an hour, most of it synthesis, none of it uncertainty about the toolchain. When a parameter change per rebuild becomes the bottleneck, Section 6 is the exit — and the AD9361 datapath you now own end-to-end is exactly the `axi_ad9361 → axi_dmac` insertion the Phase 1 guide promised you'd be repeating with your own HDL in the mix.
