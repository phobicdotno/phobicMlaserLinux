# M1 bench session: running the 11 §7 confirmation on the real machine

This runbook is for the machine owner. It walks through the ten-step "M1 confirmation" session of
[`analysis/11-static-findings.md` §7](analysis/11-static-findings.md) on the Gweike M3 Ultra, with the
**laser key OFF**. You finish with a packet capture and a written report, and the port's remaining
NARROWED items (K, bus cycle, block 5000, firmware licence gating, bit 31, stop profile, home vector,
limit polarity, tick period, Continue path, exception 2/3) can then be settled from that evidence.

Everything is driven by `tools/m1_session.py`. The tool:

* talks only to the `nexcut-mccd` daemon over its IPC socket. It never opens the card socket, and
  every vector passes the daemon's safety gate (docs/DECISIONS.md D5, PORT-PLAN §8.2);
* will not start until you have confirmed each item of the safety checklist with `y`;
* runs all reads by itself;
* before every motion, prints the exact vector(s) and asks `Send this motion? [y/N]`. Only `y` or
  `yes` sends it; any other answer, including just Enter, skips it. The daemon is armed only after
  that `y`, and disarmed again at the end of the step;
* writes `~/mlaser-captures/m1-<date>/report.json` and `report.md` after every step, so a crash
  keeps what was already recorded;
* at the end runs `python -m nexcut.mcc.dissector` on the pcap and adds its summary, plus a traffic
  census for each step's time window.

Plan for about 1.5 hours without the vendor variants and about 1 hour more with them. Two people
are better than one: one keeps a hand on the E-stop and watches the machine, the other types.

---

## Contents

1. [Safety rules and prerequisites](#1-safety-rules-and-prerequisites)
2. [Rehearse on the simulator first](#2-rehearse-on-the-simulator-first)
3. [Network setup on the Ubuntu laptop](#3-network-setup-on-the-ubuntu-laptop)
4. [Packet capture](#4-packet-capture)
5. [Start the daemon against the card](#5-start-the-daemon-against-the-card)
6. [Run the session tool](#6-run-the-session-tool)
7. The ten steps: [1](#step-1-connect-start-up-reads) · [2](#step-2-block-5000-and-the-e-stop) ·
   [3](#step-3-jog-x-5-mm-at-20-mms) · [4](#step-4-bit-31--absolute) ·
   [5](#step-5-continuous-jog-and-key-release-stop) · [6](#step-6-home-x-then-y) ·
   [7](#step-7-limit-switches-by-hand-y2-tracking) · [8](#step-8-fifo-tick-period) ·
   [9](#step-9-pause--continue--stop-in-the-vendor-tool) · [10](#step-10-power-cycle-read-10002-immediately)
8. [Vendor tool under Wine (for the vendor variants)](#8-vendor-tool-under-wine-for-the-vendor-variants)
9. [After the session](#9-after-the-session)
10. [Troubleshooting](#10-troubleshooting)
11. [Known limits of this build and UNVERIFIED items](#11-known-limits-of-this-build-and-unverified-items)

---

## 1. Safety rules and prerequisites

**Physical prerequisites.** The tool asks you to confirm each of these before it starts, and refuses
to start if any answer is not `y`:

| # | Item | Why |
|---|---|---|
| 1 | **Laser power OFF**: CO2 tube PSU key or enable off, **and** the fibre source's emission key off | Hardware laser interlock (PORT-PLAN §8.2). Software cannot turn on a PSU that is switched off. The daemon also has no laser-arming command in this phase. |
| 2 | **Hardware E-stop within reach** for the whole session, and checked that it stops the machine | The hardware E-stop is the primary stop. The software stops are secondary. |
| 3 | **Gantry parked mid-bed**, at least 100 mm from every X and Y end stop | Un-homed jogs have no position reference (D1). Steps 3 and 5 move about 25 mm in X+ natively, plus about 40 mm more with the vendor variants, before homing. |
| 4 | **Bed and path clear**: no material, tools or loose objects; head and Z clear of the bed; nobody else near the machine | Homing (step 6) runs the full axis. The vendor dry run may command the Z follower (step 8). |
| 5 | **Assist gas closed** (air / N2 / O2 at the cylinder or compressor) | The daemon's stop sequence switches gas DOs off. The vendor dry run switches DO3 "high air" on (11 §5.4). |
| 6 | **Only one master on the card**: the machine's own control PC is unplugged from the card network, and Mlaser (Windows or Wine) is not running while `nexcut-mccd` is | Two programs polling and commanding the card at once corrupt each other's sequence numbers and state. |

**If anything unexpected moves: press the hardware E-stop first.** Then press Ctrl-C in the tool,
which sends `stop` and `disarm`. Other ways to stop:

* `nexcut-mccd estop` from any terminal;
* stopping the daemon (Ctrl-C in its terminal), which disarms and stops before it exits.

If the tool dies or hangs while a jog is running, that jog stops on its own. Continuous and long
step jogs run under the daemon's 200 ms deadman, and the tool refreshes them from a separate thread
only until a fixed deadline.

**What the session never does.** It never writes register 150/151, the licence area 59500–59599,
hardware parameters 59600+ or block 5000. It never runs system home, lift-table or roll-feeder
moves, and never streams FIFO frames. The daemon's allow-list refuses all of these (11 §3.3).

**Things to have at hand:**

* a steel ruler or tape;
* a phone stopwatch (steps 8 and 9);
* a small steel plate, in case the limit switches are inductive (step 7);
* this runbook, open on a second screen or printed.

---

## 2. Rehearse on the simulator first

Do this the day before. Nothing here touches the machine.

```sh
cd ~/phobicMlaserLinux
.venv/bin/nexcut-mccd serve --sim -v                    # terminal 1
.venv/bin/python tools/m1_session.py \
    --out-dir /tmp/m1-rehearsal --no-dissector           # terminal 2
```

Answer every prompt as you would at the machine. With the simulator, some results will read
INCONCLUSIVE or NOT SEEN because nothing physical happens. The rehearsal is to learn the prompts,
not to get results. `tests/test_m1_session.py` runs the same session automatically on each test run.

---

## 3. Network setup on the Ubuntu laptop

The card is `10.1.1.168` on UDP port 502. The Z follower is at `.169` and the laser source at `.170`
(docs/analysis/10). The laptop gets `10.1.1.10/24` on its wired NIC, **with no gateway**, so Wi-Fi
stays the default route.

**Find the NIC name** (`<nic>`). Plug the Ethernet cable into the laptop, or into a USB-Ethernet
adapter, and connect the other end to the card's network, where the machine PC was plugged in. Then:

```sh
nmcli device status          # the wired one: TYPE ethernet, e.g. enp0s31f6 or enx00e04c68...
ip -br link                  # the same names; unplug/replug the cable and see which one goes UP/DOWN
```

**Create and bring up the connection:**

```sh
nmcli con add type ethernet ifname <nic> con-name mlaser-m1 \
    ipv4.method manual ipv4.addresses 10.1.1.10/24 ipv4.never-default yes ipv6.method disabled
nmcli con up mlaser-m1
ip -br addr show <nic>       # expect 10.1.1.10/24
ip route                     # "default via ..." must NOT name <nic>
ping -c 3 10.1.1.168         # the vendor ships File/pingMC.bat doing exactly this
```

A missing ping reply does not prove the card is unreachable. The daemon in §5 is the real test.
Make sure no other device on that segment already uses `10.1.1.10`. The machine PC is unplugged
anyway (checklist item 6).

**Revert after the session:**

```sh
nmcli con down mlaser-m1
nmcli con delete mlaser-m1
```

---

## 4. Packet capture

`tcpdump` needs root. Inside Claude Code on this laptop `sudo` has no TTY and fails. Use `pkexec`,
which shows a **graphical password dialog** on the desktop. Use a separate terminal:

```sh
mkdir -p ~/mlaser-captures
pkexec tcpdump -i <nic> -s 0 -w ~/mlaser-captures/m1-$(date +%F).pcap \
    'host 10.1.1.168 or host 10.1.1.169 or host 10.1.1.170'
```

Recommended additions, which should also work in the command above:

* `-U` writes each packet immediately, so a crash or unplugged laptop loses nothing.
* `-Z "$USER"` makes tcpdump drop root privileges to your user before it opens the file. Ubuntu's
  tcpdump otherwise switches to the `tcpdump` user, which may not be allowed to write to your home
  directory. The file is also owned by you afterwards. UNVERIFIED on this laptop: if you get
  "Permission denied", use `-Z root` and fix the owner afterwards (§9).

```sh
pkexec tcpdump -i <nic> -s 0 -U -Z "$USER" -w ~/mlaser-captures/m1-$(date +%F).pcap \
    'host 10.1.1.168 or host 10.1.1.169 or host 10.1.1.170'
```

`$(date +%F)` and `$USER` are expanded by your shell before `pkexec` runs. Keep tcpdump running
from before §5 until the tool tells you to stop it at the end, then stop it with Ctrl-C. The tool
expects the file at `~/mlaser-captures/m1-<YYYY-MM-DD>.pcap`. If you used another path, pass it
with `--pcap`.

If you run the vendor variants, turn on `EnableLog=1` as well (§8). That makes Mlaser write every
frame with timestamps to its own log, a second record next to the pcap.

---

## 5. Start the daemon against the card

In a second terminal:

```sh
cd ~/phobicMlaserLinux
.venv/bin/nexcut-mccd serve --card-ip 10.1.1.168 -v
```

The real card address is only used when you pass `--card-ip` yourself (docs/DECISIONS.md D3).
Expect:

* `nexcut-mccd ...: IPC on /run/user/<uid>/nexcut/mccd.sock (card 10.1.1.168)`
* `REAL CARD at 10.1.1.168:502 (explicit --card-ip)`
* `card connected, version 20152` (or another version at or above `MinHardwareVer` 20152)

On connect, the daemon runs the start-up sequence of 11 §7 step 1:

1. `READ 1000/2`, `READ 1000/36`, `READ 50000/26`, `READ 50200/100`;
2. the connect write `0x65 <- [9999, 5, 0, 0]` (11 §2 V0, N4);
3. polling of `1000/36` every 30 ms.

It **stays disarmed**: nothing moves until the tool arms it after a motion `y`.

Check it from a third terminal: `.venv/bin/nexcut-mccd status`. It should show
`link CONNECTED`, `DISARMED` and machine state `READY`. Do not use the TUI to move the machine
during the session: every motion should come from the tool so that it is recorded.

**Only one daemon may talk to the card** (docs/DECISIONS.md D12). A second `nexcut-mccd` on the same
`--card-ip` — even with another `--socket` — is refused before it sends anything:

```
nexcut-mccd: another nexcut-mccd is already driving the card at 10.1.1.168:502 (pid 12345);
only one master per card (docs/DECISIONS.md D12, lock /run/user/1000/nexcut/card-10.1.1.168-502.lock)
```

If you see that and no daemon should be running, `ps -p <pid>` names the holder. A daemon that was
killed with `kill -9` leaves the lock file behind but not the lock, so the next start just takes it —
never delete the lock file by hand while a daemon is running. This lock covers only this laptop and
this user: it cannot see Mlaser under Wine or the machine's own control PC (checklist item 6).

**Arming — and the right to move with it — belongs to one connection** (docs/DECISIONS.md D9).
`nexcut-mccd arm` in one terminal followed by `nexcut-mccd jog` in another no longer works: the
daemon disarms as soon as the `arm` process exits. And even *while* the session tool is armed, a
`jog`, `home` or `run-job` sent from another terminal is refused — arming is not a machine-wide
mode, it is held by the connection that asked for it:

```
arming: jog_step needs MOTION_ARMED armed on this connection (another connection holds the
arming): arming - and the right to move with it - belongs to the connection that asked for it
(docs/DECISIONS.md D9). Send arm_motion on this connection, or use 'nexcut-mccd jog --arm' /
'home --arm' / 'run-job --arm'
```

That is deliberate: during the session every motion must come from the tool, so that it is
recorded. **Stopping is never owned** — `nexcut-mccd stop`, `estop`, `ack-estop` and `disarm` work
from any terminal at any time, which is what makes it safe to leave the session tool holding the
arming. For a one-shot move outside the session tool use

```sh
.venv/bin/nexcut-mccd jog X 5 --speed 20 --arm      # arms, moves, waits, disarms
.venv/bin/nexcut-mccd home X --arm
```

`tools/m1_session.py` and the TUI keep one connection open for commands, so they are unaffected.
The TUI's deadman refresher runs on a second connection on purpose: `jog_refresh` is not owned, so
a jog keeps its lease, but a refresh cannot revive a lease while the machine is disarmed (D6).

**TUI keys changed** (docs/DECISIONS.md D11). If you open `.venv/bin/nexcut-mccd tui` to watch status
or to recover between steps, note that **no letter key starts motion any more**: the vi-style
`H J K L` continuous-jog keys are gone. Press `?` in the TUI for the full list.

| Key | Action |
|---|---|
| space or `s` | STOP (works in every mode, either case) |
| Esc or `e` | E-STOP, latches |
| `a` | acknowledge the E-stop latch (was `A`) |
| `m` / `d` | arm motion / disarm |
| arrows | step jog X/Y by the current step size |
| PgUp / PgDn | step jog W (lift table) |
| **Shift+arrows** | continuous jog X/Y while held — the only continuous-jog keys |
| `[` `]` | step size 0.1 / 1 / 10 mm |
| `h` then `x` / `y` | home one axis (`b` = X then Y, only with `--allow-home-all`; was `a`) |
| `r` | read a register block `ADDR/N` |
| `?` / `q` | key list / quit (the daemon keeps running, and disarms — D9) |

Every printable key works with Caps Lock on: each is bound in both cases. Only STOP and E-STOP are
global; `a` (acknowledge) is a normal-mode key, so it cannot shadow the home menu's `x` / `y` / `b`.
Continuous jog needs a terminal that sends Shift+arrow — xterm (`CSI 1;2A`) and rxvt (`CSI a`) both
do. Arming in the TUI (`m`) and its jogs share one connection, so the D9 rule above is invisible
there.

---

## 6. Run the session tool

In the third terminal:

```sh
.venv/bin/python tools/m1_session.py
```

| Option | Default | Use |
|---|---|---|
| `--steps 1-10` | all | run a subset, e.g. `--steps 6,7` to repeat steps. An existing `report.json` / `report.md` in the directory is renamed `report-HHMMSS.*`, never overwritten |
| `--pcap FILE` | `~/mlaser-captures/m1-<date>.pcap` | capture file analysed at the end |
| `--out-dir DIR` | `~/mlaser-captures/m1-<date>` | report directory |
| `--step-mm 5` / `--step-speed 20` | 5 mm, 20 mm/s | steps 3 and 7 jog (max 10 mm, the un-homed step limit of D1; max 50 mm/s) |
| `--cont-speed 20` / `--cont-hold-s 1.0` | 20 mm/s, 1 s | step 5 continuous jog (hold at most 3 s) |
| `--switches X-,X+,Y-,Y+,Y2-,Y2+` | these six | switches to test in step 7 |
| `--home-timeout-s 180` | 180 s | step 6 wait. The daemon's own homing supervision stops the axis after 120 s (`mccd.home_timeout_ms`) |
| `--power-cycle-timeout-s 300` | 300 s | step 10 wait for reconnection |
| `--vendor-log-dir DIR` | `~/.wine/drive_c/users/$USER/AppData/Local/NexCut/Log` | Mlaser logs to copy into the report |
| `--socket PATH` | `$XDG_RUNTIME_DIR/nexcut/mccd.sock` | daemon socket |
| `--date YYYY-MM-DD` | today | the date tag used in the default pcap and report paths |
| `--motion-timeout-s 30` | 30 s | how long one motion may take before the step is recorded as timed out |
| `--connect-timeout-s 30` | 30 s | how long to wait for the daemon to report `CONNECTED` at the start |
| `--estop-wait-s 5` | 5 s | step 2: how long to wait for alarm word 1006 bit 30 to follow the hardware E-stop |
| `--no-dissector` | off | skip the pcap analysis |

How it prompts:

* `[y/N]`: only `y` or `yes` counts as yes. Every step starts with "Start this step? (N = skip it)".
* `[Enter]`: do the physical action first, then press Enter. The tool timestamps the moment.
* Free-text questions: type what you saw, with measured numbers where you have them. Enter leaves
  the answer blank.
* Ctrl-C at any time sends `stop` + `disarm`, marks the report `interrupted` and exits.

Each step also sends a harmless marker read `READ 1000/1` at its start and end. The daemon never
sends that read itself, so the analyst can cut the pcap into steps even without the report's
timestamps.

**At the end of every motion step the daemon sends its stop sequence.** That is `[1, 31, 2, vd, 10vd]`,
DO/DA/PWM off and `[101]`, sent when the tool disarms (PORT-PLAN §8.2). It is expected in the
capture and is not a fault.

---

## 7. The ten steps

Vectors are the words after `[0x40, 0x65, n]` (11 §2). Units: speeds and distances × K (K = 1000
card units/mm), acceleration in plain mm/s², jerk = 10 × acceleration.

### Step 1: Connect, start-up reads

**Purpose.** Confirm the card-authoritative scalars the planner and FIFO depend on (11 §3.2, C23,
O1).

**Operator actions.** Nothing physical. Watch the daemon terminal.

**What the tool sends.** `READ 1000/2`, `READ 1000/36`, `READ 50000/26`, `READ 50200/100`. The
daemon already sent these and `[9999, 5, 0, 0]` at connect time, before the tool started. The pcap
has both.

**Observe physically.** No motion. Note any panel or LED change.

**Expected results.**

| Hypothesis | Confirmed if | If not |
|---|---|---|
| Version ≥ `MinHardwareVer` | reg 1001 ≥ 20152 | the daemon would already have refused (`VERSION_REFUSED`) |
| K = 1000 (A1 §2) | 50000 word 17 = 1000 | every speed and distance scale in `commands.py` changes. Stop and report |
| Bus cycle 250 µs (11 §5.3) | 50000 word 5 = 250 | the tick period used by the planner changes (O1) |
| ZFType = 1 | 50000 word 6 = 1 | CO2 jobs would not emit ZF records (C6) |
| Axis scale (C23) | 50200 axis-0 word 10 ÷ word 9 (lead in mm or µm) ≈ 8000/31.003 = 258.04 p/mm | X tick factor comes from the card, Y from XML. Note both values |
| FIFO empty | reg 1016 = 60000 (checked again in step 8) | C2 units |

**Write down.** The panel state, and the `card connected, version …` line from the daemon.

### Step 2: Block 5000 and the E-stop

**Purpose.**

* N3: what block 5000 holds (the vendor never reads it).
* Confirm alarm_1 (1006) bit 30 = E-stop (11 §4.2).
* See which DI the E-stop is wired to, and whether the card inverts it.

**Operator actions.** When prompted:

1. press the hardware E-stop, then Enter;
2. release it (twist out), then Enter;
3. answer `y` to acknowledge the daemon's E-stop latch.

**What the tool sends.** `READ 5000/9` and `READ 1000/36` three times: idle, pressed, released.
`ack_estop` goes to the daemon only (no card traffic). When the card reports bit 30, the **daemon
latches E-stop and sends its E-stop stop sequence by itself**. Expect it in the capture.

**Observe physically.** Whether the drives lose enable (servo LEDs, brake click), and the alarm
lamp and buzzer.

**Expected results.**

| Hypothesis | Confirmed | Contradicted / other |
|---|---|---|
| alarm_1 bit 30 follows the E-stop | set while pressed, clear after release | the port's E-stop latch source is wrong: report the actual alarm word |
| Block 5000 reflects the e-stop port (N3) | some word of 5001–5008 changes between idle and pressed | no change: block 5000 is config only. A read error means the card refuses the block (note the error) |
| DI inversion | `di_ports_changed_by_estop` names one DI whose level flips | none: the E-stop is not wired to a DI the PC sees |

**Write down.** The drive, lamp and buzzer behaviour, and anything shown on the panel.

### Step 3: Jog X +5 mm at 20 mm/s

**Purpose.**

* O6: does the firmware execute motion without the vendor licence exchange?
* N5: exception 3 on a jog sent while the axis is moving.
* Axis status byte 2 (busy) and byte 3 (command type) while jogging.

**Operator actions.**

1. Answer whether to also send the jog a second time while moving (recommended `y`).
2. Check the printed vector.
3. Hand on the E-stop, then `y`.
4. Measure the X travel with the ruler.

**What the tool sends.** `0x65 <- [3, 0, 20000, 5999, 59990, 5000]` (V2). 5000 µm is more than
20 mm/s × 200 ms, so the gate places the step under the deadman. The tool refreshes it for
`5/20 × 1.25 + 0.3 ≈ 0.6 s`. `READ 2000/50` runs every ~50 ms until the axis is idle. The optional
second jog is **refused by the daemon before the wire**: it mirrors the card's "READY only" gate
(A1 §1, D8), so it never reaches the card.

**Observe physically.** X moves 5 mm in the + direction, slowly, then stops. Note which side "+" is.

**Expected results.**

| Hypothesis | Confirmed | Contradicted |
|---|---|---|
| O6 no firmware gate on jog | pulse position changes and/or busy byte 2 seen | nothing moves and no busy bit: suspect firmware licence gating. **Stop the session** and report it |
| Axis status bytes | byte 2 ≠ 0 while moving; byte 3 = 3 (jog) | note the values seen |
| N5 exception 3 | only in the vendor variant capture (reply `exception(503)`) | – |

**Write down.** Measured travel, direction, noise.

**Vendor variant (optional, A/B + N5).** Follow the handover in §8, then in Mlaser:

1. set step mode, step length 5 mm, slow speed (50 mm/s);
2. click X+ once and wait for it to stop;
3. click X+ twice quickly.

The capture should show `[3, 0, 50000, 5999, 59990, 5000]`, and the quick second click answered
with exception 3.

### Step 4: Bit 31 = absolute

**Purpose.** Show that bit 31 on the axis word means an absolute target (A1 §3.3, INFERENCE
medium-high).

**Native part: deferred in this build.** The planned vector is
`[3, 0x80000000, 20000, 5999, 59990, 100000]`, sent twice: an absolute move stays at 100 mm, a
relative one would go on to 200 mm. `nexcut-mccd` has no IPC command for an absolute single-axis
move yet. The gate would classify it MOTION_HOMED with soft limits (11 §3.1). The tool records the
planned vector and marks the step `partial`.

**Vendor variant.** Follow the handover in §8, then in Mlaser:

1. home first if it insists;
2. use "go to point" (ribbon; message mp112 "System will go to input point") with a target
   **20 mm from the current position**;
3. when it has stopped, send exactly the same target again.

**Go-to runs at 550 mm/s** (XFastMoveSpeed 500 × EmptyMoveSpeedFactor 1.1, 11 §2 V8). Path clear,
hand on the E-stop.

Tool marks: Enter after the first and after the second go-to.

| Result | Meaning |
|---|---|
| Second go-to does not move; capture shows `[5, 0x80000003, 550000, 8999, 89990, x, y, 0, 0]` twice | bit 31 = absolute on sub-command 5, which supports the same reading for sub-command 3 |
| Second go-to moves again by the same offset | bit 31 is not "absolute". Report it: the port's go-to and absolute move builders are wrong |

**Write down.** Whether the second go-to moved, and the target coordinates entered.

### Step 5: Continuous jog and key-release stop

**Purpose.** The stop profile: is `vd` (words 3 and 4 of `[1, mask, 2, vd, 10·vd]`) a deceleration
in mm/s²? (A1 §4.1)

**Operator actions.** Check the two printed vectors, then `y`. The tool holds the jog for
`--cont-hold-s` (1 s) and then releases it. Watch how X stops.

**What the tool sends.**

1. start: `[3, 0, 20000, 5999, 59990, 400000]` (V1: d = 20 × JogFastSpeed × K, and the daemon sets
   JogFastSpeed to the requested speed), with deadman refreshes every 50 ms;
2. release: `[1, 1, 2, 5000, 50000]` (V3: vd = clamp(100000 ÷ 20, 2000, 8000) = 5000);
3. `READ 2000/50` every ~50 ms during the jog and after the stop (speed word +1, position word +2).

11 §7 asks for 50 mm/s, which gives `[1, 1, 2, 2000, 20000]`. The daemon allows a continuous jog
above 20 mm/s only on a homed axis with a verified position scale (DECISIONS D1/D2), so the native
run uses 20 mm/s. The vendor variant gives the 50 mm/s vectors.

**Observe physically.** About 20 mm of travel, then a stop. Note whether it stops abruptly or with
a visible ramp.

**Expected results.**

| Hypothesis | Supported | Against |
|---|---|---|
| vd = decel in mm/s² | at 5000 mm/s², stopping from 20 mm/s takes ~4 ms and ~0.04 mm: an "instant" stop; `position_counts_travelled_after_stop` ≈ 0 | a long ramp (tens of ms, mm of travel) means vd is something else (e.g. a time or a jerk). Compare with the vendor variant at vd = 2000 |

**Write down.** Stop feel, estimated stopping distance, total travel.

**Vendor variant (A/B at 50 mm/s).** Follow the handover in §8, then in Mlaser:

1. continuous jog (step mode off), slow speed 50 mm/s;
2. hold X+ for about half a second and release.

The capture should show `[3, 0, 50000, 5999, 59990, 4000000]` and then `[1, 1, 2, 2000, 20000]`.

### Step 6: Home X, then Y

**Purpose.**

* Confirm the home vector `[2, 1<<slot, 0]` (V6).
* Watch the card-side homing speeds and back-off.
* Confirm homed bit 15 of the axis status word.

**Operator actions.** Two separate motions, X then Y, each with its own `y`.
**Homing can travel the full axis at a speed set on the card** (unknown to the port, AxisRW
+5/+6 UNVERIFIED). Hand on the E-stop.

**What the tool sends.** `home {slots:[0]}`, which the daemon sends as `[2, 1, 0]`. When X reports
homed, `home {slots:[1]}` goes out as `[2, 2, 0]`. The tool reads `2000/50` about every 50 ms until
`homing` is empty and the slot is in `homed_slots`.

**Observe physically.** Direction, fast approach, slow re-approach, back-off distance, final corner.

**Expected results.**

| Hypothesis | Confirmed | Contradicted |
|---|---|---|
| `[2, 1, 0]` homes X only; `[2, 2, 0]` homes Y (+Y2) | only that axis moves; bit 15 set at the end | another axis moves, or nothing: stop and report |
| homed bit 15 | `homed_bit15_X/Y` CONFIRMED | homed but bit 15 clear: the homed flag is elsewhere (A2 §3.1 INFERENCE) |
| byte 3 = 2 while homing | samples show `cmd_type_byte3` 2 | note the value |

**Write down.** For each axis: direction and corner, speed estimates, back-off, and whether Y2
followed Y.

### Step 7: Limit switches by hand; Y2 tracking

**Purpose.**

* O8: which DI and which axis status bit each hard limit raises, and with what polarity.
* O7: does slot 2 (Y2) track slot 1 during a Y jog?

**Operator actions.**

1. Press the E-stop (drives disabled) and keep it pressed. Enter.
2. For each switch in `--switches`, answer `y`, then actuate and **hold** it: press a mechanical
   switch by hand, or hold the steel plate against an inductive sensor. The tool reads. Release,
   Enter. Answer `N` for a switch that does not exist.
3. Release the E-stop, Enter, and acknowledge the latch with `y`.
4. Say which Y direction moves **away** from the Y home end you saw in step 6: the tool asks "Y+ away?" and, on anything but `y`, "Y− away?"; if neither gets a `y` the Y jog is skipped (an unsure answer never picks a direction),
   so the 5 mm jog never runs into the home switch.
5. Confirm the Y jog.

**What the tool sends.**

* `READ 1000/36` (DI word 1004) and `READ 2000/50` for the baseline, then again with each switch
  held and after release.
* Then `[3, 1, 20000, 5999, 59990, ±5000]` (Y ±5 mm away from home, 20 mm/s), sampling slots 1
  and 2 (reg 2012 and 2022).
* While a limit bit is set, the daemon may send a stop by itself (axis-fault reaction, 11 §4.7).
  That is expected.

**Observe physically.** Switch LEDs, if any. During the Y jog, whether both gantry sides move.

**Expected results.**

| Hypothesis | Confirmed | Other |
|---|---|---|
| Hard + limit → status bit 0 of that slot, hard − → bit 1 (A2 §3.1) | `axis_bits_changed` shows bit 0 for X+, bit 1 for X−, and likewise for Y/Y2 | swapped bits mean `axis_limit_blocks()` direction mapping must be swapped (record it) |
| Each switch maps to one DI | `di_changed` names one port per switch; `level_held` gives polarity (raw level, NO/NC not applied) | two ports or none: record it. This decides `watchdog_di_alarms` and the NO/NC table |
| Y2 dual drive (O7) | `y2_slot2_delta` ≠ 0 and ≈ `y_slot1_delta` | slot 2 stays 0: Y2 is not a separate card slot, or it does not report |

**Write down.** Switch types, any switch that did not register, whether both Y sides moved.

### Step 8: FIFO tick period

**Purpose.**

* O1: the card-side tick period.
* C2: units of reg 1016.
* The frame-id rule (reg 1015 + 1).
* O6: FIFO start without the licence exchange.

**Native part.**

* The tool reads the FIFO baseline from `1000/36`: 1015 frame id, 1016 margin (expected 60000 when
  empty), 1017 and 1019.
* The streaming part **exists in software since 2026-09-16** but has only ever run against the
  card simulator, so it is *not* part of this session's required sequence. If the owner chooses to
  run it, the sequence is:

  ```sh
  .venv/bin/nexcut-plan line100.chf --layer-xml BkLayerPara.xml --hard-xml BkHardPara.xml -o f.txt
  .venv/bin/nexcut-mccd run-job f.txt --arm      # rehearse this against `serve --sim` first
  ```

  `nexcut-mccd run-job` does exactly what the deferred note described: `0x67 <- [1]`, then frames
  under reg 1015/1016 flow control, then `0x67 <- [2]`, and always `0x67 <- [3]` + `0x67 <- [1]`
  at the end. It is a dry run — laser records are stripped three times over (PORT-PLAN §8.2) —
  but **it does move the gantry**, so the step-1 preconditions of §4 apply in full and the
  operator keeps a hand on the E-stop. `job-status --json` shows frames sent, reg 1015/1016 and
  the queue low water, which is what O1 and C2 are read from.
* Two behaviours to expect, both added by the streaming review and both relevant here: the feeder
  waits for a status poll taken **after** `0x67 <- [1]` before its first fill, and refuses to start
  a program at all if nothing fitted (`no frame fits the card FIFO after 0x67 ← [1]`) — the card is
  never told to run an empty FIFO. And a frame reg 1015 has not acknowledged within 600 ms
  (`ipAdd.ini FifoTimeout`) is re-sent once and then the job is aborted; the vendor silently drops
  it instead. If the card re-runs a re-sent frame rather than ignoring it, that shows up here as a
  doubled move, so watch the first run closely.
* Running it natively also closes O6's FIFO half (a FIFO start without the licence exchange).
  Until it has been run once on the card, treat O6's FIFO half as open and prefer the vendor
  variant below, which is what the tick period was always going to come from.

**Vendor variant (tick period from the capture).** Follow the handover in §8, then in Mlaser:

1. draw or import **one horizontal 100 mm line** on a CO2 layer at 50 mm/s;
2. laser key OFF, gas closed;
3. use **Dry run (空走), not Start**. Dry run needs the licence dongle working under Wine (MainApp
   gates Start/frame/dry-run on the dog state, 11 O6).

Tool marks: Enter when you click Dry run, and Enter when the head stops at the end of the line.

**What to observe.**

* The head moves 100 mm in X.
* The Z follower may move: ZF records `103[1000,0]` / `109[1000,0]` are in the vendor CO2 stream
  (11 §5.4, C6). Watch the head.
* The high-air valve may be commanded (DO3), but the gas is closed.

**Expected results.** In the per-step capture census:

* FIFO frames and a 3000-item count;
* `tick_period_estimate` = (span of polls with reg 1019 = 1) ÷ (number of 3000 ticks);
* expected ≈ 250 µs, i.e. 100 mm at 50 mm/s = 2 s → ~8000 ticks. If it is ≈ 1 ms, the bus cycle
  and tick period differ (O1).

The frame ids in the capture versus reg 1015 in the replies check the "id = 1015 + 1" rule. Reg
1016 before and after the first frame checks the byte accounting (C2).

**Write down.** The speeds Mlaser showed or used (layer speed, dry-run speed setting) and the
stopwatch time.

### Step 9: Pause / Continue / Stop in the vendor tool

**Purpose.**

* N2: how the vendor resumes after Pause. There is no resume primitive in the card vocabulary, and
  the Continue path was not traced statically.
* Record the stop-manu frames.

**Vendor variant only.** Follow the handover in §8, then in Mlaser:

1. load a 200 × 100 mm rectangle, CO2 layer, 20 mm/s, laser key OFF;
2. Dry run; after ~3 s **Pause**; wait 3 s; **Continue**; wait 3 s; **Stop**.

Press Enter in the tool at each click, four marks in all.

**What to observe.**

* On Pause: does the head stop at once or finish a segment first? Does Z move?
* On Continue: does it resume where it stopped, back up (`MC.ResumeBackLength`), or restart the
  contour?
* Any dialog texts.

**Expected results.**

* Pause: `0x67 <- [3]` (Pause = Stop, 11 N1), then DO/PWM/gas off and `[101]`.
* Continue: new traffic. This is the discovery: a new `0x67 <- [1]` / frames / `[2]`, possibly
  starting with a go-to or ZF move to the break point.
* Stop: the same stop-manu sequence.

**Write down.** All three behaviours and any dialog text, verbatim.

### Step 10: Power-cycle, READ 1000/2 immediately

**Purpose.** N5: does the card answer exception 2 (ErrCode 502) while it boots, as the logs
suggest (08 §7)?

**Operator actions.**

1. Switch the **motion card / controller** power OFF, not the laptop. Use the controller-cabinet
   switch or the machine main switch; which switch powers only the card is UNVERIFIED. Enter.
2. Wait about 10 s, switch it ON again (the laser key stays OFF), Enter.
3. Wait while the tool watches the daemon reconnect.

**What the tool sends.** Nothing at first. The daemon notices the stale poll after 1 s (link lost,
disarm) and retries its start-up sequence every 2 s. **Its first request each time is
`READ 1000/2`**, so the capture shows the card's first answers to exactly the read 11 §7 asks for.
The tool records every daemon link and error change every 100 ms. After reconnection it reads
`1000/2` once more.

**Expected results.**

| Hypothesis | Confirmed | Other |
|---|---|---|
| exception 2 while not ready | `daemon_errors` contain `ErrCode:502`; the pcap shows `exception(502)` replies to `READ 1000/2` before the first `ok` | only timeouts (`10060`) until the first `ok`: the card is silent while booting and exception 2 means something else |

**Write down.** Boot time and panel messages. Afterwards the machine is un-homed again.

---

## 8. Vendor tool under Wine (for the vendor variants)

Source: docs/analysis/10 and PORT-PLAN §6 step 1. The prefix is already set up on this laptop.

**One-time preparation** (skip what is already done):

```sh
winetricks -q mfc42                               # CAD module needs MFC42.DLL (docs/analysis/10)
mkdir -p ~/.wine/drive_c/Mlaser
cp -a "/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52/." ~/.wine/drive_c/Mlaser/
```

* Run it **from `C:`**. It creates `\Technology\...` at the drive root, and from `Z:` it fails
  with "Failed to create Technology folder".
* Never run it from, or edit, the read-only package under `~/Documents`.
* In the **working copy** `~/.wine/drive_c/Mlaser/File/ipAdd.ini`, section `[Soft]`, set
  `EnableLog=1`. NCModule then also writes `Send Cmd:/Recv Cmd:` lines with timings to
  `~/.wine/drive_c/users/$USER/AppData/Local/NexCut/Log/<date>.log` (04 §9). The tool copies logs
  modified during the session into `m1-<date>/vendor-log/`.
* Optional privacy: blank `MonitorIP` in the same file, or switch Wi-Fi off during the vendor runs.
  Mlaser reports to `47.104.17.21:9001`, and the wired NIC has no gateway but Wi-Fi does.
* The licence dongle (USB HID `3689:8762`) must be plugged in and readable under Wine for
  Start / Dry run / Frame (11 O6). Jog, home and stop do not check it.

**Handover for each variant.** The tool guides you through it:

1. The tool disarms the daemon.
2. Stop `nexcut-mccd` with Ctrl-C in its terminal. **Mlaser and nexcut-mccd must never talk to the
   card at the same time.** The tool checks the socket. If the daemon still answers and is
   `CONNECTED`, it warns you and continues only on an explicit `y`, which is meant for simulator
   rehearsals only.
3. Keep tcpdump running. Start Mlaser: `cd ~/.wine/drive_c/Mlaser && wine MainApp.exe`, and wait
   until it shows the card as connected.
4. Do the actions. Press Enter in the tool at each marked moment.
5. Type your observations.
6. Close Mlaser completely, which also ends its polling. Restart
   `nexcut-mccd serve --card-ip 10.1.1.168 -v` and press Enter. The tool waits until the daemon is
   `CONNECTED` again.

---

## 9. After the session

1. When the tool asks, stop tcpdump with Ctrl-C. If the pcap is owned by root:
   `pkexec chown $USER:$USER ~/mlaser-captures/m1-<date>.pcap`.
2. The tool then writes:
   * `~/mlaser-captures/m1-<date>/report.json`: everything, including all reads, samples, motions,
     marks and observations;
   * `report.md`: the readable version with a hypothesis table per step;
   * `dissector-summary.txt`: `python -m nexcut.mcc.dissector --summary-only <pcap>`;
   * `pcap-decoded.txt`: the full decode with FIFO items;
   * `vendor-log/`: the Mlaser logs, when `EnableLog=1` was set.
   The pcap's size and SHA-256 are recorded in the report.
3. To re-run the analysis by hand:
   `.venv/bin/python -m nexcut.mcc.dissector --fifo ~/mlaser-captures/m1-<date>.pcap | less`.
4. Revert the network (§3) and switch the laser key back only when you intend to cut with the
   vendor software.
5. Share the report directory and the pcap. Both stay out of the public repository: the pcap
   contains your machine's configuration. The next development step turns them into
   `docs/analysis/11-capture-findings.md` and updates:
   * `mcc/registers.py`;
   * the simulator;
   * `[motion] position_counts_per_mm` / `position_scale_verified`;
   * `[mccd] watchdog_di_alarms`;
   * the limit-bit direction mapping.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Tool: "nexcut-mccd is not reachable" (exit 3) | Start the daemon (§5). With a non-default socket, pass `--socket` |
| Daemon never reaches `CONNECTED` | `ping 10.1.1.168`; check `ip -br addr` (§3). Make sure the machine PC is unplugged and Mlaser is closed. Check the card is powered. `nexcut-mccd status` shows `last_error` |
| `VERSION_REFUSED` | Card version < 20152. Stop and report the version |
| Motion refused `arming: E-stop latched` | Release the hardware E-stop, then `nexcut-mccd ack-estop` (or answer `y` to the tool's acknowledge prompt) |
| Motion refused `busy: axis status not refreshed…` | Normal right after a motion. Repeat the step with `--steps N` |
| Motion refused `refused: axis … not homed …` | The D1 rule: un-homed steps up to 10 mm, continuous jog at ≤ 20 mm/s |
| Motion refused `… (state DISARMED); arming ends with the connection that asked for it` | D9. Arm on the same connection: use `nexcut-mccd jog --arm` / `home --arm`, or let the session tool arm |
| Motion refused `arming: … needs MOTION_ARMED armed on this connection (another connection holds the arming)` | D9 again, the other half: something *is* armed, but not your connection — normally the session tool. Move from the tool, or stop it first. Stops and `disarm` are never refused this way |
| `run-job` refused `busy: axis status not refreshed since the last motion command` | The FIFO start waits for a 2000/50 poll newer than the last motion write (~90 ms). Wait a moment and run it again; the CLI does not retry yet |
| `run-job` fails `no frame fits the card FIFO after 0x67 ← [1] (reg 1016 = …)` | The card still holds a program, or reg 1016 is not what the port assumes. Send `stop`, check `job-status --json`, and record reg 1016 — this is one of the numbers step 8 exists to settle |
| Daemon start refused `another nexcut-mccd is already driving the card …` | D12. `ps -p <pid>` names the holder; stop it. Do not delete the lock file by hand |
| Motion outcome `aborted: daemon disarmed (…)` | The watchdog saw an alarm, a limit bit or comm loss. See `report.json` → `events` → `status` |
| tcpdump "Permission denied" on the file | Use `-Z "$USER"`, or write to `/tmp/…pcap` and pass `--pcap` |
| pkexec dialog does not appear | It needs the graphical session. Run tcpdump from a GNOME Terminal, not over SSH |
| Mlaser: dongle / "no key" | See the project notes on the udev rule for hidraw access; jog-only variants still work |
| Step 10 never reconnects | Increase `--power-cycle-timeout-s`. Check the daemon terminal. The link list in the report shows what happened |

---

## 11. Known limits of this build and UNVERIFIED items

* **Deferred natively:** step 4, because the IPC has no absolute move. Step 9 is vendor-only by
  nature. Step 8's FIFO stream is no longer deferred in software — `nexcut-mccd run-job` streams a
  planned frame file — but it has only been exercised against the simulator, so the vendor variant
  stays the primary way to get the tick period.
* **A link blip ends the arming.** `tools/m1_session.py` reconnects to the daemon lazily, and a
  reconnect is a *new* connection: it inherits neither the arming nor the right to move (D9, and the
  R12 amendment). If a step fails with an `arming:` refusal after a hiccup, re-run just that step
  with `--steps N` — the tool arms again at the next motion prompt. Nothing moves in the meantime,
  which is the intended direction of the failure.
* **Jog-while-moving (step 3)** is refused PC-side by the daemon (D8), so the card's exception 3
  shows only in the vendor capture.
* **Continuous jog speed (step 5)** is 20 mm/s natively; 50 mm/s only via the vendor variant.
* **Dissector labels.** `python -m nexcut.mcc.dissector` now uses the 11 §8 names: 0x65 sub-command
  1 = `stop`, 2 = `home`, 3 = `jog` (relative; bit 31 = absolute), 5 = `goto`, 101 = `zf-stop`, and
  each line carries the decoded meaning (`axis X distance +5.000 mm (relative), v 20.000 mm/s, …`).
  A capture dissected with an older build reads sub-command 1 as "home" and 3 as "move-axis" —
  re-run the dissector rather than trusting such a summary.
* **UNVERIFIED** (recorded as such in the tool):
  * that the card answers the 1-word marker read `READ 1000/1`;
  * `tcpdump -Z "$USER"` behaviour on this laptop;
  * which power switch cycles only the card;
  * the lead unit in AxisRW word 9 (µm or mm; the tool shows both candidates);
  * that the E-stop disables the servo drives (step 7 relies on it: watch the drives);
  * the vendor dry-run speed source (step 8, write down what Mlaser shows);
  * the tick-period estimate: 30 ms poll resolution divided by the tick count.
