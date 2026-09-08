# ThinkVision blackout — investigation log

## Hardware
- Mac mini M2 (`Mac14,3`), macOS 26.6.2
- LG/Lenovo ThinkVision T27 (T27hv), native res 2560x1440

## Actual topology (established late in the investigation)
Original reports said "direct HDMI." The real chain, for most of the affected
period, was:

```
Mac mini --USB-C--> Lenovo ThinkPad Hub (own power supply) --HDMI--> ThinkVision
```

This matters: Lenovo docks are validated against ThinkPads/Windows, not
macOS's USB-C DisplayPort-Alt-Mode stack — third-party dock + Mac is a known
compatibility risk category, independent of any specific hardware fault.

## Evidence trail
1. First `collect.sh` capture (dock still in the chain, believed at the time
   to be irrelevant) showed a burst of
   `IOAccessoryManagerUSBC::setDisplayPortPinAssignment(): dpPinAssignment: 0`
   repeating every ~0.5s for 12s. Dismissed at the time because the user
   reported a direct-HDMI connection. In hindsight this is almost certainly
   the dock's video link renegotiating.
2. A live capture during an actual blackout (`correlate.sh`-style manual
   capture) caught the mechanism directly:
   - `12:03:26.844` — WindowServer: `Display 1 hot plug` → OUT
   - `12:03:27.208` — fresh EDID re-read begins
   - `12:03:27.291` — `Display 1 hot plug` → IN (electrical recovery in ~0.5s)
   - `12:03:27.328` — `Deferred hotplugs cannot be processed in a non-wake state`
   - picture did not actually return for **7 minutes**, until user input
     forced a WindowServer `DidWake` transition at `12:10:16.998`
   - `pmset -g log`'s "Display is turned off/on" entries were WindowServer's
     after-the-fact description of this blip, not an idle-timer decision.
3. Switching 75Hz -> 60Hz did NOT stop the blackouts. Rules out link
   bandwidth/refresh rate as the driver (HPD is a simple contact signal,
   independent of pixel clock).
4. User then tried 3 different HDMI cables between the dock and the
   monitor — total failure, no picture at all (not intermittent). This
   points at the dock's internal DP-to-HDMI conversion chip, not the cables.
5. Decisive test: Mac mini's HDMI port wired directly to the ThinkVision
   HDMI input, dock fully out of the video path. Picture returned
   immediately. **Result pending** on whether blackouts recur over sustained
   use (see below).

## Suspects ruled out
- macOS idle/energy-saving settings (displaysleep resets on every
  keystroke; blackouts occurred mid-typing)
- Link bandwidth / refresh rate (60Hz didn't help)
- The monitor's own standby (no OSD-side evidence; the HPD-blip event is
  Mac-side)

## Current suspect
Lenovo ThinkPad Hub's HDMI/DP-alt-mode conversion path, used with a Mac
(a combination Lenovo doesn't validate).

## Open test (in progress)
Direct Mac-to-monitor HDMI, dock removed from the video path entirely.
Counting real HPD blips with `~/tv-hpd-count.sh` on the Mac mini
(`~/thinkvision-logs/hpd-events.log`, `grep -c 'hot plug 0'`).

- Near-zero blips + no blackouts over a full day -> dock confirmed as sole
  cause. Fix: keep direct HDMI permanently; route only non-video
  peripherals (keyboard/mouse/ethernet) through the dock if still wanted.
- Blackouts recur even on direct HDMI -> back to the Mac's HDMI port or
  cable itself; revisit cable swap / USB-C-to-DisplayPort bypass tests.

## Scripts in this directory
- `collect.sh` — one-shot evidence bundle (identity, pmset, unified log)
- `correlate.sh` — run within ~2 min of a blackout with the wall-clock time;
  checks whether macOS logged an intentional display-off and pulls the
  hotplug/EDID sequence around it
- `thinkvision-diag.sh` / `thinkvision-fix.sh` — earlier general-purpose
  diagnostic/mitigation pair, written before the dock was known about;
  still useful for the pmset/assertions side but the hotplug-specific
  findings above supersede its four-suspect framing
