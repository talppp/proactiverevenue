# ThinkVision random-blackout diagnosis (Mac mini, HDMI)

**Symptom:** the screen goes black as if it entered power-save, several times a
day, *while actively typing or watching*. Replugging HDMI restores it. Present
since day one.

## The one fact that narrows this down immediately

macOS's display-sleep timer is reset by **every keystroke and every mouse
movement**. If the screen blanks while you are typing, the idle timer did not
expire — so this is *not* "energy saving misfiring".

That means **your two goals are not in conflict at all.** You can keep energy
saving exactly as it is. Something else is cutting the picture.

## The signal chain, and where it can break

```
  Mac mini                                    HDMI cable                ThinkVision
 ┌────────────────────────────┐              ┌───────────┐            ┌────────────┐
 │ macOS  →  WindowServer     │  TMDS video  │           │  video     │  scaler    │
 │           ↓                │─────────────▶│  ~18 Gbps │───────────▶│   ↓        │
 │        DCP firmware        │              │  @ 4K60   │            │  panel     │
 │           ↓                │◀─────────────│           │◀───────────│  + its own │
 │        HDMI port           │   HPD + EDID │           │  HPD line  │  standby   │
 └────────────────────────────┘              └───────────┘            └────────────┘
      (1)          (3)                            (2)                      (4)
```

Four suspects, and each leaves a *different fingerprint in the logs*:

| # | Suspect | Fingerprint | Analogy |
|---|---------|-------------|---------|
| 1 | macOS software | `pmset -g log` says "Display is turned off" | The lights were switched off on purpose |
| 2 | HDMI link | HPD / link-training / EDID re-read events, **no** pmset entry | The wire came loose mid-sentence |
| 3 | DCP firmware | `dcpav` / DCP restart messages (Apple Silicon only) | The projectionist fainted |
| 4 | The monitor | **nothing in any log** | The screen decided to nap by itself |

Suspect 2 is the front-runner: replug-fixes-it and present-since-day-1 are both
textbook link-loss signatures. Suspect 3 is a close second if this is an Apple
Silicon Mac mini — the built-in HDMI port on those has a documented history of
exactly this.

## Run this, on the Mac mini

```bash
cd tools/thinkvision
./thinkvision-diag.sh snapshot      # identity + settings + last 6h, ends in a verdict
```

Blackouts are intermittent, so if the verdict says "no event in window":

```bash
./thinkvision-diag.sh record        # leave running; use the Mac normally
# when the screen blacks out: wait ~10s, replug, come back, Ctrl-C, then:
./thinkvision-diag.sh verdict
```

`record` is a flight recorder — like an aircraft black box, it is running
*before* the incident so the incident is on tape.

## Elimination protocol

Run in this order; each step is cheap and rules out one suspect for good.

| Step | Do | Rules out | Time |
|------|----|-----------|------|
| 0 | `./thinkvision-fix.sh apply` — keeps energy saving, removes the video-playback case and system-sleep renegotiation | Suspect 1 | 1 min |
| 1 | ThinkVision OSD → turn **off** Auto Standby / Deep Sleep / DDC-CI | Suspect 4 | 2 min |
| 2 | Swap in a **certified Ultra High Speed** HDMI cable, ≤2 m | Suspect 2 (cable) | 1 day of use |
| 3 | Drop to 4K@30, or 1440p@60, or turn HDR off — halves the link bandwidth. **If blackouts stop, the link was marginal.** | Suspect 2 (margin) | 1 day |
| 4 | **USB-C → DisplayPort** cable into a Thunderbolt port, bypassing the HDMI port and its DCP path entirely | Suspects 2 + 3 | 1 day |

Step 4 is the highest-yield single change on an Apple Silicon Mac mini. If the
blackouts vanish over USB-C→DP but return over HDMI, the built-in HDMI port is
the culprit and the fix is simply to stay on DisplayPort.

## While you are still testing

```bash
./thinkvision-fix.sh autorecover-install
```

Opt-in band-aid: when the panel disappears for ~9 seconds it forces the
framebuffer to re-initialise — the software equivalent of replugging the cable,
without reaching behind the machine. It treats the symptom only; keep working
through the protocol above.

Remove it with `./thinkvision-fix.sh autorecover-remove`.
