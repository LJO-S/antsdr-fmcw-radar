# AntSDR E200 - Phase 3: AXI-Lite registers, or "control your block from Linux"

Follow-up to the Phase 2 guide (its Section 6 was the sketch; this is the worked
version). Assumes Phase 2 is complete through Section 5: `rx_tap.vhd` sits in the
RX datapath, the TEST_RAMP capture proved it present, and the pass-through build
proved it transparent.

Written in the same spirit as before, with one difference on request: **this guide
specifies and explains, but you write the code.** Where a typo would cost you a
40-minute synthesis run for nothing (port lists, Tcl one-liners, constraint
syntax), the exact text is given. Where the learning lives (the AXI-Lite slave
processes, the testbench, the CDC logic), you get a precise behavioral spec,
the failure modes, and hints - not a solution.

---

## The three rules of Phase 3

1. **Simulate before you synthesize.** A wrong AXI-Lite slave does not produce a
   wrong answer; it hangs the ARM's bus, which freezes the shell mid-`devmem` with
   no error message (the Zynq GP0 port has no timeout by default). The simulator
   shows you the deadlock in seconds; the hardware shows you a dead board and a
   reboot. Every protocol bug in this guide is findable in xsim.
2. **Two clock domains now, forever.** Registers live on `s_axi_aclk`
   (`sys_cpu_clk`, 100 MHz); your datapath lives on `l_clk` (up to 61.44 MHz,
   unrelated phase). Every signal that crosses gets a synchronizer and a
   constraint. This discipline, learned now on one bit, is what makes the future
   chirp-generator/deramp block routine instead of haunted.
3. **Same as always: SD boot, QSPI untouched, commit before `resetGit.sh`.**
   New work goes on a branch (suggested: `feat/axi-regs` in the `hdl` repo).

---

## VHDL naming conventions per LJO-S track record:
- r_* : clocked signals ('r' is playing on the inference of registers)
- w_* : combinatorial or structural signals ('w' is playing on Verilogs wire)
- f_* : functions
- t_* : types
- i_*/o_* : inputs/outputs
- G_* : generics
- C_* : constants

---

## 0. What you are building, and why this exact shape

`rx_tap` grows an AXI-Lite slave port and four 32-bit registers:

| Offset | Name    | Access | Function |
|---|---|---|---|
| 0x00 | MAGIC   | R  | Constant `0x52585431` (ASCII "RXT1"). First-contact proof that the bus, address map, and device tree all agree. |
| 0x04 | CTRL    | RW | bit 0 = `ramp_en`: the TEST_RAMP generic becomes a runtime switch. Crosses into `l_clk`. |
| 0x08 | SCRATCH | RW | No function. Write/readback exercises the full W and R paths independently of any datapath logic. |
| 0x0C | COUNT   | R  | Free-running count of `valid_in` pulses. Crosses back from `l_clk`. Doubles as a live sample-rate meter (read twice, 1 s apart: the delta is FS). |

Why these four: MAGIC fails only if the *plumbing* is wrong; SCRATCH fails only
if your *slave logic* is wrong; CTRL fails only if the *CPU-to-l_clk crossing* is
wrong; COUNT fails only if the *l_clk-to-CPU crossing* is wrong. Four registers,
four independently diagnosable subsystems. Every future block of yours (the
chirp NCO wants start-frequency, slope, sweep-length, enable; the deramp wants
bypass and decimation) is this same skeleton with more rows in the table.

Why hand-written AXI-Lite instead of Vivado's "Create and Package New IP" wizard:
the wizard generates ~400 lines of boilerplate you didn't write and now own, and
it drags in the IP-packaging flow (component.xml, library Makefiles - study
`hdl/library/axi_vcxo_ctrl/` to see the full ceremony). The module-reference flow
you already use in Phase 2 carries an AXI interface just fine, and AXI-Lite is
small enough that writing the slave once, yourself, is the fastest way to ever
stop fearing it. The wizard route stays available for later, when a block wants
interrupts or AXI-Stream.

---

## 1. AXI-Lite in one page (all you must implement)

AXI-Lite is five independent one-way channels, each with the same handshake:

```
sender drives:    valid  + payload
receiver drives:  ready
transfer happens: on the rising clock edge where valid = ready = '1'
```

The one law: **a sender may not wait for `ready` before asserting `valid`**
(deadlock by symmetry), but a receiver MAY wait for `valid` before asserting
`ready`. You are the slave: you receive on three channels (AW, W, AR) and send
on two (B, R), so you get the easy side of the law on three of five.

The five channels, with the exact port names Vivado's interface inference
expects (prefix `s_axi_`, lower case - the names are the contract, do not
improvise):

```
Write address:  s_axi_awaddr(7:0)  s_axi_awprot(2:0)  s_axi_awvalid  ->  s_axi_awready
Write data:     s_axi_wdata(31:0)  s_axi_wstrb(3:0)   s_axi_wvalid   ->  s_axi_wready
Write response: s_axi_bresp(1:0)   s_axi_bvalid       <-  s_axi_bready
Read address:   s_axi_araddr(7:0)  s_axi_arprot(2:0)  s_axi_arvalid  ->  s_axi_arready
Read data:      s_axi_rdata(31:0)  s_axi_rresp(1:0)   s_axi_rvalid   <-  s_axi_rready
Clock/reset:    s_axi_aclk         s_axi_aresetn (active LOW)
```

(Arrows show who drives the ready/valid pair toward whom.)

A complete write, as the master performs it: it presents AW and W (possibly in
either order, possibly simultaneously), you accept both, then you must answer on
B with `bresp = "00"` (OKAY) and hold `bvalid` until the master raises `bready`.
A complete read: master presents AR, you accept, then answer on R with the data
and `rresp = "00"`, holding `rvalid` until `rready`. **"Hold until" is where
bus hangs are born**: if you pulse `bvalid`/`rvalid` for one cycle and the master
wasn't ready that cycle, the transaction is lost and the CPU waits forever.

Simplifications that are legitimate for a v1 slave and used by everyone:

- Wait until `awvalid` and `wvalid` are BOTH high, then accept address and data
  in the same cycle (assert `awready` and `wready` together for exactly one
  cycle). This serializes the write channels and removes all ordering cases.
- Ignore `wstrb` (Linux `devmem`/`mmap` does 32-bit accesses; note the debt).
- Ignore `awprot`/`arprot` entirely.
- Answer OKAY to every address; reads of unmapped offsets return a marker like
  `0xDEADC0DE`. (SLVERR exists; nothing in this project needs it.)
- One transaction at a time: while a write is being answered, keep `arready`
  low, and vice versa if you like - simplest is a single tiny FSM:
  `IDLE -> WRITE_RESP -> IDLE` / `IDLE -> READ_RESP -> IDLE`.

### Exercise 1 - the slave

Extend `rx_tap.vhd`: add the ports above (keep every existing datapath port),
add the four registers, and implement the two transactions per the spec. Hints,
not code:

- One clocked process on `s_axi_aclk` can do the whole slave. Decode the
  register from `awaddr(3 downto 2)` / `araddr(3 downto 2)` (word-aligned;
  bits 1:0 are always 00 from Linux).
- Reset (`s_axi_aresetn = '0'`): all valids/readys you drive go low, CTRL and
  SCRATCH go to zero. Registers holding handshake state MUST reset; the bus
  arrives before your logic has seen a single valid transaction.
- Latch `araddr` when you accept AR - the master is allowed to change it the
  cycle after `arready`, and the mux into `rdata` must use the latched copy.
- The classic bugs, in the order you will meet them: `rvalid` pulsed instead of
  held (read hangs); `bvalid` never raised for writes to unmapped addresses
  (write to a typo'd offset hangs - answer OKAY to everything); `awready`
  asserted while waiting for `wvalid` without handling data arriving first
  (avoided entirely by the wait-for-both simplification).
- Keep TEST_RAMP as a generic if you want, but the ramp mux now takes
  `ramp_en_sync` (Section 2) instead of the generic. Power-on default lives in
  the CTRL reset value.

---

## 2. The clock domain crossings (two of them, both small)

### CTRL.ramp_en: s_axi_aclk -> l_clk

A quasi-static single bit: the two-flop synchronizer is the entire answer.

```vhdl
signal ramp_en_meta, ramp_en_sync : std_logic;
attribute ASYNC_REG : string;
attribute ASYNC_REG of ramp_en_meta : signal is "TRUE";
attribute ASYNC_REG of ramp_en_sync : signal is "TRUE";
```

Clock both flops on `l_clk`: `ramp_en_meta <= ctrl_reg(0); ramp_en_sync <=
ramp_en_meta;`. The `ASYNC_REG` attribute tells Vivado to keep the pair adjacent
and never optimize them - it is the standard idiom, learn it once.

### COUNT: l_clk -> s_axi_aclk

The counter increments on `valid_in = '1'` in the `l_clk` domain. A multi-bit
word can NOT go through a per-bit two-flop synchronizer: the bits are sampled
independently, so around an increment like 0x0FFF -> 0x1000 the reader can see
any Frankenstein mix (0x1FFF, 0x0000...). Two honest fixes:

- **Gray code** (do this one): `gray <= count xor ('0' & count(31 downto 1))`
  registered in `l_clk`, two-flop synchronize the 32-bit gray word into
  `s_axi_aclk`, decode back to binary there. A gray counter changes exactly one
  bit per increment, so a torn sample is always either the old or the new value,
  never garbage. Decode hint: MSB passes through; each lower binary bit is the
  xor of the gray bit with the already-decoded binary bit above it.
- Handshake snapshot (request/ack across domains) - more general (works for
  non-counter payloads), more logic; keep it in your pocket for later blocks.

### Exercise 2 - constraints

Vivado times paths between `sys_cpu_clk` and `l_clk` by default even though
their phase relationship is meaningless, so your synchronizer inputs can produce
phantom timing failures - and worse, phantom *passes* that legitimize nothing.
Declare the crossings on. Add to
`hdl/projects/e200/system_constr.xdc`:

```tcl
set_false_path -to [get_cells -hier -filter {name =~ *ramp_en_meta*}]
set_false_path -to [get_cells -hier -filter {name =~ *gray_sync_meta*}]
```

(Adjust the patterns to your actual signal names; after the first build, verify
they matched something: `report_timing_summary` in the implemented design, or
grep `timing_impl.log` - a false path that matches zero cells is silently
useless. `system_constr.xdc` is already in `M_DEPS`, so editing it triggers
rebuilds correctly.)

---

## 3. The testbench (where this phase is actually won)

### Exercise 3 - write it

A single `tb_rx_tap.vhd`, two clock generators (10 ns period for `s_axi_aclk`;
~16.28 ns for `l_clk` - unrelated periods on purpose, so simulation exposes
crossing artifacts), plus two procedures you will reuse for every future block:

```vhdl
procedure axi_write(addr : in unsigned(7 downto 0); data : in unsigned(31 downto 0); ...);
procedure axi_read (addr : in unsigned(7 downto 0); data : out unsigned(31 downto 0); ...);
```

(The `...` is the bus signals; in VHDL-2008 you pass them as `signal` parameters,
or simply make the procedures local to the stimulus process and drive the
testbench signals directly - the local-procedure route is less typing.)

`axi_write` per the master's rules: drive awvalid+awaddr and wvalid+wdata, hold
until the respective ready (they may accept in the same cycle - your slave
does), drop valids, then wait for `bvalid`, assert `bready` one cycle. Mirror
for `axi_read`. Also drive the datapath: a process that pulses `valid_in` every
N `l_clk` cycles with changing `data_in_*`.

The checklist the testbench must tick (make them `assert`/`report` lines, or VUnit's check(), so a
run is self-judging):

1. Read MAGIC -> 0x52585431.
2. Write SCRATCH = 0xCAFEBABE, read it back.
3. Read unmapped offset 0x10 -> your marker, and the read COMPLETES (this is
   the hang test).
4. Write CTRL = 1; wait ~10 l_clk cycles; `data_out_0` ramps on valid pulses.
   Write CTRL = 0; pass-through returns.
5. Read COUNT twice around a burst of valid pulses; the delta equals the number
   of pulses (gray decode proven).
6. Back-to-back transactions: two writes then two reads with no idle gap.

### Running it

Use VUnit and run.py Python script.

Iterate here until all six checks pass. This loop is seconds, the hardware loop
is an hour - the ratio is the argument.

---

## 4. Into the block design

### 4.1 GUI first (this time it is not optional)

The whole scheme rests on Vivado *inferring* an `s_axi` AXI-Lite interface from
your port names. Verify the inference before touching Tcl:

1. Open `e200.xpr` (chown first if a `sudo make` ran since - Phase 2 Section
   1.1), open the block design.
2. Right-click `rx_tap_0` -> **Refresh Module** (it re-reads the edited VHDL).
3. Look at the block: the AXI ports must have collapsed into a single interface
   pin named `s_axi` (expandable +), with `s_axi_aclk` and `s_axi_aresetn`
   left as loose pins associated to it. If instead you see 19 loose `s_axi_*`
   pins, inference failed - a port name is off. Fix the VHDL, refresh again.
4. Optional but instructive: let **Run Connection Automation** wire `s_axi` up,
   assign 0x43C1_0000 in the Address Editor, press F6 (validate). Then discard
   the session - the Tcl below is the real change. (Automation instantiates its
   own interconnect; the ADI proc does it differently. Prototype only.)

### 4.2 The Tcl (this is the entire integration)

In `system_bd.tcl`, two additions. Where your Phase 2 block is created, connect
the AXI clock/reset explicitly:

```tcl
ad_connect sys_cpu_clk    rx_tap_0/s_axi_aclk
ad_connect sys_cpu_resetn rx_tap_0/s_axi_aresetn
```

Then find the block of `ad_cpu_interconnect` calls (line ~408, ending with
`ad_cpu_interconnect 0x43C00000 axi_vcxo_ctrl`) and add after it:

```tcl
ad_cpu_interconnect 0x43C10000 rx_tap_0
```

That one line grows the CPU AXI interconnect by one master port, wires it to
your `s_axi`, and assigns the address. Read the proc yourself - it is
`ad_cpu_interconnect` -> `ad_hpmx_interconnect` in
`hdl/projects/scripts/adi_board.tcl` (line ~1182) - and notice HOW it finds
things: the slave interface by VLNV `aximm_rtl` filter on your cell, the clock
pin via its `ASSOCIATED_BUSIF` property. That is why the naming convention and
the successful inference in 4.1 are load-bearing, and why the explicit
clock/reset `ad_connect` lines come first (the proc can also find them itself,
but being explicit costs two lines and removes a failure mode).

0x43C1_0000 is free: check Section 2.2 of the Phase 2 guide (vcxo owns
0x43C0_0000) or the Address Editor. The default allocation is 4 KB, ample.

`rx_tap.vhd` is already in `M_DEPS` from Phase 2, `system_bd.tcl` triggers the
rebuild anyway. Commit (on `feat/axi-regs`), then the usual:

```bash
cd ~/work/projects/sdr/antsdr-fw-patch && source env.sh
cd plutosdr-fw && sudo -E make && sudo -E make sdimg
```

Watch the first ~2 minutes for elaboration errors, then check
`timing_impl.log` for negative slack and confirm your false-path constraints
matched (Section 2).

---

## 5. First contact: devmem

Boot the new SD image. On the board (serial console or ssh):

```bash
devmem 0x43c10000            # expect 0x52585431 - MAGIC
devmem 0x43c10008 32 0xCAFEBABE
devmem 0x43c10008            # expect 0xCAFEBABE - SCRATCH
devmem 0x43c1000c; sleep 1; devmem 0x43c1000c   # delta = sample rate
devmem 0x43c10004 32 1       # ramp ON, no rebuild, no reboot
```

With CTRL = 1, rerun the Phase 2 capture + ramp check from the host - the ramp
must be there. Write CTRL = 0, capture again - normal RF. **That toggle, live,
from a shell, is the phase's checkpoint**: parameter changes no longer cost a
synthesis run.

If MAGIC reads back something else: wrong address or the interconnect didn't
wire (check the build log for `ad_cpu_interconnect` errors, and the Address
Editor in the post-build project). If the first devmem FREEZES the shell: your
slave is holding out on a valid - which testbench check 3 was supposed to
catch; go back to xsim, reproduce, fix, rebuild. Recovery from a hang is
power-cycle (QSPI fallback is not needed - the bitstream is fine, the bus is
just wedged).

---

## 6. The proper interface: device tree + UIO

`devmem` is a lab probe: no ownership, no interrupts ever, one fat-fingered
address from a wedged bus. The 10-minute upgrade is UIO - the kernel maps your
block, userspace `mmap`s it through `/dev/uio0`.

1. Add a node to the fabric section of
   `linux/arch/arm/boot/dts/zynq-e200.dtsi` (this file lives in the `linux`
   submodule - note for your branch bookkeeping; a one-file change there is
   fine to carry uncommitted at first, but the resetGit warning applies to
   five repos, this one included):

   ```dts
   rx_tap: rx_tap@43c10000 {
       compatible = "generic-uio";
       reg = <0x43c10000 0x1000>;
   };
   ```

2. The generic UIO driver only claims `generic-uio` nodes if told to on the
   kernel command line. Append to the bootargs in `uEnv.txt` on the SD card:
   `uio_pdrv_genirq.of_id=generic-uio`. Verify after boot:
   `cat /proc/cmdline` shows it, and `/sys/class/uio/uio0/maps/map0/addr`
   reads `0x43c10000`.
3. Rebuild (`sudo -E make` regenerates the dtb; the bitstream part is
   unchanged but will rebuild anyway if you touched hdl - device-tree-only
   changes rebuild fast).

### Exercise 4 - a 30-line C tool

The rootfs has no Python, but you have a cross-compiler with 7.3.1 muscle
memory. Write `rxtap.c`: open `/dev/uio0`, `mmap` 4 KB (PROT_READ|PROT_WRITE,
MAP_SHARED, offset 0), cast to `volatile uint32_t *`, then argv-driven
peek/poke: `rxtap 1` reads offset 1 (word index), `rxtap 1 0x1` writes it.

```bash
arm-linux-gnueabihf-gcc -O2 -o rxtap rxtap.c
scp rxtap root@192.168.2.1:/root/
```

The host-side loop then closes exactly like the radar's future control path:
Python on the host, `subprocess`/`paramiko` over ssh, `rxtap` on the board -
while `iio_readdev` streams the data your register writes are shaping. When the
chirp-gen block exists, "reconfigure the radar" is a handful of these writes.

---

## 7. Troubleshooting catalogue (Phase 3 edition)

- **devmem freezes the shell** -> slave never completed the transaction
  (`rvalid`/`bvalid` not held, or not raised for that address). Fix in xsim
  first; testbench checks 3 and 6 cover the known causes.
- **MAGIC reads 0x0 or garbage** -> address map problem, not protocol: check
  `ad_cpu_interconnect` really ran (build log), address 0x43C10000 in Address
  Editor of the post-build project.
- **Vivado: 19 loose s_axi pins instead of one interface** -> a port name off
  the convention (a stray upper-case, `awvalid` vs `awvald`...). Fix names,
  Refresh Module. If inference still balks, the heavy hammer is
  `X_INTERFACE_INFO` attributes in the VHDL - look them up (UG994), but with
  correct names you should never need them.
- **`ad_cpu_interconnect` errors in the batch log about clock pins** -> the
  proc found no `ASSOCIATED_BUSIF` clock; usually the same naming issue, or the
  explicit `ad_connect sys_cpu_clk rx_tap_0/s_axi_aclk` line is missing/after
  the interconnect call. Order: create cell, connect clocks, then interconnect.
- **CTRL writes work but the ramp never engages** -> the crossing: check you
  muxed on `ramp_en_sync` (l_clk domain), not `ctrl_reg(0)` (axi domain).
  Visible in xsim as a mux that switches, just one domain too early.
- **COUNT deltas look insane** -> torn multi-bit crossing: gray encode missing
  or decoded wrong. Testbench check 5 in xsim with truly unrelated clock
  periods reproduces it within a few hundred increments.
- **Timing failures naming s_axi paths against l_clk** -> your false-path
  patterns didn't match the actual cell names; fix the patterns (Section 2
  exercise), don't "fix" the logic.
- **/dev/uio0 missing** -> bootargs (`/proc/cmdline`), then the dtb (does
  `/proc/device-tree/` contain `rx_tap@43c10000`?), in that order.
- **It all worked and then vanished after a rebuild** -> you know this one.
  Which branch were you on, and did you commit?

---

## Where this leads (so the registers get designed with the future in mind)

The point of Phase 3 for THIS project (see TODO.md Part I): the radar's fabric
endgame is chirp generation + dechirp in PL, with Ethernet carrying a few MSPS
of IF instead of 226 MB/s of raw IQ. Every block in that chain is the Phase 3
skeleton with a different register map:

- chirp NCO: enable, phase-increment start, phase-increment slope, sweep
  length, TX-source mux (DMA passthrough vs NCO) - all quasi-static CTRL-style
  registers crossing into l_clk exactly like `ramp_en`.
- deramp: bypass bit (KEEP the raw path available - `dsp.py` stays the golden
  model that verifies the fabric output), decimation select, and COUNT-style
  status readbacks for sanity metering.

When a block eventually needs to signal the CPU (frame-complete interrupts),
that is the moment to graduate from `generic-uio` polling to UIO with an
interrupt line - and possibly to the IP-packaging flow. Not before.
