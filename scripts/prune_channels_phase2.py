#!/usr/bin/env python3
"""Phase 2 channel pruning — targeted surgical edits to the documents
the generic regex pass couldn't safely handle.

Run AFTER prune_channels.py --apply.

  python prune_channels_phase2.py --root "<Phase1 folder>"             # dry-run
  python prune_channels_phase2.py --root "<Phase1 folder>" --apply     # write

Per-file edits below. Each entry is (search_regex, replacement). Empty
replacement = delete the whole match.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Per-file targeted edits. Order matters within a file.
# ---------------------------------------------------------------------------

EDITS: dict[str, list[tuple[str, str]]] = {
    # -----------------------------------------------------------------
    "01_Diagrams/build_diagram.py": [
        # Remove the channel-box tuples for INSTAGRAM DM and GMAIL.
        (r"^\s*\(\d+,\s*\"(?:INSTAGRAM DM|GMAIL)\",[^)]*\),?\s*\n", ""),
        # Remove the discovery-table rows.
        (r"^\s*\(\"\d\",\s*\"(?:Instagram DM|Gmail)\",[^)]*\),?\s*\n", ""),
        # Rewrite the channel-summary footer line.
        (
            r"\(Apple Mail, WhatsApp, SMS, Instagram\)\s+plus Gmail carried over from the original SOW\.",
            "(Apple Mail, WhatsApp, SMS) plus sms_phone via the on-phone iOS Shortcut.",
        ),
    ],
    # -----------------------------------------------------------------
    "02_Workflow_Designs/build_workflow_options.py": [
        (
            r'bullet\("Triggers: Gmail node, IMAP node \(Apple Mail\), WhatsApp Business node, Twilio SMS webhook, IG webhook\."\)',
            'bullet("Triggers: IMAP node (Apple Mail), WhatsApp Business node, Twilio SMS webhook, plus the iOS Shortcuts forwarder for sms_phone.")',
        ),
        (
            r'bullet\("Inbound webhooks: /webhook/whatsapp, /webhook/sms, /webhook/instagram, /webhook/gmail, /webhook/applemail\."\)',
            'bullet("Inbound webhooks: /webhook/whatsapp, /webhook/sms, /webhook/applemail, /webhooks/sms_phone.")',
        ),
    ],
    # -----------------------------------------------------------------
    "03_Discovery/build_discovery.py": [
        # Docstring header
        (
            r"Inbound Channels Discovery Draft - Apple Mail, WhatsApp, Mobile SMS, Instagram, \(Gmail\)\.",
            "Inbound Channels Discovery Draft - Apple Mail, WhatsApp, Mobile SMS, sms_phone (iOS Shortcuts).",
        ),
        # Volume-table rows for IG and Gmail
        (
            r'^\s*\["Instagram DM",[^\]]*\],?\s*\n',
            "",
        ),
        (
            r'^\s*\["Gmail \(Apteker Realty\)",[^\]]*\],?\s*\n',
            "",
        ),
        # Apple-DKIM note that name-drops Gmail
        (
            r"bullet\(\"Apple does not sign messages with DKIM headers the same way Gmail does[^\"]*\"\)",
            'bullet("Apple Mail uses lighter DKIM coverage than typical workplace mail; flag iCloud-relay senders for human review when authenticity matters.")',
        ),
        # Whole "4. Instagram DM" section — from h2("4. Instagram DM ...") up to the next h2(.
        (
            r"h2\(\"4\. Instagram DM[^\"]*\"\)\n(?:(?!h2\().*\n)+",
            "",
        ),
        # Whole "5. Gmail" section
        (
            r"h2\(\"5\. Gmail[^\"]*\"\)\n(?:(?!h2\().*\n)+",
            "",
        ),
        # Discovery questions D4 (Instagram) and D5 (Gmail) — remove the rows
        (
            r'^\s*\["D4",\s*"Instagram[^\]]*\],?\s*\n',
            "",
        ),
        (
            r'^\s*\["D5",\s*"Gmail[^\]]*\],?\s*\n',
            "",
        ),
    ],
    # -----------------------------------------------------------------
    "05_Workflow_Mapping/build_workflow_mapping.py": [
        # Channel enum docstring
        (
            r'"whatsapp \| sms \| gmail \| apple_mail \| instagram"',
            '"whatsapp | sms | sms_phone | apple_mail"',
        ),
        # Capability bullet
        (
            r"Works across WhatsApp Business, SMS, Gmail \(\+Apple Mail, \+Instagram\)",
            "Works across WhatsApp Business, SMS, sms_phone (+Apple Mail)",
        ),
        # Sender_handle example
        (
            r"\+14045551212 / john@gmail\.com / @ig_handle",
            "+14045551212 / john@apteker.com / nathalie@icloud.com",
        ),
    ],
    # -----------------------------------------------------------------
    "08_Review_Schema_Plan/build_plan.py": [
        # Meta App Review row — only WhatsApp now, drop Instagram
        (
            r'\("File Meta App Review for WhatsApp \+ Instagram"',
            '("File Meta App Review for WhatsApp"',
        ),
        # Apple Mail decision — drop "forward-to-Gmail" mention
        (
            r'\("Decision: Apple Mail strategy \(IMAP vs forward-to-Gmail\)"[^)]*\),',
            '("Decision: Apple Mail strategy (IMAP vs forward-via-bridge)", "Nathalie", "Default: native IMAP via Mail.app server-side rule."),',
        ),
        # Gmail-wire row removed
        (
            r'^\s*\("Wire Gmail API trigger \(easiest\)"[^)]*\),?\s*\n',
            "",
        ),
        # Apple Mail forward-to-Gmail row → IMAP directly
        (
            r'\("Wire Apple Mail forward-to-Gmail"\s*,[^)]*\)',
            '("Wire Apple Mail IMAP", "Engineer", "Native IMAP via Mail.app server-side rule on her iCloud account.")',
        ),
        # Verification line
        (
            r'"5\+ test messages from Gmail and Apple Mail land in inbound_message\."',
            '"5+ test messages from Apple Mail and sms_phone land in inbound_message."',
        ),
        # Instagram-wire row removed
        (
            r'^\s*\("Wire Instagram webhook \(if approved\)"[^)]*\),?\s*\n',
            "",
        ),
        # Risk row — drop Gmail-forward reference
        (
            r'"Default to forward-to[^"]*"',
            '"Default to native IMAP via Mail.app server-side rule."',
        ),
    ],
    # -----------------------------------------------------------------
    "08_Review_Schema_Plan/build_review.py": [
        (
            r'"Recommend the FORWARD-TO-GMAIL pattern[^"]*"',
            '"Recommend native IMAP for Apple Mail via Mail.app server-side rule."',
        ),
        (
            r'\["C2",\s*"Instagram DM volume[^\]]*\],?\s*\n',
            "",
        ),
    ],
    # -----------------------------------------------------------------
    "08_Review_Schema_Plan/build_schema.py": [
        (
            r'"Each monitored inbox \(Apteker Gmail, IG, WA Business, \.\.\.\)\."',
            '"Each monitored inbox (Apple Mail, WhatsApp Business, sms_phone, ...)."',
        ),
    ],
    # -----------------------------------------------------------------
    "09_Advanced_Tests/agents/orchestrator.py": [
        (
            r'"email":\s*"/webhook/gmail",\s*"voice_message":\s*"/webhook/sms"',
            '"email": "/webhook/applemail", "voice_message": "/webhook/sms"',
        ),
    ],
    # -----------------------------------------------------------------
    "09_Advanced_Tests/generate_stress_data.py": [
        (
            r"email\s+->\s+POST /webhook/gmail",
            "email          -> POST /webhook/applemail",
        ),
    ],
    # -----------------------------------------------------------------
    "09_Advanced_Tests/test_plans/combination_tests.py": [
        (
            r'url_map = \{"sms":"/webhook/sms","whatsapp":"/webhook/whatsapp","email":"/webhook/gmail"\}',
            'url_map = {"sms":"/webhook/sms","whatsapp":"/webhook/whatsapp","email":"/webhook/applemail","sms_phone":"/webhooks/sms_phone"}',
        ),
    ],
    # -----------------------------------------------------------------
    "app/ingestion/inbound.py": [
        # Docstring references
        (
            r"^\s*POST /webhook/gmail\s*\n",
            "",
        ),
        (
            r"^\s*POST /webhook/instagram\s*\n",
            "",
        ),
        # Channel enum description
        (
            r'channel: str = Field\(\.\.\., description="One of sms\|whatsapp\|gmail\|apple_mail\|instagram"\)',
            'channel: str = Field(..., description="One of sms|whatsapp|sms_phone|apple_mail")',
        ),
        # Whole /instagram route handler
        (
            r"@router\.post\(\"/instagram\"[^)]*\)\s*\nasync def [^\n]+\n(?:(?:[ \t]+[^\n]*\n)+)",
            "",
        ),
        # Whole /gmail route handler
        (
            r"@router\.post\(\"/gmail\"[^)]*\)\s*\nasync def [^\n]+\n(?:(?:[ \t]+[^\n]*\n)+)",
            "",
        ),
    ],
    # -----------------------------------------------------------------
    "docs/EMULATOR_SETUP.md": [
        # Top intro
        (
            r"during the live demo, with WhatsApp, Gmail, Messages \(SMS\), and a browser tab",
            "during the live demo, with WhatsApp, Messages (SMS), Mail, and a browser tab",
        ),
        # Install row
        (
            r"^\| Google account \(test\) \| install WhatsApp / Gmail \| free \|\s*\n",
            "",
        ),
        # Mid-doc Gmail reference (a single sentence)
        (
            r"Gmail directly\.\s*\n",
            "",
        ),
        # Install bullet
        (
            r"^- \*\*Gmail\*\* — sign in with the same throwaway Google account[^\n]*\n",
            "",
        ),
    ],
    # -----------------------------------------------------------------
    "README.md": [
        # Channel list in deliverable table
        (
            r"Inbound channels discovery — Apple Mail, WhatsApp, SMS, Instagram, Gmail",
            "Inbound channels discovery — Apple Mail, WhatsApp, SMS, sms_phone (iOS Shortcuts)",
        ),
        # Emulator line
        (
            r"running real WhatsApp / Gmail / Messages",
            "running real WhatsApp / Mail / Messages",
        ),
    ],
    # -----------------------------------------------------------------
    "scripts/seed_database.py": [
        (
            r"3 identities \(sms, whatsapp, gmail\)",
            "3 identities (sms, whatsapp, apple_mail)",
        ),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    total_files = 0
    total_edits = 0
    edited_files = 0

    for rel, edits in EDITS.items():
        path = root / Path(rel.replace("/", "\\")) if "\\" in str(root) else root / rel
        # Fallback: forward slashes; pathlib handles either on Win
        if not path.exists():
            path = root / rel
        if not path.exists():
            print(f"[skip] {rel}  (not found)")
            continue

        total_files += 1
        text = path.read_text(encoding="utf-8")
        new = text
        file_edits = 0
        for pattern, replacement in edits:
            new2, n = re.subn(pattern, replacement, new, flags=re.MULTILINE)
            if n > 0:
                file_edits += n
                new = new2

        if file_edits > 0:
            print(f"[edit] {rel}  ({file_edits} substitutions)")
            if args.apply:
                path.write_text(new, encoding="utf-8")
            total_edits += file_edits
            edited_files += 1
        else:
            print(f"[ok]   {rel}  (no patterns matched — possibly already clean)")

    print()
    print("=" * 60)
    print(f"Files targeted:    {len(EDITS)}")
    print(f"Files matched:     {total_files}")
    print(f"Files edited:      {edited_files}")
    print(f"Total substitutions: {total_edits}")
    print("=" * 60)

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
