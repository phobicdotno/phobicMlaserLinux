# Design decisions

One entry per decision: what was decided, why, where it is enforced, and how to change it.
Evidence references use the analysis section numbers (`11 §3.2` = `docs/analysis/11-static-findings.md` §3.2).
A decision that rests on a value not proven by evidence says so (UNVERIFIED).

---

## Index

| Id | Decision | Status |
|---|---|---|
| D1 | Un-homed jogs: 10 mm steps, or ≤ 20 mm/s under the deadman | decided |
| D2 | Soft limits from read-back positions only with a verified position scale | decided; `position_scale_verified` still `false` until the bench measurement |
| D3 | Default card address is the simulator; the real card only via `--card-ip` | decided |
| D4 | Transport arbitration: priority bus, short reads, vendor ladder for writes | decided |
| D5 | The enforcement boundary is the `nexcut-mccd` process | decided; amended for job streaming, the socket directory and safety review R17 |
| D6 | Deadman and input sources | decided; amended (no lease refresh while disarmed) |
| D7 | Watchdog scope and recovery | decided, **with one open issue**: jogging off a pressed hard limit while `alarm_1 ≠ 0` (11 §7 step 7) |
| D8 | Card-side motion gates mirrored on the PC | decided; amended (one motion lock) |
| D9 | Arming is owned by the IPC connection that asked for it | **decided 2026-09-16**; amended twice the same day — safety review R12 (the owner also owns the right to move) and the UI review (re-arming after a reconnect needs its own operator confirmation) |
| D10 | Motion epoch: nothing queued before a stop is sent after it | decided |
| D11 | No letter key starts motion; the key table is data | **decided 2026-09-16**; re-reviewed unchanged (exhaustive key × mode × case matrix) |
| D12 | One master per card: an advisory lock keyed by the card address | **decided 2026-09-16**; amended the same day by safety review R13/R14/R15 |
| D13 | Laser arming (`LASER_ARMED`): what `arm_laser` quotes, proves and loses | **proposed 2026-09-16 — needs the owner's sign-off before implementation**; no M5 code exists or may exist until then |
| D14 | `.chf` v2-v4 reserved lines are kept verbatim in a model slot; nothing is rejected | **decided 2026-09-16** (closes strict xfail X6) |
| D15 | A parameter file changes only where the operator changed it | **decided 2026-09-16** by the UI review (U1-U4, W1-W3) |

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

**Decision.** Only the daemon process owns `McTransaction` and the safety gate. Clients (UI, CLI, tests) talk JSON lines over `$XDG_RUNTIME_DIR/nexcut/mccd.sock` (directory 0700, socket 0600, peer uid must equal the daemon uid). The IPC vocabulary is fixed (`nexcut.mccd.daemon.IPC_COMMANDS`): `ping, status, subscribe, unsubscribe, arm_motion, disarm, jog_step, jog_continuous_start, jog_refresh, jog_continuous_stop, home, stop, estop, ack_estop, read_block, set_do, load_job, start_job, pause_job, stop_job, job_status`. There is no raw write, transact or firmware command; `read_block` goes through the read allow-list (11 §3.2); `set_do` only addresses allow-listed ports and can only switch laser outputs **off**; there is no `arm_laser` in this phase.

**Job streaming (amendment, task 8).** The five `*_job` commands stream a job planned by `nexcut.plan` into the card FIFO (`src/nexcut/mccd/feeder.py`). They carry no register address and no item words: `load_job` takes the path of a planned frame file (or, in process, an iterator of packed frames), every frame is validated against the item grammar of 11 §5.2 and neutralised by `strip_laser_records` as it is read, and the gate strips it again on the way out. `load_job` needs `MOTION_ARMED` because the job token comes from `ArmingStateMachine.begin_job`, and it is refused outright while `LASER_ARMED`: this phase runs dry runs only. `stop` / `estop` / `disarm` and every watchdog trip end the job; `stop_job` is the clean one (`0x67 <- [3]`, `0x67 <- [1]`, token dropped, machine still armed). A feeder reaches its final state *before* that closing pair is on the wire, so `load_job` waits for the previous feeder's thread (`JobFeeder.closed`, up to `CLEAN_STOP_JOIN_S` = 5 s) and otherwise refuses with "the previous job is still stopping": without it the old feeder's clean stop would clear the new job's queue. Tests: `tests/test_mccd_job.py`, `tests/test_perf_streaming.py`.

**A stop on a job that never started (amendment, safety review R17).** `JobFeeder.request_stop` now finishes a job whose thread was never created: state `STOPPED`, `closed` set, job token returned. Before, such a job stayed `LOADED` with a latent stop flag, and two things followed. `start_job` after `stop_job` cleared the FIFO, streamed the frames and sent `0x67 ← [2]` before the streaming loop looked at the flag — the card saw `clear, start, stop, clear` and briefly ran a program the operator had already stopped (PORT-PLAN §8.2 says a stop ends motion, not "starts it once more"). And because the job never reached a final state, every later `load_job` was refused with `a job is already loaded (LOADED)` for the life of the daemon, with no IPC way out. `_stream` also re-checks the stop flag on entry and immediately before `0x67 ← [2]`, and the feeder now carries the motion epoch (D10) of its `start_job` request instead of reading the current one on the streaming thread. Tests: `test_r17_*`.

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

## D9 — Arming is owned by the IPC connection that asked for it

**Status: decided (2026-09-16) and enforced.** This entry was the open question "who owns the arming?"; option (a) was taken, implemented, and then amended the same day by safety review R12 (see below), which found that the first implementation owned the *lifetime* of arming but not the right to move. The strict xfail X3 is an ordinary passing test now.

**Decision.** Option (a) of the open entry. `arm_motion` records the connection that sent it (`MccDaemon._arm_owner`, returned as `arm_owner` in the reply; a later `arm_motion` on another connection transfers ownership). When that connection closes — clean close, `q` in the TUI, a crash, a socket error — the daemon runs the same sequence as an operator `disarm`: invalidate the motion epoch (D10), cancel a homing job, disarm, and — if anything could still be moving — send the stop sequence. The jogs that connection itself started were already stopped per axis one step earlier by the input-source rule (D6); the stop sequence covers what *another* client started while the machine was armed. `arm_motion` without a connection is refused.

A motion command refused because nothing armed this connection says so: the refusal text names D9 and `nexcut-mccd jog --arm`.

**Why.** PORT-PLAN §8.2 requires an explicit operator arming step before anything moves. With arming as daemon state it was not the *operator* who armed but whoever had armed last, possibly hours earlier in a process that no longer exists: a stray `nexcut-mccd jog` (or any buggy client of the same uid) then moved the machine with no arming step of its own. Refusing that is the whole point of the arming state. The strict xfail `test_r9_arming_does_not_outlive_the_arming_client` is now a passing test, and it kills the arming client with SIGKILL, not a clean close.

Option (b), an idle timeout, was rejected: the timeout value would be UNVERIFIED, and it still leaves a window in which a foreign client moves. Option (c), "have the TUI and the m1 tool disarm on exit", was rejected because it protects only against tidy clients — the case that matters is the untidy one.

**Cost, and how the one-shot CLI keeps working.** `nexcut-mccd arm` followed by `nexcut-mccd jog` no longer works (the first process exits, so the daemon disarms; `arm` prints that on stderr). `nexcut-mccd jog --arm` and `home --arm` arm, move and disarm inside one connection instead. `--arm` implies `--wait`, because the disarm on exit stops whatever is still moving. `tools/m1_session.py` is unaffected: it arms and moves on one long-lived `DaemonLink` connection.

**The owner also owns motion (amendment, safety review R12).** `jog_step`, `jog_continuous_start`, `home`, `load_job` and `start_job` are accepted only on the connection that is the current `_arm_owner`; any other connection gets `arming: … needs MOTION_ARMED armed on this connection … (docs/DECISIONS.md D9)`. The first version of D9 tied only the *lifetime* of arming to a connection, so while the arming client was alive **any** other client of the same uid still jogged, homed and streamed jobs without an arming step of its own — precisely the stray-`nexcut-mccd jog` case this entry calls the whole point of the arming state, and a window in which a client that connected after `arm_motion` inherited an armed machine. `arm_motion` on another connection still transfers ownership, so a deliberate hand-over works. Stops are deliberately **not** owned: `stop`, `estop`, `disarm`, `jog_continuous_stop`, `pause_job` and `stop_job` are accepted from any connection, so a stuck arming client can never lock an operator out. `jog_refresh` is not owned either — the TUI refreshes the deadman on its own connection (`IpcBackend._refresh_loop`), and a lease cannot be refreshed while the machine is DISARMED (D6 amendment), so the arming owner's death still ends it. In-process callers (`conn is None`, e.g. `load_job_frames`) are inside the D5 boundary and are not checked.

**Re-arming after a reconnect is its own consent (amendment, 2026-09-16, UI/safety review M1).**
A client that reconnects gets a *new* connection, which owns neither the arming nor the right to
move — and by the time it reconnects the daemon has already run the full `_release_arming` path:
the machine was **stopped and disarmed**, and the axis need not be where the operator's last
instruction left it. `tools/m1_session.py` used to restore the arming and send the queued motion on
the strength of a printed line. It now asks an explicit `y/N` (`op.confirm("s<n>.rearm.<cmd>")`,
defaulting to **No**) before re-arming; a `no` records `rearm_declined`, raises `ArmingLost` and
sends nothing. The general rule this fixes: **consent given for a motion is consent for the machine
state that existed when it was given.** Any future client that re-establishes arming on the
operator's behalf has to re-ask, for the same reason. Tests:
`tests/test_ui_safety_review.py::test_a_reconnect_rearm_asks_the_operator_and_sends_nothing_on_no`;
the 14 existing `tests/test_m1_session.py` tests are unchanged (the scripted operator answers `y`).

**Residual (documented, not fixed).** A client that is alive but stuck — SIGSTOPped, hung in a syscall, or half-closed on the read side only — never produces a close event, so it keeps the machine armed. D9 rejected an idle timeout, and with the amendment above a stuck owner can no longer be *used* by anything else: nothing moves, and any connection can still stop and disarm. A half close (`shutdown(SHUT_WR)`) does disarm: the daemon's reader sees EOF. Tests: `test_r19_*`.

**Where.** `MccDaemon._cmd_arm_motion`, `_require_arm_owner`, `_release_arming`, `_disarm_and_stop`, `_arming_hint`; `nexcut.mccd.cli._armed`, `_jog`, `_home`, `_one_shot`. Tests: `tests/test_mccd_safety_review.py::test_r9_*`, `test_r12_*`, `test_r19_*`, `tests/test_mccd_cli.py::test_one_shot_arm_does_not_survive_its_process`.

**How to change.** Code only. To let arming survive its client (not recommended), drop the `_release_arming` call in `MccDaemon._on_close`; the CLI `--arm` flag keeps working either way.

---

## D10 — Motion epoch: nothing queued before a stop is sent after it

**Decision.** `stop`, `estop`, `disarm`, watchdog trips and daemon shutdown call `MccdGate.invalidate_motion()` before queuing their stop. Every IPC request runs under `MccdGate.motion_guard(epoch at request start)`; a homing job carries the epoch of its `home` request. A motion write (policy not ALWAYS/CONNECT) whose guard epoch is older than the current one is refused **under the bus lock**, right before the transport write.

**Why.** `stop` keeps MOTION_ARMED (PORT-PLAN §8.2 only auto-disarms the laser), so the arming re-check under the bus did not catch a motion that was already past its checks. Found by forcing the interleaving: a homing thread (or a `jog_step` handler) pre-empted between its READY/cancel check and the wire, or waiting for the bus at COMMAND priority while the URGENT stop went first, sent `[2, 1, 0]` / the jog *after* the operator's stop — the axis started moving after STOP. Tests: `test_r2_*` (20× stress).

**Where.** `nexcut.mccd.gate.MccdGate._send`, `motion_guard`; `MccDaemon.handle`, `_cmd_stop/_cmd_estop/_cmd_disarm`, `_trip`, `_home_run`, `close`.

**How to change.** Code only. A command that must survive a stop needs its own decision.

---

## D11 — No letter key starts motion; the key table is data

**Status: decided (2026-09-16) and enforced.** X4 is an ordinary passing test. Re-reviewed on the same day against an exhaustive matrix (112 keys × 4 modes × both cases, plus every `CSI 1;<mod>` escape sequence, `Alt+<letter>` and every control code 1–31) and left **unchanged**: motion in normal mode is exactly `{UP, DOWN, LEFT, RIGHT, PGUP, PGDN, S_UP, S_DOWN, S_LEFT, S_RIGHT}`, in the home menu exactly `{x, X, y, Y, b, B}`, and nothing at all in the read prompt or the help overlay (`tests/test_mccd_safety_review.py::test_r18_*`).

**Decision.** The TUI key map is one table, `nexcut.mccd.tui.KEY_BINDINGS` (`KeyBinding(keys, action, label, what, mode, motion, arg)`), and the controller does nothing that is not a row of it. Two rules hold over the whole table:

1. **Motion keys are named keys only.** In normal mode a jog is started by the arrow keys (step), PgUp/PgDn (W step) and Shift+arrow (continuous). The vi-style `H J K L` continuous-jog keys are **removed**. Homing keeps letter keys, but only inside the `home` menu that `h` opens — a two-key confirmation with a prompt on screen, never one keystroke.
2. **Every printable key is bound in both cases**, so Caps Lock or a stuck Shift cannot change what a key does.

`?` shows the whole table as a help overlay built from it (`tui.help_lines`, any key returns); the two footer lines of the normal screen name the same keys in short form.

Keys after the change: `m` arm, `d` disarm, `a` acknowledge E-stop (was `A`), `h` home menu, `r` read block, `?` help, `q` quit, `[` `]` step size, space/`s` STOP, Esc/`e` E-STOP — each in both cases. Home menu: `x`, `y`, and `b` = X then Y (was `a`, which now collides with nothing) and only with `--allow-home-all`. Acknowledging the E-stop is a normal-mode key now, not a global one, so the home menu's letters cannot be shadowed by it.

**Why.** Curses cannot see the Caps Lock state, so binding `H` is really binding `h` as well. With Caps Lock on, the home-menu key `h` arrived as `H` and started a continuous jog X− at 20 mm/s for the initial hold (0.7 s ≈ 14 mm): motion from a menu key, PORT-PLAN §8.2's "nothing moves without an explicit operator step" broken by a stuck modifier. The same trap was latent in `l`/`j`/`k` (unbound lowercase, motion uppercase) and, less dangerously, in `m`/`M` and `A`/`a`. Removing the letter jogs costs the terminals that send no Shift+arrow; both xterm (`CSI 1;2A`) and rxvt (`CSI a`) do, and `tokenize` decodes both.

The rules are enforced by tests over the table itself, not key by key: `test_r7_key_table_has_no_case_or_motion_trap` (each alphabetic key resolves to the same binding as its swapped case; every `motion=True` row outside the home menu uses only names from `KEY_NAMES`) and `test_r7_no_printable_key_starts_motion_in_normal_mode` (all 95 printable characters, fed to a fresh controller, send no `jog*`/`home`).

**Where.** `src/nexcut/mccd/tui.py`: `KEY_BINDINGS`, `BINDINGS_BY_MODE`, `binding_for`, `help_lines`, `TuiController.handle_key` / `_normal_key` / `_home_menu_key`. Tests: `tests/test_mccd_safety_review.py::test_r7_*`, `tests/test_mccd_cli_tui.py`.

**How to change.** Add a row to `KEY_BINDINGS`; a printable key must list both cases, and a `motion=True` row outside `MODE_HOME` must use key *names* (`S_UP`, `PGUP`, …), or the two tests above fail.

---

## D12 — One master per card: an advisory lock keyed by the card address

**Status: decided (2026-09-16) and enforced.** X5 is an ordinary passing test. Amended the same day by safety review R13/R14/R15 — the first implementation could be defeated by deleting the lock file, did not check the directory or file ownership, and keyed on the raw address string. What the lock still cannot see is listed under "Limits" and pinned by a test, so it is not mistaken for coverage.

**Decision.** `MccDaemon.start()` takes an exclusive, non-blocking `flock` on `$XDG_RUNTIME_DIR/nexcut/card-<ip>-<port>.lock` (`/tmp/nexcut-<uid>/…` without `XDG_RUNTIME_DIR`, following `default_socket_path`) and holds it until `close()`. The file contains the holder's pid; a second daemon is refused before it sends anything, with `another nexcut-mccd is already driving the card at <ip>:<port> (pid N); only one master per card (docs/DECISIONS.md D12, lock <path>)`. The IPC socket path is the second interlock, unchanged: `IpcServer._prepare_path` refuses a socket another daemon already answers on (and removes a stale one). The lock is taken **first**, so a second daemon with a different `--socket` never reaches the card.

Key = address, not socket: `--card-ip 10.1.1.168` twice collides, `--sim` twice does not (each simulator gets its own ephemeral port).

**Stale locks.** A holder killed with SIGKILL leaves the file but not the lock: the kernel drops an `flock` when the last descriptor closes, so the next daemon acquires it and overwrites the pid. Nothing has to time out or be cleaned up by hand. On a clean release the file is unlinked while the lock is still held, and an acquirer that opened the old inode in that window notices (it compares `fstat(fd)` with `stat(path)`) and retries, so unlinking cannot hand the same card to two daemons.

**Why.** Two daemons on one card means two safety gates, two E-stop latches, two deadmen and two sequence counters: each would see the other's replies as unexpected, and a stop from one would be invisible to the other's state. Before this, "single master" was only a checklist line in `tools/m1_session.py` (bench prerequisite 6).

**The lock is re-asserted, not just taken (amendment, safety review R13).** An `flock` lives on the *inode*, not on the name, so a lock file deleted or replaced while the holder runs stops protecting anything: the next daemon creates a new inode, locks that one and drives the same card. This needs no attacker — the refusal message names the lock path, which invites "just delete the stale lock", and in the `/tmp/nexcut-<uid>` fallback any local user can replace the file. `CardLock.reassert()` compares `stat(path)` with `fstat(fd)` and the daemon's watchdog calls it every `_CARD_LOCK_RECHECK_S` = 1 s (UNVERIFIED port choice): a removed or replaced file is **re-taken**, so a second daemon is refused again; if another process got the new inode first there are two masters, and this daemon stops being one — `MccDaemon.card_lock_lost` is set, the watchdog trip disarms and stops, and `arm_motion` is refused until the daemon is restarted. Tests: `test_r13_*`.

**Directory and file ownership (amendment, safety review R14).** The lock directory now gets the same check as the IPC socket directory (D5 amendment): `ipc.ensure_private_dir` refuses a symlink or a directory owned by another uid, and the lock file is opened `O_NOFOLLOW` and refused unless this uid owns it. Before, `CardLock` created the directory with `mkdir(mode=0o700)` and used whatever was already there. Test: `test_r14_*`.

**Key normalisation (amendment, safety review R15).** The key is the *normalised* address (`_card_key`): `ipaddress.ip_address` canonical form, IPv4-mapped IPv6 folded to IPv4, so `10.1.1.168`, `::ffff:10.1.1.168` and `::1` / `0:0:0:0:0:0:0:1` collide. Test: `test_r15_*`.

**Limits.** An advisory lock binds only this user on this host. It says nothing about Mlaser under Wine, the machine's own control PC, or a second workstation on the card network — those stay operator discipline (M1-BENCH-SESSION §4 prerequisite 6). Detecting a foreign master from the reply stream is not possible; whether the card even answers two masters is UNVERIFIED. **Undetectable by the lock, and left that way on purpose:** a host *name* and its address (`--card-ip laser.local` vs `--card-ip 10.1.1.168`) are two different keys, because resolving a name in `MccDaemon.start()` would put a DNS lookup in front of the card lock; likewise a card reachable on two addresses (second NIC, NAT, a router alias). `test_r15_the_card_lock_key_normalises_the_address` pins this as a known gap so it is not mistaken for coverage.

**Where.** `nexcut.mccd.daemon.card_lock_path`, `CardLock`, `MccDaemon.start` / `close`. Tests: `tests/test_mccd_safety_review.py::test_r11_*` (second daemon, same socket path, SIGKILLed holder), `test_r13_*` (re-assert, a lost lock disarms, a lost lock is released without deleting the winner's file), `test_r14_*` (directory and file ownership, 0700/0600), `test_r15_*` (address normalisation and the documented gaps).

**How to change.** The path comes from `card_lock_path()`; set `XDG_RUNTIME_DIR` to move it. There is no flag to disable the lock: a second master is never wanted.

---

## D13 — Laser arming (`LASER_ARMED`): what `arm_laser` must quote, prove and lose

**Status: proposed — needs the owner's sign-off before implementation.** Nothing in this entry is
implemented, and nothing in this phase may implement it. It is written *before* any M5 code exists
because that is the point of the D-series: `LASER_ARMED` is the one state in the port that can put
energy into material, and the shape of the request that reaches it should be argued on paper, not
discovered while wiring a button. Until the owner signs this off, `mcc/safety.py`'s refusal stays
exactly as it is (see "The placeholder, and the exact lines that change").

**Scope.** This entry settles the *grant*: the IPC request, what the daemon verifies, what revokes
it, what is written to the log, and what software cannot do at all. It does **not** settle the
in-stream laser records themselves (11 §5.2: tick duty in the low half of the second `3000` word,
`9999[2, 1<<p, v<<p]` DO for the DO9 CO2 gate, `9999[3|0x11, freq, duty]` PWM-set, `9999[4, ch, v]`
DA) — those are already classified by `mcc/safety.strip_laser_records` and by the write allow-list
(11 §3.1 ALLOW, §3.3 DENY); D13 only decides *when* a frame is allowed to keep them.

---

### 1. The request

```json
{"cmd": "arm_laser",
 "job_token": "<the token load_job returned>",
 "layers":    [1, 3, 9],
 "material":  {"kind": "technology", "laser": "co2",
               "preset": "Carbon 10.0mm  4.0D F+12 O2",
               "sha256": "<of the technology XML as the port serialises it>"},
 "confirm":   "ARM LASER <job name>",
 "ttl_s":     300}
```

Every field is mandatory, and every field is a *quote of something the operator can see on screen*
— the request carries no value of its own, so a client cannot arm a machine into a configuration
the operator never looked at.

* **`job_token`** — the token `ArmingStateMachine.begin_job()` minted for the loaded job (the same
  token `load_job` returns today and `arm_laser(job_token=…)` already demands). A token exists only
  while a job is loaded, so "arm the laser with no job" is unrepresentable rather than refused.
* **`layers`** — the 1-based layer slots (02 §4) the job will actually cut, sorted, no duplicates.
  The daemon compares them with the layer set of the loaded job and refuses a mismatch. This is
  what makes a laser arming specific to *this* job: re-using a token after the operator changed
  which layers are enabled is a different job in every way that matters to the material.
* **`material`** — the technology preset the layers were loaded from (`LayerFileName`, 02 §6.1)
  plus the sha256 of that preset as `io.params.serialize_technology` writes it. The daemon
  re-serialises the layer slots it is about to cut and compares. A preset edited between the dry
  run and the arming is a different cut; the operator re-confirms it.
* **`confirm`** — a free-text string the operator *types*, not a boolean. It must equal
  `f"ARM LASER {job_name}"` (case-sensitive, the job name as `job_status` reports it). A boolean
  `confirmed: true` is one flipped flag in a buggy client; a string that has to name the job cannot
  be produced by a client that does not know which job is loaded, and cannot be produced at all by
  a retry loop replaying an older request against a newer job. `mcc/safety.ArmingStateMachine.
  arm_laser(confirmed=…)` keeps its boolean parameter — the daemon is what turns the typed string
  into that boolean, because `safety.py` must not grow a notion of "job name".
* **`ttl_s`** — how long the operator wants the arming to last, 1 ≤ ttl ≤ `laser.max_arm_ttl_s`
  (proposed default **600 s**, UNVERIFIED port choice). See §3 (idle timeout).

The reply is `{"arm_state": "LASER_ARMED", "job_token": …, "expires_at": <monotonic>, "layers": …,
"material_sha256": …}` — the grant restates what it granted, so the UI can display it and a log
reader can see it without reconstructing the request.

**`arm_laser` is added to `IPC_COMMANDS`** (it is absent today, D5) and is subject to D9 **and** its
R12 amendment: it is accepted only on the connection that is `_arm_owner`. The laser cannot be
armed by a connection that did not arm the motion, and — per D9's ownership transfer rule — a later
`arm_motion` on another connection takes the machine away from it (and, by §3 below, disarms the
laser on the way).

A companion `disarm_laser` (`LASER_ARMED → MOTION_ARMED`, drops the token) is accepted from **any**
connection, exactly like `stop`/`estop`/`disarm` (D9): revoking energy is never owned.

---

### 2. What the daemon verifies before granting it

In this order, each refusal naming the check and this entry:

1. **Not E-stop latched** and `card_lock_lost is None` (D12/R13) — the same two gates `arm_motion`
   has. A daemon that has lost its card lock may not arm anything.
2. **State is `MOTION_ARMED`** and this connection owns it (D9/R12).
3. **Homed, on every axis the job moves**, and **`position_scale_verified` is true** (D2). D2 already
   refuses homed soft limits without a verified scale; a laser that follows a path whose millimetres
   are not known to be millimetres is the same defect with a beam attached. This one check means M5
   cannot start before §5 task 3 of STATUS (the 100 mm ruler measurement) has been done — which is
   the intended ordering, not an accident.
4. **A job is loaded, its token matches, and it is *dry-run-clean*.** "Dry-run-clean" is defined
   here as: the loaded job has been streamed to completion at least once with
   `strip_laser_records` active, on this daemon, since it was loaded, and that run ended `DONE`
   with 0 starvation events and 0 aborted frames. The feeder records this as
   `JobFeeder.dry_run_result`; the daemon keeps the last one per job token. No dry run, a failed
   dry run, or a dry run of a *different* token → refused. (INFERENCE, port policy: the vendor has
   no such rule; 空走 is a mode the operator chooses, not a precondition. The port makes it a
   precondition because a first cut is the worst place to discover a path that leaves the bed or a
   stream that starves.)
5. **Soft limits**: the planned path is inside `SoftLimitMaxLen` (D2/PORT-PLAN §8.2). Already
   enforced for streaming; re-checked here so the refusal names the laser.
6. **No alarm**: alarm words 1006/1007 are 0 and the last status poll is younger than the watchdog
   period (11 §4; PORT-PLAN §8.2). A stale poll is treated as an alarm.
7. **Gas / assist state**: the assist-gas DO for every gas type the job's layers request
   (`CutGasType`, `DrillGasType{k}`, 02 §3.3/§3.6, ports from `MGP`/`DO`) is *available* — the port
   is configured (non-zero, 11 §3.1 DO allow-list) and the valve is not already commanded on by
   something else. The port does **not** measure pressure: there is no pressure DI on this machine
   in `BkHardPara.xml`, so "gas is flowing" is not observable (UNVERIFIED, and it stays that way
   until a capture says otherwise). What the check buys is that a cut cannot be armed against a
   gas configuration that has no output port at all.
8. **The hardware interlock is still the primary safeguard.** PORT-PLAN §8.2: the CO2 tube PSU
   enable / key switch (fibre: emission enable + shutter) is wired in hardware and software cannot
   override a missing PSU enable. The daemon does not check it, cannot check it, and must never
   present `LASER_ARMED` as "the laser is enabled" — it means "this daemon will stop stripping
   laser records from *this* job's frames". Every UI string for this state says so.

Refusals use the existing `IpcError` codes: `arming` for 1–3, `refused` for 4–7.

---

### 3. Auto-disarm: everything that drops `LASER_ARMED` back to `MOTION_ARMED`

`LASER_ARMED` is a lease, never a mode. It ends — with no operator action and no acknowledgement —
on **every** one of:

| Event | Where it already exists |
|---|---|
| job end (`DONE`, `ABORTED`, or any final feeder state) | `ArmingStateMachine.end_job` |
| `stop`, `stop_job` | `on_stop` (PORT-PLAN §8.2: "auto-disarms on stop") |
| **`pause_job`** | *new*: pause is a stop with an intent to resume. Resuming re-arms, with a new `confirm` string |
| `estop` (UI or alarm 1006 bit 30) | `estop` → `disarm` + latch |
| any card alarm ≠ 0 | `on_alarm` |
| comm loss (status poll failing > 1 s) | `on_comm_loss` |
| watchdog trip, card-lock loss (D12/R13) | `_trip`, `MccDaemon.card_lock_lost` |
| the arming connection closing — clean close, crash, SIGKILL, half close (**D9**) | `_release_arming` → `_disarm_and_stop` |
| `arm_motion` on another connection (D9 ownership transfer) | *new*: the transfer disarms the laser first |
| daemon shutdown | `MccDaemon.close` |
| **idle timeout**: `ttl_s` elapsed, or 30 s with no frame accepted by the card while a job is streaming | *new* |

**The idle timeout is the one place D13 departs from D9.** D9 rejected an idle timeout for *motion*
arming because the timeout value would be UNVERIFIED and it still leaves a window in which a foreign
client moves. Both objections are weaker here and one of them inverts: the value being a port choice
matters less when expiry is the *safe* direction (the laser stops being armable, nothing starts), and
the hazard being timed out is qualitatively different — a motion arming left behind by a frozen
client is a machine that will not move until someone asks it to, while a laser arming left behind is
a machine one stray `start_job` away from cutting. `ttl_s` is chosen by the operator per arming and
is bounded by `laser.max_arm_ttl_s`; the 30 s no-progress rule is a second, shorter fuse for the case
where the stream stalls with the lease held. Both values are **UNVERIFIED port choices**.

Disarming the laser never disarms the motion and never stops an axis by itself: the events above
that *should* stop the machine (stop, estop, alarm, comm loss, close) already do, through their own
paths. A bare laser disarm only takes the beam away.

---

### 4. What is logged

Every grant, refusal and revocation is one structured record in the daemon log **and** in
`SafeMccClient.write_log`, at WARNING:

* on grant: `arm_laser`, connection id, job token, job name, layer list, material preset name and
  sha256, the `confirm` string as typed, `ttl_s`, the expiry, and the dry-run result being relied on
  (its end state, frame count and starvation count);
* on refusal: the same fields plus the number of the check in §2 that failed;
* on revocation: which row of §3 fired, the wall-clock and monotonic time, how long the lease had
  run, and how many FIFO frames carried a laser record while it was held.

The last item is the one number an incident review needs and the only one nothing counts today, so
it is part of the decision rather than an implementation detail: `SafeMccClient` already returns
`laser_items` from `strip_laser_records(…, laser_ok=True)` on the `LASER_ARMED` path
(`mcc/safety.py`, the `0x66` branch), so it is a counter, not a new mechanism. The audit-log size
item in STATUS §5 task 10 has to be settled first — a per-frame 298-word record at tens of MB per
job is not a log one can keep for a review.

Nothing in the log is a secret, and the `confirm` string is operator-typed text about this machine,
so it is logged verbatim.

---

### 5. What stays impossible in software, on purpose

* **The PSU key.** The CO2 tube's PSU enable / key switch (fibre: emission enable and the shutter)
  is not reachable from any register in 11 §3.1, is not on any DO this machine wires, and is not
  something `arm_laser` can assert. `LASER_ARMED` with the key off is a laser that does not fire.
  This is the safeguard the port deliberately does not replicate, and every M5 runbook step starts
  with it off.
* **The DENY list does not move.** 11 §3.3 stays exactly as it is under `LASER_ARMED`: 150/151,
  59500–59599, 59600+ writes, 11000+2k, `100 ← 9999`, 5000–5012, 200/201, the unnamed `0x65`
  sub-commands and func 0x26 are refused in every arming state. `LASER_ARMED` widens **one** thing:
  `9999[2, 1<<p, v<<p]` for `p` in the laser DO set and the in-stream duty/PWM/DA records of 11
  §5.2 stop being stripped from `0x66` frames of *this* job. Nothing else.
* **No "arm and forget".** There is no configuration file key, no environment variable and no CLI
  flag that pre-arms the laser or raises `max_arm_ttl_s` past its bound. `nexcut-mccd` gains no
  `--arm-laser`; the only path is the IPC request above, on a connection that already owns the
  motion arming, quoting a job that has already been dry-run.
* **Simulator by default (D3).** `arm_laser` against the simulator target is granted and logged
  like any other, because the whole point of the simulator is to exercise this path; the real card
  still needs `--card-ip`.

---

### 6. The placeholder, and the exact lines that change when M5 starts

Today the port cannot reach `LASER_ARMED` from outside at all, in four independent places. M5
changes them in this order and no earlier:

| File | What is there now | What M5 changes |
|---|---|---|
| `src/nexcut/mccd/daemon.py`, `IPC_COMMANDS` (the tuple at line ~126) and its docstring | the 21-command vocabulary has no `arm_laser`, and the docstring says the `*_job` commands "are **dry run only**" | add `"arm_laser"` and `"disarm_laser"`; add `_cmd_arm_laser` / `_cmd_disarm_laser` implementing §1–§4; the docstring stops saying dry-run-only and cites D13 |
| `src/nexcut/mccd/daemon.py`, `load_job_frames` (the `if self.gate.arming.state is ArmState.LASER_ARMED: raise IpcError("refused", "the machine is LASER_ARMED: this phase streams dry runs only (docs/DECISIONS.md D5)")` block) | a job cannot even be *loaded* while laser-armed | the refusal goes; loading a new job while armed instead revokes the arming first (§3: a new token is a new job) |
| `src/nexcut/mccd/feeder.py`, `JobFeeder._preflight` and `_stop_reason` (the two `ArmState.LASER_ARMED` branches: `"streaming is refused while LASER_ARMED: this phase runs dry runs only (docs/DECISIONS.md D5)"` and `"machine became LASER_ARMED (D5)"`) | streaming refuses to start, and aborts if the state appears mid-job | both branches invert: `LASER_ARMED` becomes a permitted streaming state for the token that owns it, and the abort fires on `LASER_ARMED` **without** the matching token. `_preflight` also asserts the §2 checks still hold |
| `src/nexcut/mcc/safety.py`, `ArmingStateMachine.arm_laser` (`confirmed` / `job_token` arguments), `SafetyConfig.laser_do_ports` / `laser_da_channel_words` / `fifo_laser_da_channel_words`, and the `0x66` branch of `SafeMccClient` | the **placeholder refusal lives here and stays**: `arm_laser` raises `ArmingError` without an explicit confirmation and without the running job's token; a DO write to port 5/6/9, a non-zero laser DA word and a `0x67 ← [1]`-less FIFO start over frames queued while armed are all refused unless the state is `LASER_ARMED`; every `0x66` frame goes through `strip_laser_records` in every other state. Nothing outside `tests/` can reach `LASER_ARMED`, because no IPC command leads here | **unchanged in shape**: `arm_laser` gains no new parameter (the daemon turns §1's typed string into `confirmed`), the allow/deny classification is untouched, and the `0x66` branch gains only the counter of §4. The refusals this entry lifts are the daemon's and the feeder's, not this module's |

`mcc/safety.py` is therefore *not* the placeholder that goes away — it is the mechanism the
placeholder protects. The refusals that change are the two in `feeder.py` and the one in
`daemon.load_job_frames`, plus the absent command in `IPC_COMMANDS`.

**Why.** PORT-PLAN §8.2 requires that `LASER_ARMED` be reachable "only from the UI with a
confirmation, only while a job-running token exists, auto-disarming on stop/alarm/comm loss". That
sentence leaves four things open that an implementer would otherwise settle by accident: *what* the
confirmation is (here: a typed string naming the job, not a boolean), *what else* the request has to
quote so the arming is specific to a cut (here: the layer set and the material preset hash), *what
the daemon must independently prove* before granting it (here: the eight checks of §2, of which the
D2 scale verification and the dry-run requirement are the two that actually gate M5), and *what ends
it* (here: eleven events, including three that do not exist yet). Writing them down first is cheaper
than arguing them over a lit tube.

**Alternatives rejected.**
* *A boolean `confirmed` flag* (what `safety.py` takes internally): too easy to set by accident, and
  it carries no evidence that the operator saw this job. Kept as the internal parameter, never as
  the wire format.
* *Arming the laser for a session rather than for a job*: it is the vendor's model (the PSU key is
  the session), and it is exactly the state D9 refused for motion — an arming nobody remembers
  granting. A job token is the shortest lease that still lets a job run.
* *No idle timeout, by analogy with D9*: rejected above. The asymmetry is deliberate and argued.
* *Letting the UI hold the arming across a pause*: rejected; a paused job is where the operator
  walks up to the machine.

**Where (once implemented).** `nexcut.mccd.daemon.IPC_COMMANDS`, `_cmd_arm_laser`,
`_cmd_disarm_laser`, `_release_arming`, `_disarm_and_stop`; `nexcut.mccd.feeder.JobFeeder._preflight`
/ `_stop_reason` / `dry_run_result`; `nexcut.mcc.safety.ArmingStateMachine.arm_laser`;
`nexcut.core.config` for `laser.max_arm_ttl_s`. Tests: a new `tests/test_mccd_laser_arming.py`
covering each §2 check failing on its own, each §3 event revoking, and an adversarial pass in the
style of R12–R21.

**How to change.** This entry, then the code — never the other way round. Any relaxation of §2 or
§3 needs a new dated amendment here with the reason, exactly as D9/D12 carry theirs.

---

## D14 — `.chf` v2–v4 reserved lines are kept in a model slot, and nothing is rejected (X6)

**Status: decided (2026-09-16) and implemented.** This closes the strict xfail X6
(`tests/test_io_fidelity_review.py::test_legacy_reserved_lines_with_content_are_not_lost`),
which STATUS §2 correctly called "a design choice waiting to be made, not a missing
measurement".

**The situation.** Versions 2–4 of the `.chf` container emit a fixed number of extra lines at
seven places (03 §9, counted `ReadToken` loops at `0x100a991d`, `0x1006b51a`, `0x1006b64f`,
`0x1006b6f7`, `0x1007694b`, `0x10094944`, `0x100a40b7`):

| Site | Lines | Owner in the port |
|---|---|---|
| after `<Begin Graphs>` | 5 | `ChfDocument.legacy_reserved` |
| after `<Glyphs>` | 10 | `Contour.legacy_reserved_glyphs` |
| after each glyph | 3 | `ContourElement.legacy_reserved` |
| after `<Crafts>` | 20 | `Crafts.legacy_reserved` |
| start of a group body | 3 | `Group.legacy_reserved` (inherited by `ContourEx`/`Scan`, and carried by a `Text`'s outline group) |
| after `<Scan path>` | 5 | `Scan.legacy_reserved_paths` |
| start of a text record | 5 | `Text.legacy_reserved` |

The vendor reader consumes them and never looks at them; 03 §9 reads them as reserved string
fields a writer emitted as `WriteStr(NULL)`. All four shipped v4 samples leave every one of
them empty, and **no v1–v4 writer exists in the shipped DLL**, so there is no evidence either
way about whether any producer ever puts content there. Until this entry the port skipped
them unread, i.e. it silently dropped bytes it could not model.

**Decision.** Option (a) of the three below: **keep the raw line text in a model slot and
re-emit it; reject nothing.**

* Each record owns the lines that belong to it (table above), as `list[str]`, latin-1 decoded
  — lossless for every byte the tokenizer can produce.
* On write, a short list is padded with empty lines exactly as the vendor's `WriteStr(NULL)`
  would write them. **More** lines than the site reserves is a `ValueError`, and so is a line
  break inside one: either would change the line grammar the vendor reader counts by, which
  is the one thing the port must never emit.
* All-blank reserved lines — the only case that exists in the wild today — are stored as the
  **empty list**, not as a list of empty strings. The file is identical either way (the writer
  pads), and it keeps a document the port built equal to the same document read back, keeps
  the JSON dumps clean, and allocates nothing on a 50 000-contour v4 file.
* A v5 document written as v4 emits blank reserved lines, because a v5 file has none to carry.

**Why, and why not the alternatives.**

* *Option (b), reject a v2–v4 file whose reserved lines are not empty (a strict-mode error).*
  Rejected. The port would refuse a file the vendor tool reads without complaint, on the
  strength of a guess about what the bytes mean. Interchange failures are the expensive kind
  of failure here, and this one would be self-inflicted.
* *Option (c), keep skipping them.* Rejected: it is the one behaviour that is certainly wrong
  if a producer does write there, and it fails silently — a file round-tripped through the
  port loses data with nothing to show for it. The whole point of 03 §14's "keep every scalar
  the original serialises" is that unknown ≠ ignorable.
* Option (a) costs one list per record, is invisible when the lines are empty (the [] rule),
  and its failure mode is inert data carried along.

**What it does *not* claim.** Nothing here says what the lines mean, or that the vendor ever
writes one. UNVERIFIED, and settled by nothing short of a Wine session that saves a v2–v4 file
(`docs/WINE-SESSION-H.md`, question H-4) — which no shipped build can do, since the writer is
v5-only. The slots simply make that question harmless in the meantime.

**Where.** `nexcut.io.chf._Tokens.reserved`, `_Out.reserved`, the seven call sites in
`_read_*`/`_write_*`; `nexcut.model.graph` (the `legacy_reserved*` fields and the module
docstring). Tests: `tests/test_io_fidelity_review.py::test_legacy_reserved_lines_*` (four,
including the former X6), plus the eight byte-identity samples of
`test_all_vendor_chf_files_rewrite_byte_identical`, which are unchanged and still green.

**How to change.** Code only, plus this entry. To go to option (b), raise in
`_Tokens.reserved` when `any(raw)` and `self.strict`; the model slots can stay.

---

## D15 - A parameter file changes only where the operator changed it (U1-U4, W1-W3)

**Status: decided (2026-09-16) and implemented.** Written by the UI/safety/UX adversarial review,
which found four independent ways for a property page to alter a value nobody edited and three ways
for the write-back to damage the file it was saving. `docs/STATUS.md` §1.4 has the measured effect
of each; this entry is the rule they were fixed against.

**Decision.** Three clauses, in order of how much they cost to hold:

1. **An editor that is opened and closed without a change writes nothing.** Not "writes the same
   value" - writes *nothing*. `_Delegate` records the value at `setEditorData` time (`_OPENED_AT`)
   and `setModelData` returns early when the editor still holds it.
2. **An editor may not be able to store a value the schema rejects.** `editor_range` converts the
   descriptor's own min/max into the *display* unit, quantises both bounds **inward** to the
   resolution the widget actually offers, and re-checks them through `to_stored`; `clamp_stored`
   runs on the way out. Where a unit conversion makes an integer row unrepresentable (`FCP.MaxAcc`
   20000 mm/s^2 = 2.0394 G, and an int spin box can only offer `2`), the row gets a
   `QDoubleSpinBox` instead - the same rule `display_text` already followed.
3. **A value the file already holds is legal, whatever the schema says.** `PropertyGrid.stored_bounds`
   widens the editor's range to contain the value the row holds, so a vendor file that violates its
   own descriptor range (this machine's `GRP.EdgeBoardSizeX/Y` hold `5.0` against a min of `50`) is
   *preserved*, not "corrected". `core.schema.Descriptor.validate`'s own docstring already said
   vendor data is not guaranteed to satisfy min/max and that callers should warn rather than reject;
   this makes the editors obey it.

And, for the file itself (`io/params.write_params`, W1-W3): the directory is `fsync`ed after
`os.replace`, so the ordering the docstring promises ("either the old file, or the old file plus its
copy") survives a power loss; the replaced file's **mode is carried over** instead of `mkstemp`'s
`0600`, and the `.bak` inherits the mode of the file it is a copy of; and a failed read-back verify
names the backup in its message.

**Why.** The port's job in M3 is to be usable *alternately* with the Windows tool on the same
machine (PORT-PLAN §1 goal 2). A page that quietly rewrites `GRP.EdgeBoardSizeX` from 5 to 50, or
`FCP.MaxAcc` from 20000 to 19613, breaks that goal in the most expensive possible way: no error, no
prompt, no undo, and the damage is discovered by the machine. The vendor's own files are the proof
that "the schema is the truth" is wrong - they violate it - so the only defensible rule is that the
port is a *lossless* editor of somebody else's file and an edit is something the operator did.

**Alternatives rejected.**
* *Clamp the value into the descriptor range and warn.* This is what the code did by accident, and
  it is what U3 is. A warning the operator sees after the file is written is not consent.
* *Refuse to load a file that violates its own schema.* Refuses this machine's real files.
* *Block the save on `document.validate()`.* Same objection. A non-blocking "N values outside their
  range" line on save is worth having and is `docs/STATUS.md` §5 task 10; it does not change this
  entry.

**UNVERIFIED in the implementation** (`docs/STATUS.md` §3.8, settle in Wine session H):
`property_grid.INT32_MIN/INT32_MAX` as the editor bound for a u32 descriptor - a port choice
justified by the vendor writing those rows signed (`SOP.JoystickID2 = "-684904636"` in this
machine's `File/BkHardPara.xml`), but a file value above 2**31-1 is then preserved and displayed and
not editable; and `_QUANTISE_STEPS = 4`, how far a bound may be stepped inward.

**Where.** `nexcut.ui.property_grid` (`stored_bounds`, `clamp_stored`, `uses_int_spin`,
`editor_range`, `_editor_value`, `_snap`, `_Delegate`), `nexcut.io.params` (`_fsync_dir`,
`_atomic_write`, `write_params`). Tests: `tests/test_ui_safety_review.py` sections A and B (52 tests
in the file), plus the existing UI and params tests, which are unchanged and still green.

**How to change.** This entry, then the code. Loosening clause 1 or 3 needs a dated amendment here
with the reason - they are the two that protect the operator's file.

---

## Entries still to be written

* **The open half of D7** (see that entry): whether the operator may jog off a pressed hard limit
  while `alarm_1 ≠ 0`. Settled by 11 §7 step 7, not by argument.
