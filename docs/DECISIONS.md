# Design decisions

One entry per decision: what was decided, why, where it is enforced, and how to change it.
Evidence references use the analysis section numbers (`11 §3.2` = `docs/analysis/11-static-findings.md` §3.2).
A decision that rests on a value not proven by evidence says so (UNVERIFIED).

---

## D1 — Un-homed jogs: 10 mm steps, or ≤ 20 mm/s under the deadman (F8)

**Decision.** While an axis is not homed (or its position is not trusted, D2), a relative jog on it is accepted only if
* `|distance| ≤ motion.unhomed_max_step_mm` (default **10 mm**) per command, or
* its speed is `≤ motion.unhomed_max_jog_speed_mm_s` (default **20 mm/s**). Such a jog is always either shorter than `speed × deadman` or runs under the 200 ms deadman lease, so it stops when the client stops refreshing.

Once the axis is homed and its position is trusted, the PC-side soft limits (`motion.soft_limit_x_mm` / `soft_limit_y_mm`, from `MAC/MAC_1.SoftLimitMaxLen`, 01 §2.2) are applied to every relative jog target; continuous jogs are clipped to the remaining distance.

**Why.** Before homing the PC has no position, so a relative jog cannot be checked against the soft limits (tests/test_mcc_safety_adversarial.py F8). Refusing all un-homed jogs is not an option: the operator must be able to move off a limit switch or away from a clamp before homing, and the vendor allows un-homed jogs without any bound (A1 §1 gates: Home/Stop/jog are ungated). The vendor continuous jog is a single relative ±4 000 mm command (11 §2 V1); a lost key-up would run the full stroke into the hard limit (11 N7). With the bound, the worst case before the deadman fires is 20 mm/s × 0.2 s = 4 mm plus the stop deceleration; a step is at most 10 mm.

**Step rate (amendment, safety review).** A step allowed *only* by the 10 mm branch (speed above `unhomed_max_jog_speed_mm_s`) also draws on a per-axis token bucket: capacity `unhomed_max_step_mm`, refilled at `unhomed_max_jog_speed_mm_s`. Without it a client chaining 10 mm steps at 200 mm/s (each accepted as soon as the axis is READY again) moved an un-homed axis at ~27 mm/s on the simulator and would reach ~50 mm/s on the card — the continuous-jog bound of this decision bypassed by repetition, with no deadman. With the bucket, un-homed motion above 20 mm/s is limited to one 10 mm burst plus 20 mm/s on average; slow jogs are not rate limited (they already average ≤ 20 mm/s and run under the deadman). Capacity and refill are UNVERIFIED port choices (the D1 values reused). Refusal text contains "D1 step rate". Tests: `tests/test_mccd_safety_review.py::test_r3_*`.

**Where.** `nexcut.mccd.gate.MccdGate.jog_rule_violation` / `step_rate_violation` (last gate before the wire, so any in-daemon caller is covered) and, with clearer messages, `MccDaemon._cmd_jog_continuous_start`. Tests: `test_f8_unhomed_continuous_jog_is_bounded_by_soft_limits`, `tests/test_mccd_gate.py`, `tests/test_mccd_daemon.py::test_unhomed_then_homed_jog_rules`.

**How to change.** `config.toml`:
```toml
[motion]
unhomed_max_step_mm = 10.0
unhomed_max_jog_speed_mm_s = 20.0
```
Both must be > 0; the speed must not exceed `max_jog_speed_mm_s`. The code defaults are `UNHOMED_MAX_STEP_MM` / `UNHOMED_MAX_JOG_SPEED_MM_S` in `src/nexcut/mccd/gate.py`.

---

## D2 — Soft limits from read-back positions only with a verified position scale

**Decision.** The daemon hands axis positions to the gate (enabling D1's "homed" branch) only when `motion.position_scale_verified = true`. Position = axis RO word 2 (`2000 + 10·slot + 2`) ÷ `motion.position_counts_per_mm[slot]` × K. Default: not verified, so homed axes keep the un-homed rule of D1.

**Why.** On the card, word 2 is named "pulse position" (A2 §3, name order LIKELY) and motor pulses are about 258.04 p/mm X / 257.99 p/mm Y (11 §5.3), while the simulator stores µm (counts/mm = 1000). A wrong scale would make the PC think the head is ~4× closer to the origin than it is, and the soft-limit check would let jogs run past the stroke. That is worse than the D1 bound.

**Where.** `MccDaemon._update_positions`. Test: `test_unhomed_then_homed_jog_rules` (simulator, verified = true), `test_homed_axis_uses_soft_limits`.

**How to change.** After 11 §7 steps 3/4 (jog 5 mm, read 2000+10·slot+2; absolute move) confirm the counts per mm and set
```toml
[motion]
position_counts_per_mm = [258.04, 257.99]   # example: use the measured values
position_scale_verified = true
```

---

## D3 — The default card address is the simulator; the real card only via `--card-ip`

**Decision.** `card.ip` defaults to `127.0.0.1` (the vendor `ipAdd.ini` has `10.1.1.168`). `nexcut-mccd run` refuses (exit 2) a non-loopback `card.ip` that comes only from `config.toml`; the operator must pass `--card-ip 10.1.1.168` on the command line. `--simulator` starts the in-process simulator on an ephemeral loopback port.

**Why.** PORT-PLAN §8: nothing in development may cause motion on real hardware unless the operator explicitly asked for it. A config file copied between machines, or left over from a bench session, must not silently point a test run at the machine.

**Where.** `nexcut.core.config.card_ip_needs_cli_confirmation`, `nexcut.mccd.cli._run`. Tests: `tests/test_mccd_config.py`.

**How to change.** Pass `--card-ip`. To make a config-file address sufficient, change `card_ip_needs_cli_confirmation` (not recommended before M5 sign-off).

---

## D4 — Transport arbitration: priority bus, short single-try reads, vendor ladder for writes

**Decision.** One UDP socket (as the vendor, 04 §3.1). Transactions are serialised by `PriorityBus` in the order URGENT (stop, E-stop, deadman/watchdog stops) > FAST (1000/36 poll, start-up) > COMMAND (IPC) > SLOW (2000/60001/50000/10000). Retry policies per queue:

| Queue | Policy | Source |
|---|---|---|
| fast poll 1000/36 | 1 send, 1 receive, `mccd.poll_timeout_ms` = 150 ms | port choice (UNVERIFIED) |
| slow reads, IPC `read_block` | 1 send, 1 receive, `mccd.slow_timeout_ms` = 150 ms | port choice (UNVERIFIED) |
| writes (0x65) | vendor idle ladder `MCTimeout/MCFifoTime/MCMaxRecvTime/MCSendInterval` (3 sends × 3 receives, 3.5 s) | 04 §1, transaction.py |

Periods: 1000/36 every `MCCore × MCUpdateFactor` = 30 ms (04 §1); 2000/50 90 ms, 60001/120 1 s, 50000/26 1 s, 10000/18 10 s (only while ZFType ≠ 0) — all UNVERIFIED periods, `[mccd]` keys.

**Why.** PORT-PLAN §3.4: the slow blocks must never block the fast poll, and the 1 s watchdog (PORT-PLAN §8.2) must see a dead link. With the vendor ladder on reads, one deaf 10000 read (08 §4.3) would hold the socket for 3.5 s and trip the watchdog. Opening a second socket for the slow queue was rejected: the card's behaviour with two source ports and two sequence counters is unknown. A holder is never pre-empted, so the fast poll waits at most one short read (tested: `test_deaf_slow_block_never_blocks_the_fast_poll`). Writes keep the vendor ladder because a lost reply to a stop must be retried.

**Where.** `nexcut.mccd.gate.PriorityBus`, `BusClient`; `MccDaemon.__init__`.

**How to change.** `[mccd] poll_timeout_ms`, `slow_timeout_ms`, `*_period_ms`; `[mc]` ladder keys. Both timeouts must stay below `watchdog_timeout_ms` (validated).

---

## D5 — The enforcement boundary is the `nexcut-mccd` process (F12)

**Decision.** Only the daemon process owns `McTransaction` and the safety gate. Clients (UI, CLI, tests) talk JSON lines over `$XDG_RUNTIME_DIR/nexcut/mccd.sock` (directory 0700, socket 0600, peer uid must equal the daemon uid). The IPC vocabulary is fixed (`nexcut.mccd.daemon.IPC_COMMANDS`): `ping, status, subscribe, unsubscribe, arm_motion, disarm, jog_step, jog_continuous_start, jog_refresh, jog_continuous_stop, home, stop, estop, ack_estop, read_block, set_do`. There is no raw write, transact or firmware command; `read_block` goes through the read allow-list (11 §3.2); `set_do` only addresses allow-listed ports and can only switch laser outputs **off**; there is no `arm_laser` in this phase.

**Why.** Inside one Python process nothing can hide `SafeMccClient.client` or `McTransaction.transact` (which can send func 0x26 or any register). A process boundary with a narrow protocol is the only enforcement that holds against a buggy or hostile UI (PORT-PLAN §2.3).

**Socket directory (amendment, safety review).** The daemon refuses a socket directory that is a symlink or belongs to another uid (before: it only tightened the mode of a directory it owned and silently used a foreign one — a pre-created `/tmp/nexcut-<uid>` would let that user replace the socket and swallow `stop`/`estop`). The client refuses a server whose `SO_PEERCRED` uid is not its own. Tests: `test_r6_*`.

**Where.** `src/nexcut/mccd/ipc.py`, `src/nexcut/mccd/daemon.py`. Tests: `test_f12_raw_transport_not_reachable_through_the_gate`, `tests/test_mccd_daemon.py::test_ipc_has_no_raw_register_path`, `tests/test_mccd_ipc.py`.

**How to change.** Add a command to `IPC_COMMANDS` and a `_cmd_<name>` handler; every card access in it must go through `self.gate`. Laser arming needs its own decision entry (confirmation UX, job token over IPC).

---

## D6 — Deadman and input sources

**Decision.**
* Continuous jogs (and any relative jog longer than `speed × deadman`) must be refreshed with `jog_refresh` within `mccd.deadman_timeout_ms` (200 ms, PORT-PLAN §8.2); otherwise the per-axis stop `[1, 1<<slot, 2, vd, 10·vd]` (A1 §4.1) is sent.
* Every IPC connection is an *input source*: when it disconnects, the jogs it started get the per-axis stop immediately.
* Generic hook for other inputs (pendant): `MccDaemon.register_input_source(name, silence_timeout_s)`; `touch()` on each report, `claim(slot)` for a running jog, `lost(reason)` on read error / hidraw removal. Silence longer than the timeout (pendant: `mccd.input_silence_timeout_ms` = 1040 ms, 11 N7 / A8 §7) stops its jogs.

**Why.** The card jog is one long relative command with no key-up on the pendant path (11 N7); the vendor leaves the axis running on unplug. The same failure exists for a crashed UI.

**Leases while disarmed (amendment, safety review).** `jog_refresh` returns `alive: false` and does not extend a lease while the machine is DISARMED or E-stop latched. Before, a client (or a TUI refresher thread) kept refreshing through a watchdog trip: when the trip's single stop attempt failed on a dead link (> 3.5 s), the lease never expired, the deadman never retried and the reconnect sent no stop — a card would have continued its 4000 mm jog. Now the lease expires and `service()` re-sends the per-axis stop until it gets through. A deadman stop answered with a card exception (`CardRefused`, e.g. exception 3 "already stopped", 08 §4.5 INFERENCE) counts as delivered; before, the lease was kept and the stop re-sent every 20 ms (59 stops in 1.5 s). Tests: `test_r1_*`.

**Where.** Leases: `SafeMccClient._register_lease/service` (mcc/safety.py), `MccdGate.jog_keepalive`; sources: `nexcut.mccd.daemon.InputSource`, `MccDaemon._watchdog_once`, `_on_close`. Tests: `test_deadman_expiry_sends_per_axis_stop`, `test_client_disconnect_stops_its_jog`, `test_input_source_silence_hook_stops_jog`.

**How to change.** `[mccd] deadman_timeout_ms`, `input_silence_timeout_ms`.

---

## D7 — Watchdog scope and recovery

**Decision.**
* Comm loss (1000/36 poll older than `mccd.watchdog_timeout_ms` = 1 s) is checked **always**, not only while a job runs: link → `LINK_LOST`, disarm, abort homing, and — if anything could be moving — send the minimal stop (`0x67 ← [3]` if the FIFO runs, then `[1,0x1F,2,vd,10·vd]`) from a helper thread. The full stop-manu sequence is not used there because each vector may run the whole retry ladder on a dead link.
* After the link returns, the start-up sequence (including `[9999,5,0,0]`) is re-run; the machine stays **DISARMED** until an operator arms it again. If the trip's stop did not reach the card (safety review), the start-up sends `0x67 ← [3]` (FIFO running) and `[1,0x1F,2,vd,10·vd]` right after the connect write and reports CONNECTED only once the card answered it (an exception reply counts as delivered).
* `ack_estop` needs a CONNECTED link and a status read younger than `watchdog_timeout_ms` (safety review): a block 1000 from before a link loss says nothing about the E-stop input now.
* Card alarm words (11 §4.7 rows 1–4, 8) are handled by the gate on every status read (stop sequence + disarm; bit 30 latches the E-stop until `ack_estop`, which is refused while the bit is still set).
* Axis status bits 0–5 (11 §4.7 row 5) trip stop + disarm **only while jogging** (deadman lease or machine state JOG), not during homing, where a limit/home switch may legitimately be active (UNVERIFIED card behaviour). Jogs toward an active limit bit are refused; jogs away from it are allowed.
* DI alarms through the NO/NC map (row 6) are configurable (`mccd.watchdog_di_alarms = [[11, true], [4, false]]`) but **off by default**: whether reg 1004 is the raw level or already inverted by the card (A2 §2.1) is UNVERIFIED.

**Open issue.** The gate refuses all non-ALWAYS writes while `alarm_1 ≠ 0` (`SafeMccClient.machine_fault`). If the card raises an alarm_1 axis bit while a hard limit is pressed, jogging off the limit is impossible through the port. Settle with 11 §7 step 7 (press each limit switch, read 1006 and 2000+10·slot) and decide then.

**Where.** `MccDaemon._watchdog_once`, `_trip`, `_startup`; `nexcut.mccd.status.watchdog_reasons`. Tests: `test_watchdog_*`, `test_ui_estop_latches_until_acknowledged`, `test_card_estop_bit_latches_and_ack_needs_release`.

**How to change.** `[mccd] watchdog_timeout_ms`, `watchdog_di_alarms`, `reconnect_interval_ms`.

---

## D8 — Card-side motion gates mirrored on the PC

**Decision.** `jog_step`, `jog_continuous_start` and `home` are accepted only when the derived machine state (11 §4.5) is READY, computed from a 2000/50 read taken **after** the last motion command the daemon sent. Homing runs one axis at a time (11 §2 V6), each waiting for the axis status to show busy/not-homed and then homed and idle (`mccd.home_timeout_ms`, UNVERIFIED 120 s).

**Why.** The card ignores jog/move unless its runStatus is 0 and home unless it is 0 or 1 (A1 §1), and answers exception 3 to commands while moving (08 §4.5). Refusing on the PC gives the client a clear `busy` error instead of a card exception, and the freshness rule prevents a stale READY from a read taken before the previous command.

**Concurrency (amendment, safety review).** The READY check and the send of one motion command run under `MccDaemon._motion_lock` (a concurrent motion request gets `busy`, it does not wait), and jogs are refused while a homing job exists. Before, two IPC connections sending `jog_step` at the same moment both passed the READY check on the same status read and both reached the card (19 of 20 races ended in card exception 3 on the simulator); a jog between two homed axes of one `home` job also slipped in. Tests: `test_r4_*`.

**Where.** `MccDaemon._require_ready`, `_one_motion`, `_send_motion`, `_home_run`.

**How to change.** Code only.

---

## D9 — Arming lifetime (OPEN)

**Open.** `MOTION_ARMED` is daemon state: it persists after the connection that armed closes (TUI `q`, CLI `arm` exits, crashed UI). Any later client of the same uid can then jog or home without its own `arm_motion` (strict xfail `test_r9_arming_does_not_outlive_the_arming_client`). The one-shot CLI flow `nexcut-mccd arm` → `nexcut-mccd jog` relies on this, and PORT-PLAN §8.2 does not define the lifetime.

**Options.** (a) disarm when the arming connection closes (CLI would need `jog --arm` in one process); (b) disarm after N s without a motion command (UNVERIFIED value); (c) keep, and have the TUI/m1 tool disarm on exit. Decide before M1 on the machine.

---

## D10 — Motion epoch: nothing queued before a stop is sent after it

**Decision.** `stop`, `estop`, `disarm`, watchdog trips and daemon shutdown call `MccdGate.invalidate_motion()` before queuing their stop. Every IPC request runs under `MccdGate.motion_guard(epoch at request start)`; a homing job carries the epoch of its `home` request. A motion write (policy not ALWAYS/CONNECT) whose guard epoch is older than the current one is refused **under the bus lock**, right before the transport write.

**Why.** `stop` keeps MOTION_ARMED (PORT-PLAN §8.2 only auto-disarms the laser), so the arming re-check under the bus did not catch a motion that was already past its checks. Found by forcing the interleaving: a homing thread (or a `jog_step` handler) pre-empted between its READY/cancel check and the wire, or waiting for the bus at COMMAND priority while the URGENT stop went first, sent `[2, 1, 0]` / the jog *after* the operator's stop — the axis started moving after STOP. Tests: `test_r2_*` (20× stress).

**Where.** `nexcut.mccd.gate.MccdGate._send`, `motion_guard`; `MccDaemon.handle`, `_cmd_stop/_cmd_estop/_cmd_disarm`, `_trip`, `_home_run`, `close`.

**How to change.** Code only. A command that must survive a stop needs its own decision.

---

## D11 — TUI letter bindings under Caps Lock (OPEN)

**Fixed.** Stop (`s`), E-stop (`e`) and disarm (`d`) keys accept both cases: with Caps Lock on they arrived uppercase and were "not bound" (test `test_r7_tui_stop_keys_work_with_caps_lock`).

**Open.** The vi-style continuous-jog keys are uppercase `H J K L`. With Caps Lock on, `h` (home menu) arrives as `H` and starts a continuous jog X− at 20 mm/s for the initial hold (0.7 s ≈ 14 mm) — motion from a menu key (strict xfail `test_r7_caps_lock_home_key_does_not_start_motion`). Curses cannot see the Caps Lock state. Options: drop the letter bindings (Shift+arrows only), move the home menu to a key whose other case is not a motion key, or require a second confirmation key for letter-started jogs.

---

## D12 — Single master per card (OPEN)

**Open.** Nothing prevents a second `nexcut-mccd` (different `--socket`) from driving the same card address: two gates, two E-stop latches, two deadmen, two sequence counters on one card (strict xfail `test_r11_second_daemon_on_same_card_is_refused`). Today "single master" is only an item of the `tools/m1_session.py` checklist. Options: an `flock` on `$XDG_RUNTIME_DIR/nexcut/card-<ip>-<port>.lock` (same user and machine only); detecting foreign traffic is not possible from the reply stream (UNVERIFIED whether the card answers two masters).
