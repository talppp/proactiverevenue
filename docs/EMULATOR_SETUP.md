# Mobile-emulator setup for the Phase I demo

Goal: give each team member (Nathalie / Fabi / Noa) a "phone" they can hold up
during the live demo, with WhatsApp, Messages (SMS), Mail, and a browser tab
pointed at their personal team-inbox dashboard.

For Apple Mail (which is a macOS app, not iOS) we use **iCloud webmail** in
Chrome on the emulator — functionally equivalent for an inbound-classification
demo.

## What you need

| Item | Why | Cost |
|------|-----|------|
| Android Studio (Hedgehog or newer) | AVD Manager + emulator runtime | free |
| 8 GB RAM free on the dev box | runs 3 AVDs concurrently (~2 GB each) | – |
| Twilio sandbox phone numbers (3) | optional, only for real SMS testing | ~$3/mo total |

The WebSocket dashboard at `/inbox/<member>` is what actually drives the demo,
so even a single AVD (or zero AVDs and just three browser tabs on the dev box)
is enough to run the trace.

## Step 1 — install Android Studio + the emulator

1. Download Android Studio: <https://developer.android.com/studio>
2. During setup wizard, accept the default SDK + Android Emulator + Intel HAXM
   (or Apple Hypervisor on Mac, or AMD Hypervisor on Windows).
3. Open **Device Manager → Create Virtual Device → Pixel 6 → API 34 (system
   image: `Google Play`)** — the Play Store image lets you install WhatsApp /
   ## Step 2 — create three AVDs (one per team member)

Repeat Step 1's create-device flow three times, naming the AVDs:

- `Nathalie-Pixel`
- `Fabi-Pixel`
- `Noa-Pixel`

Set each one's RAM to 2 048 MB, internal storage 4 GB.

## Step 3 — first boot, install the messaging apps

Boot each AVD, sign in with a throwaway Google account, then install:

- **WhatsApp** (from Play Store) — register with a dedicated test phone number
- **Messages** — pre-installed (handles SMS / RCS)
- **Chrome** — pre-installed

## Step 4 — wire the inbox dashboard

Open **Chrome** on each emulator and bookmark its dashboard URL:

| Emulator        | URL                                                |
|-----------------|----------------------------------------------------|
| Nathalie-Pixel  | `http://10.0.2.2:8000/inbox/nathalie`              |
| Fabi-Pixel      | `http://10.0.2.2:8000/inbox/fabi`                  |
| Noa-Pixel       | `http://10.0.2.2:8000/inbox/noa`                   |

> **Why `10.0.2.2`?**  Android emulators use that magic IP to reach
> `localhost` on the host machine.  If you're running against the Render-deployed
> service, swap for the public URL (e.g. `https://apteker-router.onrender.com`).

In Chrome's three-dot menu → "Add to home screen" so each dashboard shows up as
a tile on the emulator's homescreen.

## Step 5 — iCloud webmail tab for "Apple Mail"

Open Chrome on each emulator a second time, sign in to <https://www.icloud.com/mail>
with a test iCloud account, leave the tab open. This stands in for Apple Mail on
the macOS desktop side.

## Step 6 — launch them all at once (optional)

```powershell
# Launch all three AVDs headless in parallel.
# Adjust the AVD names if you used different ones.
$avds = @("Nathalie-Pixel","Fabi-Pixel","Noa-Pixel")
foreach ($a in $avds) {
    Start-Process -NoNewWindow -FilePath "$env:LOCALAPPDATA\Android\Sdk\emulator\emulator.exe" `
        -ArgumentList @("-avd", $a, "-no-snapshot-save")
}
```

## Running the demo

1. **Terminal A** — start the router service:

   ```powershell
   cd Phase1_AI_Issue_Router
   $env:DB_URL = "memory"        # or your Postgres URL
   python -m uvicorn app.main:app --port 8000
   ```

2. Boot the three AVDs (Step 6).
3. Open Chrome on each emulator → tap the inbox bookmark.
4. **Terminal B** — fire the demo:

   ```powershell
   python scripts\verify_e2e.py
   ```

   The Nathalie-Pixel screen lights up with a red P0 card within ~50 ms.

5. (Optional) replay the full 3 K corpus:

   ```powershell
   python scripts\feed_mockup.py --rate 10
   ```

   All three dashboards animate concurrently over ~5 minutes.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Chrome can't reach `10.0.2.2` | Confirm `uvicorn` bound to `0.0.0.0` (the default for `--host`). |
| WhatsApp won't install | Use an x86_64 AVD with Play Store image; WhatsApp does not ship arm64 builds via Play. |
| Dashboard "connecting…" never goes green | Open the AVD's Chrome DevTools (chrome://inspect on host), look at the WS handshake — usually a CORS or wrong-host issue. |
| Demo passes but no card appears | The card was buffered before the WS connected; refresh the dashboard tab — it replays the last 50 cards. |
