#!/usr/bin/env python3
"""Generate an iOS Shortcut file (.shortcut plist) that forwards inbound
SMS to the L1 webhook.

  ⚠️ iOS 26 NOTE: Apple blocks importing UNSIGNED shortcut files on
  iOS 26 ("Importing unsigned shortcut files is not supported").
  This generator's output therefore only imports on:
    - iOS <= 15 with "Allow Untrusted Shortcuts" on, OR
    - any iOS if you first sign it on a Mac:
        shortcuts sign -i forward_sms.shortcut -o signed.shortcut -m anyone
  On Windows + iOS 26, ignore this script and build the Shortcut
  manually — see docs/runbooks/sms_phone_install_ios26.md (FASTEST PATH,
  one action). The server now stamps the timestamp, so the manual build
  is a single Get-Contents-of-URL action.


Usage:
    python scripts/build_ios_shortcut.py \\
        --url   https://apteker-router-l1.onrender.com \\
        --token "<SMS_BEARER_TOKEN_PRIMARY>" \\
        --out   ./forward_sms.shortcut

The output is an *unsigned* shortcut. iOS 13+ requires
Settings > Shortcuts > "Allow Untrusted Shortcuts" toggled on to import
unsigned shortcuts. That toggle only appears AFTER you've run at least
one shortcut once.

How to land it on your iPhone:
  - AirDrop the file from a Mac, or
  - Drop it into iCloud Drive on your laptop (iCloud.com -> Drive -> upload).
    Then on the iPhone, Files app -> iCloud Drive -> tap the file
    -> "Open in Shortcuts" -> "Add Shortcut".

After import, build a 2-action Automation in the Shortcuts app:
  1. Automation tab -> + -> Personal Automation -> Message
     Sender: Any, Contains: " " (single space), Run Immediately: ON.
  2. Add action: "Run Shortcut" -> pick "Forward SMS to AI Router".
     - Input: Build a Dictionary with two text entries:
         sender = magic variable "Sender"  (from the trigger)
         body   = magic variable "Message" (from the trigger)
       Pass that Dictionary as the "Input" to Run Shortcut.

The imported shortcut reads sender + body out of the input dictionary,
computes ISO 8601 timestamp, builds the JSON payload, and POSTs it with
your bearer token.

Disclaimer:
    Apple's .shortcut plist format is undocumented and changes between
    iOS versions. This generator targets the iOS 16-26 layout and works
    on most devices, but iOS 26.x has been known to be strict about
    schema. If the import fails with "Couldn't open shortcut", fall back
    to docs/runbooks/sms_phone_install_ios26.md (manual build).
"""

from __future__ import annotations

import argparse
import plistlib
import sys
import uuid
from pathlib import Path


def _uuid() -> str:
    return str(uuid.uuid4()).upper()


def _magic_var(token_string: str, attachments: dict) -> dict:
    """Wrap a string that contains magic-variable references as a
    WFTextTokenString — what iOS Shortcuts expects for parameters that
    can mix literal text and variable references."""
    return {
        "Value": {
            "string": token_string,
            "attachmentsByRange": attachments,
        },
        "WFSerializationType": "WFTextTokenString",
    }


def _attachment(action_uuid: str, output_name: str = "Dictionary") -> dict:
    """A magic variable that points to a prior action's output."""
    return {
        "OutputName": output_name,
        "OutputUUID": action_uuid,
        "Type": "ActionOutput",
    }


def _attachment_input() -> dict:
    """A magic variable that points to Shortcut Input."""
    return {"Type": "ExtensionInput"}


def _attachment_date_current() -> dict:
    return {"Type": "CurrentDate"}


def build_shortcut(url: str, bearer_token: str) -> dict:
    """Construct the WFWorkflowActions array as a Python dict that
    plistlib will serialize."""
    # UUIDs for each action so we can reference their outputs.
    get_input_uuid = _uuid()
    sender_uuid    = _uuid()
    body_uuid      = _uuid()
    date_uuid      = _uuid()
    fmt_date_uuid  = _uuid()
    dict_uuid      = _uuid()
    url_uuid       = _uuid()
    post_uuid      = _uuid()

    actions = [
        # 1. Get Shortcut Input: this is the dict passed by the
        #    automation { sender, body }.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getinput",
            "WFWorkflowActionParameters": {
                "UUID": get_input_uuid,
                "WFInputType": "Dictionary",
            },
        },
        # 2. Get Dictionary Value: sender
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": sender_uuid,
                "WFDictionaryKey": "sender",
                "WFGetDictionaryValueType": "Value",
            },
        },
        # 3. Get Dictionary Value: body — re-read input
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getinput",
            "WFWorkflowActionParameters": {
                "UUID": _uuid(),
                "WFInputType": "Dictionary",
            },
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
            "WFWorkflowActionParameters": {
                "UUID": body_uuid,
                "WFDictionaryKey": "body",
                "WFGetDictionaryValueType": "Value",
            },
        },
        # 4. Current Date
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.date",
            "WFWorkflowActionParameters": {
                "UUID": date_uuid,
            },
        },
        # 5. Format Date in ISO 8601 with timezone.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.format.date",
            "WFWorkflowActionParameters": {
                "UUID": fmt_date_uuid,
                "WFDateFormatStyle": "Custom",
                "WFDateFormat": "yyyy-MM-dd'T'HH:mm:ssXXX",
            },
        },
        # 6. Build the JSON payload as a Dictionary.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.dictionary",
            "WFWorkflowActionParameters": {
                "UUID": dict_uuid,
                "WFItems": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFItemType": 0,  # text
                                "WFKey": _magic_var("from_number", {}),
                                "WFValue": _magic_var(
                                    "￼",
                                    {"{0, 1}": _attachment(sender_uuid, "Dictionary Value")},
                                ),
                            },
                            {
                                "WFItemType": 0,
                                "WFKey": _magic_var("body", {}),
                                "WFValue": _magic_var(
                                    "￼",
                                    {"{0, 1}": _attachment(body_uuid, "Dictionary Value")},
                                ),
                            },
                            {
                                "WFItemType": 0,
                                "WFKey": _magic_var("received_at_phone", {}),
                                "WFValue": _magic_var(
                                    "￼",
                                    {"{0, 1}": _attachment(fmt_date_uuid, "Formatted Date")},
                                ),
                            },
                        ],
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
            },
        },
        # 7. URL
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.url",
            "WFWorkflowActionParameters": {
                "UUID": url_uuid,
                "WFURLActionURL": f"{url.rstrip('/')}/webhooks/sms_phone",
            },
        },
        # 8. Get Contents of URL — POST with bearer + JSON body.
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": post_uuid,
                "WFHTTPMethod": "POST",
                "WFHTTPHeaders": {
                    "Value": {
                        "WFDictionaryFieldValueItems": [
                            {
                                "WFItemType": 0,
                                "WFKey": _magic_var("Authorization", {}),
                                "WFValue": _magic_var(
                                    f"Bearer {bearer_token}", {}
                                ),
                            },
                            {
                                "WFItemType": 0,
                                "WFKey": _magic_var("Content-Type", {}),
                                "WFValue": _magic_var("application/json", {}),
                            },
                        ],
                    },
                    "WFSerializationType": "WFDictionaryFieldValue",
                },
                "WFHTTPBodyType": "JSON",
                "WFJSONValues": {
                    "Value": _attachment(dict_uuid, "Dictionary"),
                    "WFSerializationType": "WFTextTokenAttachment",
                },
                "ShowHeaders": True,
            },
        },
    ]

    return {
        "WFWorkflowClientVersion": "2607.0.4",
        "WFWorkflowClientRelease": "2.2.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 463343103,   # green
            "WFWorkflowIconGlyphNumber": 59446,      # speech bubble
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [
            "WFAppContentItem", "WFAppStoreAppContentItem",
            "WFArticleContentItem", "WFContactContentItem",
            "WFDateContentItem", "WFEmailAddressContentItem",
            "WFGenericFileContentItem", "WFImageContentItem",
            "WFiTunesProductContentItem", "WFLocationContentItem",
            "WFDCMapsLinkContentItem", "WFAVAssetContentItem",
            "WFPDFContentItem", "WFPhoneNumberContentItem",
            "WFRichTextContentItem", "WFSafariWebPageContentItem",
            "WFStringContentItem", "WFURLContentItem",
        ],
        "WFWorkflowName": "Forward SMS to AI Router",
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowActions": actions,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--url", required=True,
                   help="Base URL of the deployed L1 service "
                        "(e.g. https://apteker-router-l1.onrender.com)")
    p.add_argument("--token", required=True,
                   help="SMS_BEARER_TOKEN_PRIMARY from Render env vars")
    p.add_argument("--out", default="forward_sms.shortcut",
                   help="Output file path (default: forward_sms.shortcut)")
    args = p.parse_args()

    shortcut = build_shortcut(args.url, args.token)
    out_path = Path(args.out)
    with out_path.open("wb") as f:
        plistlib.dump(shortcut, f, fmt=plistlib.FMT_XML)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
    print()
    print("Next steps:")
    print(f"  1. Settings > Shortcuts > 'Allow Untrusted Shortcuts' ON")
    print(f"     (toggle only appears after running any shortcut once)")
    print(f"  2. Get {out_path.name} onto your iPhone — easiest is to")
    print(f"     drop it into iCloud Drive (drag-drop on iCloud.com),")
    print(f"     then open Files > iCloud Drive on the phone and tap it.")
    print(f"  3. Shortcuts will ask 'Add Untrusted Shortcut' — confirm.")
    print(f"  4. Build the Automation that calls this shortcut — see")
    print(f"     docs/runbooks/sms_phone_install_ios26.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
