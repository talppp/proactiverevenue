# SMS-phone install — iOS 26 manual walkthrough

> **iOS 26.5 reality check:** Apple **blocks importing unsigned `.shortcut`
> files** on iOS 26 ("Importing unsigned shortcut files is not supported").
> The `scripts/build_ios_shortcut.py` generator therefore CANNOT be used on
> iOS 26 unless you sign the file on a Mac (`shortcuts sign -m anyone`).
> On Windows + iOS 26, the **manual build below is the only path.** Good
> news: a server change (received_at_phone is now optional) cut it down to
> a SINGLE action.

---

## FASTEST PATH — one action, ~3 minutes (iOS 26.5, no Mac needed)

The webhook now stamps the receipt time itself, so the Shortcut only has
to send two fields: who texted and what they said.

### Trigger
1. **Shortcuts** app → **Automation** tab → **+** → **Create Personal Automation**.
2. Scroll to **Message** → tap.
3. **Sender**: Anyone. **Message Contains**: type one space `" "`.
   **Run Immediately**: ON. Tap **Next**.

### The single action — Get Contents of URL
1. On the "Do" screen, tap **choose...** (or the search field).
2. Search **Get Contents of URL** → tap it.
3. In the URL field paste:
   ```
   https://apteker-router-l1.onrender.com/webhooks/sms_phone
   ```
4. Tap the **▸ / ▼ expand arrow** on the action to reveal its options.
5. **Method**: tap → **POST**.
6. **Headers**: tap **Add new header** twice:
   | Key | Text |
   |---|---|
   | `Authorization` | `Bearer ` then your SMS_BEARER_TOKEN_PRIMARY (one space after "Bearer") |
   | `Content-Type` | `application/json` |
7. **Request Body**: tap → change from "Form" to **JSON**.
8. Tap **Add new field** twice, both **Text** type:
   | Key | Value |
   |---|---|
   | `from_number` | tap the value box → **Select Variable** → pick **Sender** (from the Message trigger) |
   | `body` | tap the value box → **Select Variable** → pick **Message** (from the trigger) |
9. Tap **Next** → review → **Done**.

That's the whole thing. No Date action, no Format Date, no Dictionary.

### Self-test
Have someone text your iPhone "test from automation", then on your laptop:
```powershell
.\scripts\smoke_test.ps1
```
Test 8 (`handed_off`) should be one higher than your last run.

If your iPhone's "Sender"/"Message" variables are named differently in
the trigger (some iOS builds label them "Content"), just pick whatever
the trigger exposes for the sender and the message text.

---

## (Older / alternate paths below)

The sections below are kept for reference: the 2-action and file-import
approaches. On iOS 26.5 use the FASTEST PATH above. The 2-action version
sidesteps the iOS-17/18 Date-action categorization changes.

## Prerequisites

- iPhone on iOS 16+ (confirmed working on 26.4.2).
- The Render service deployed and your `SMS_BEARER_TOKEN_PRIMARY`
  available — same token you put in Render Environment.

## Two paths

| Path | Effort | When to use |
|---|---|---|
| **A. Generated file** | ~5 min one-time, then 3 taps on phone | Recommended. Run the script, drop the file into iCloud Drive, tap-import on iPhone, then build a 2-action automation. |
| **B. Pure manual** | ~10 min on phone | If the generated `.shortcut` fails to import on your iOS version. Same end result. |

---

## Path A — generated `.shortcut` file

### Step 1 — generate the file (on your laptop)

```bash
# from the repo root
python3 scripts/build_ios_shortcut.py \
    --url   https://apteker-router-l1.onrender.com \
    --token "<your SMS_BEARER_TOKEN_PRIMARY>" \
    --out   ./forward_sms.shortcut
```

PowerShell equivalent:

```powershell
python .\scripts\build_ios_shortcut.py `
    --url   "https://apteker-router-l1.onrender.com" `
    --token "<your SMS_BEARER_TOKEN_PRIMARY>" `
    --out   ".\forward_sms.shortcut"
```

You should now have `forward_sms.shortcut` in the current directory.

### Step 2 — enable Allow Untrusted Shortcuts (one-time on iPhone)

iOS only accepts iCloud-signed shortcuts by default. Unsigned ones
(like this one) need a one-time toggle:

1. Open the **Shortcuts** app on the iPhone.
2. Run *any* shortcut once (open one of the starter Gallery shortcuts and tap Run). This step unlocks the toggle.
3. Settings app → scroll to **Apps** → **Shortcuts** → toggle **Allow Untrusted Shortcuts** **ON**. Authenticate with your passcode.

### Step 3 — get the file onto your phone

Easiest path (no Mac required):

1. On your laptop, open <https://www.icloud.com> → sign in.
2. Click **Drive** → drag and drop `forward_sms.shortcut` into the
   Drive root.
3. On your iPhone: open **Files** → **iCloud Drive** → tap
   `forward_sms.shortcut`.
4. Tap **Open in Shortcuts** if iOS doesn't open it automatically.
5. Shortcuts will show a preview screen titled "Forward SMS to AI Router".
   Tap **Add Untrusted Shortcut** (or **Add Shortcut**).

The shortcut now lives in your library.

### Step 4 — build the Automation (the trigger)

The imported shortcut is the workhorse. We still need a 2-action
Automation that fires on inbound SMS and calls this shortcut.

1. Shortcuts → **Automation** tab (bottom) → **+** (top right) →
   **Personal Automation**.
2. Scroll to **Message**. Tap it.
3. **Sender**: leave as **Anyone**.
4. **Message Contains**: type one space (`" "`).
5. **Run Immediately**: ON.
6. Tap **Next**.

Now you're on the "Do" screen with `choose...`. Add two actions:

**Automation Action 1 — Dictionary**
1. Tap `choose...` (or `+` then search).
2. Search **Dictionary** → tap **Dictionary**.
3. Tap **Add new item** twice. Both **Text** type.
4. Fill:
   - Key `sender`, Value: tap the value field → in the variable picker
     pick **Sender** (from the Message trigger).
   - Key `body`, Value: tap → pick **Message** (from the Message trigger).

**Automation Action 2 — Run Shortcut**
1. Tap `+` to add another action.
2. Search **Run Shortcut** → tap **Run Shortcut**.
3. Tap **Shortcut**: pick **Forward SMS to AI Router** (the one you
   imported).
4. Tap **Input** (or the field labelled "If there's no shortcut input"):
   pick the **Dictionary** magic variable from Action 1.

Tap **Next** → review → **Done**.

### Step 5 — self-test

From a different phone, text your iPhone:
```
hello from manual test
```

On your laptop:
```powershell
cd <your repo>
.\scripts\smoke_test.ps1
```

Test 8 should report `handed_off` at least one higher than before.

---

## Path B — pure manual (no generator)

Use this if the generated file refused to import.

### Trigger (same as path A)
1. Shortcuts → Automation → + → Personal Automation → Message.
2. Sender: Anyone, Contains: " " (space), Run Immediately: ON, Next.

### Actions — 2 of them, no Date action needed

**Manual Action 1 — Dictionary**

(Build the request body directly. The trick: `Current Date` magic
variable works *inside* the Dictionary action, so we don't need a
separate Format Date action.)

1. `choose...` → search **Dictionary** → tap.
2. Tap **Add new item** three times. All **Text**:
   - Key `from_number`, Value: tap → pick magic variable **Sender**.
   - Key `body`, Value: tap → pick magic variable **Message**.
   - Key `received_at_phone`, Value:
     1. Tap value field.
     2. Tap **Select Variable** (or the variable-picker icon).
     3. Pick **Current Date**.
     4. Long-press the inserted "Current Date" pill.
     5. Tap **Show More** (or the formatting expand chevron).
     6. **Date Format**: Custom.
     7. **Format String**: `yyyy-MM-dd'T'HH:mm:ssXXX`

**Manual Action 2 — Get Contents of URL**

1. `+` → search **Get Contents of URL** → tap.
2. **URL** field: paste `https://apteker-router-l1.onrender.com/webhooks/sms_phone`.
3. Tap the **▼** to expand the action.
4. **Method**: tap, change to **POST**.
5. **Headers**: tap **Add new header** twice.
   - Key `Authorization`, Text: `Bearer <YOUR SMS_BEARER_TOKEN_PRIMARY>` (space between "Bearer" and the token).
   - Key `Content-Type`, Text: `application/json`.
6. **Request Body**: tap, change from "Form" to **JSON**.
7. The body field: tap → pick magic variable **Dictionary** (from
   Manual Action 1).

Tap **Next** → review → **Done**.

### Self-test
Same as path A step 5.

---

## Common iOS 26 gotchas

| Symptom | Cause | Fix |
|---|---|---|
| "Allow Untrusted Shortcuts" toggle not present in Settings | You haven't run any shortcut yet | Open Shortcuts, tap any Gallery shortcut, run it once, then revisit Settings |
| Search returns nothing for "Date" | iOS 26 hides utility actions behind categories | Skip the Date action entirely — use the inline magic variable approach in Manual Action 1 |
| Cannot pass a Dictionary as "Input" to Run Shortcut | iOS 26 requires the upstream action to actually output a Dictionary | Confirm Action 1 was Dictionary, not Get Dictionary from Input |
| Automation runs but no `handed_off` increment | Bearer typo in Authorization header | Edit Action 2 → re-paste bearer, watch for trailing whitespace |
| Banner says "Automation paused" | iOS auto-paused after a long quiet period | Tap the automation → toggle Enable This Automation back on |

## When the iCloud-signed approach is worth it

If you'd rather not enable Allow Untrusted Shortcuts (or you want
Nathalie to install via a single iCloud link tap), the path is:

1. Build the shortcut once on Tal's iPhone using Path A or B.
2. Open the shortcut in Shortcuts → tap the share icon → **Copy iCloud Link**.
3. Send the link to Nathalie via iMessage / email.
4. She taps it → "Get Shortcut" → done. No untrusted-toggle required
   on her end.

The signed link approach is cleanest for handoff, but you have to build
the shortcut once yourself first.
