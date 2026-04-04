#!/usr/bin/env python3
"""
sync.py — Gmail Attachments → Local + Notion
---------------------------------------------
Search for Gmail messages by query (subject, sender, label, date, etc.)
and download their attachments locally and/or push to Notion.

See README.md for full setup and usage instructions.
"""

import sys
import base64
import hashlib
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
IMGUR_CLIENT_ID = "546c25a59c58ad7"


# ── Gmail Auth ──────────────────────────────────────────────────────────────────

def get_gmail_service(credentials_path="credentials.json", token_path="token.json"):
    creds = None
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(credentials_path).exists():
                print(f"❌  {credentials_path} not found.")
                print("    Download OAuth credentials from Google Cloud Console.")
                print("    See README.md → Setup → Gmail for instructions.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_path).write_text(creds.to_json())
        print(f"✅  Gmail authenticated. Token saved to {token_path}")
    return build("gmail", "v1", credentials=creds)


# ── Message Search / Resolution ─────────────────────────────────────────────────

def resolve_messages(service, args):
    """
    Returns a list of message dicts [{id, subject, date, from}] based on
    --message-id, --subject, --query, --from, --label, --after, --before flags.
    """
    if args.message_id:
        ids = [mid.strip() for mid in args.message_id.split(",")]
        messages = []
        for mid in ids:
            meta = service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["Subject", "Date", "From"]
            ).execute()
            messages.append(_extract_meta(meta))
        return messages

    # Build query from flags
    query_parts = []
    if args.query:
        query_parts.append(args.query)
    if args.subject:
        query_parts.append(f'subject:"{args.subject}"')
    if args.from_address:
        query_parts.append(f"from:{args.from_address}")
    if args.label:
        query_parts.append(f"label:{args.label}")
    if args.after:
        query_parts.append(f"after:{args.after}")
    if args.before:
        query_parts.append(f"before:{args.before}")

    query_parts.append("has:attachment")
    query = " ".join(query_parts)

    if query.strip() == "has:attachment":
        print("❌  Provide at least one of: --message-id, --subject, --query, --from, --label")
        sys.exit(1)

    print(f"\n🔍  Searching Gmail: {query}")
    result = service.users().messages().list(
        userId="me", q=query, maxResults=args.max_emails
    ).execute()

    raw = result.get("messages", [])
    if not raw:
        print("⚠️   No messages found matching your query.")
        sys.exit(0)

    print(f"    Found {len(raw)} message(s).")
    messages = []
    for m in raw:
        meta = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "Date", "From"]
        ).execute()
        messages.append(_extract_meta(meta))

    return messages


def _extract_meta(msg):
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    ts = int(msg.get("internalDate", 0)) / 1000
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "unknown"
    return {
        "id": msg["id"],
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", "unknown"),
        "date": date_str,
    }


# ── Fetch Attachments ───────────────────────────────────────────────────────────

def fetch_attachments(service, message, mime_filter=None):
    """
    Returns list of dicts: {filename, mime_type, data, checksum, id, subject, date, from}
    mime_filter: e.g. "image/" to only return images. Pass None for all types.
    """
    msg = service.users().messages().get(
        userId="me", id=message["id"], format="full"
    ).execute()

    results = []
    for part in msg.get("payload", {}).get("parts", []):
        filename = part.get("filename", "")
        mime_type = part.get("mimeType", "")
        if not filename:
            continue
        if mime_filter and not mime_type.startswith(mime_filter):
            continue

        att_id = part.get("body", {}).get("attachmentId")
        if not att_id:
            continue

        att = service.users().messages().attachments().get(
            userId="me", messageId=message["id"], id=att_id
        ).execute()
        data = base64.urlsafe_b64decode(att["data"])
        results.append({
            "filename": filename,
            "mime_type": mime_type,
            "data": data,
            "checksum": hashlib.md5(data).hexdigest(),
            **{k: message[k] for k in ("id", "subject", "date", "from")},
        })

    return results


def deduplicate(attachments, seen_checksums):
    """Filter out attachments whose content has already been seen (cross-email dedup)."""
    unique = []
    for att in attachments:
        if att["checksum"] not in seen_checksums:
            seen_checksums.add(att["checksum"])
            unique.append(att)
        else:
            print(f"  ⏭️   Skipping duplicate: {att['filename']}")
    return unique


# ── Save Locally ────────────────────────────────────────────────────────────────

def save_locally(attachments, output_dir="attachments", organize_by_email=False):
    """
    Save attachments to disk. Skips files that already exist (re-run safe).
    If organize_by_email=True, creates subdirectories per email (date_subject/).
    """
    for att in attachments:
        if organize_by_email:
            safe_subject = "".join(
                c if c.isalnum() or c in " -_" else "_" for c in att["subject"]
            )[:50].strip()
            folder = Path(output_dir) / f"{att['date']}_{safe_subject}"
        else:
            folder = Path(output_dir)

        folder.mkdir(parents=True, exist_ok=True)
        path = folder / att["filename"]

        if path.exists():
            print(f"  ⏭️   Already exists, skipping: {path}")
        else:
            path.write_bytes(att["data"])
            print(f"  💾  Saved → {path}")

    print(f"\n📁  Files saved to ./{output_dir}/")


# ── Notion Helpers ──────────────────────────────────────────────────────────────

def notion_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def create_notion_page(token, parent_page_id, title, children, emoji="📎"):
    payload = {
        "parent": {"page_id": parent_page_id},
        "icon": {"emoji": emoji},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]}
        },
        "children": children[:100],  # Notion API limit per request
    }
    resp = requests.post(f"{NOTION_API}/pages", headers=notion_headers(token), json=payload)
    if resp.status_code != 200:
        print(f"❌  Notion API error {resp.status_code}:\n{resp.text}")
        return None

    page = resp.json()
    page_id = page.get("id", "")
    page_url = page.get("url", "")

    # Append remaining blocks in batches if over 100
    remaining = children[100:]
    if remaining:
        _append_notion_blocks(token, page_id, remaining)

    print(f"✅  Notion page created: {page_url}")
    return page_url


def _append_notion_blocks(token, page_id, blocks):
    for i in range(0, len(blocks), 100):
        batch = blocks[i:i + 100]
        resp = requests.patch(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=notion_headers(token),
            json={"children": batch},
        )
        if resp.status_code != 200:
            print(f"⚠️   Failed to append block batch: {resp.status_code}")


def _email_heading_block(message):
    text = f"📧  {message['date']}  ·  {message['subject']}  ·  {message['from']}"
    return {
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
    }


# ── Notion: Placeholder Layout ──────────────────────────────────────────────────

def push_to_notion_placeholders(token, parent_page_id, grouped, title):
    print("\n📄  Creating Notion page with placeholders...")
    children = [
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content":
                    "Files saved locally — drag them into the placeholder blocks below, "
                    "or re-run with --imgur to auto-embed."
                }}],
                "icon": {"emoji": "💡"}, "color": "blue_background",
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]

    for message, attachments in grouped:
        children.append(_email_heading_block(message))
        for att in attachments:
            children.append({
                "object": "block", "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": f"[ {att['filename']} ]"},
                        "annotations": {"italic": True, "color": "gray"},
                    }]
                }
            })
        children.append({"object": "block", "type": "divider", "divider": {}})

    return create_notion_page(token, parent_page_id, title, children, emoji="📎")


# ── Notion: Imgur Auto-Embed ────────────────────────────────────────────────────

def upload_to_imgur(image_data):
    resp = requests.post(
        "https://api.imgur.com/3/image",
        headers={"Authorization": f"Client-ID {IMGUR_CLIENT_ID}"},
        data={"image": base64.b64encode(image_data).decode(), "type": "base64"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Imgur upload failed: {resp.status_code} {resp.text}")
    return resp.json()["data"]["link"]


def push_to_notion_imgur(token, parent_page_id, grouped, title):
    print("\n🖼️   Uploading images to Imgur and embedding in Notion...")
    children = []

    for message, attachments in grouped:
        children.append(_email_heading_block(message))
        for att in attachments:
            print(f"  ⬆️   {att['filename']}...", end=" ", flush=True)
            try:
                url = upload_to_imgur(att["data"])
                print(f"✅  {url}")
                children.append({
                    "object": "block", "type": "image",
                    "image": {"type": "external", "external": {"url": url}}
                })
            except RuntimeError as e:
                print(f"FAILED — {e}")
                children.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": f"[ upload failed: {att['filename']} ]"},
                            "annotations": {"color": "red", "italic": True},
                        }]
                    }
                })
        children.append({"object": "block", "type": "divider", "divider": {}})

    return create_notion_page(token, parent_page_id, title, children, emoji="🖼️")


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download Gmail attachments locally and/or push to Notion.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
FIND MESSAGES BY:
  --message-id   One or more raw Gmail message IDs (comma-separated)
  --subject      Email subject line (partial match supported)
  --query        Full Gmail search query (any syntax Gmail supports)
  --from         Filter by sender address
  --label        Filter by Gmail label
  --after        Only emails after this date (YYYY/MM/DD)
  --before       Only emails before this date (YYYY/MM/DD)

EXAMPLES:
  # By subject
  python sync.py --subject "positive moments archive"

  # By Gmail search query
  python sync.py --query "from:boss@company.com after:2025/01/01"

  # Specific message IDs (comma-separated)
  python sync.py --message-id abc123,def456

  # Download + push to Notion with Imgur embedding
  python sync.py --subject "Q3 reports" \\
    --notion-token secret_xxx --notion-page-id abc123 --imgur

  # Pull from multiple emails, organized into subfolders, all file types
  python sync.py --label "receipts" --organize --max-emails 20 --mime-filter ""
        """
    )

    target = parser.add_argument_group("message targeting (use one or combine)")
    target.add_argument("--message-id",   help="Gmail message ID(s), comma-separated")
    target.add_argument("--subject",      help="Filter by subject line")
    target.add_argument("--query",        help="Raw Gmail search query")
    target.add_argument("--from",         dest="from_address", help="Filter by sender address")
    target.add_argument("--label",        help="Filter by Gmail label")
    target.add_argument("--after",        help="Emails after date (YYYY/MM/DD)")
    target.add_argument("--before",       help="Emails before date (YYYY/MM/DD)")
    target.add_argument("--max-emails",   type=int, default=10,
                        help="Max emails to process when searching (default: 10)")

    output = parser.add_argument_group("output")
    output.add_argument("--output-dir",   default="attachments",
                        help="Local folder for downloads (default: attachments)")
    output.add_argument("--organize",     action="store_true",
                        help="Organize into subfolders per email (date_subject/)")
    output.add_argument("--mime-filter",  default="image/",
                        help='MIME prefix filter (default: "image/"  |  pass "" for all types)')
    output.add_argument("--no-local-save", action="store_true",
                        help="Skip saving files locally")
    output.add_argument("--no-dedup",     action="store_true",
                        help="Disable cross-email duplicate detection")

    auth = parser.add_argument_group("auth")
    auth.add_argument("--credentials",   default="credentials.json",
                      help="Path to Google OAuth credentials file")
    auth.add_argument("--token",         default="token.json",
                      help="Path to cached Gmail token")

    notion = parser.add_argument_group("notion (optional)")
    notion.add_argument("--notion-token",   help="Notion integration token (secret_...)")
    notion.add_argument("--notion-page-id", help="Parent Notion page ID")
    notion.add_argument("--notion-title",
                        help="Notion page title (default: email subject, or 'Gmail Attachment Archive')")
    notion.add_argument("--imgur",          action="store_true",
                        help="Auto-embed images via Imgur (no manual dragging needed)")

    args = parser.parse_args()

    service = get_gmail_service(args.credentials, args.token)
    messages = resolve_messages(service, args)

    print(f"\n📬  Processing {len(messages)} email(s)...\n")

    seen_checksums = set()
    grouped = []
    total_attachments = 0
    mime_filter = args.mime_filter if args.mime_filter != "" else None

    for message in messages:
        print(f"  📧  [{message['date']}] {message['subject']}")
        atts = fetch_attachments(service, message, mime_filter=mime_filter)

        if not args.no_dedup:
            atts = deduplicate(atts, seen_checksums)

        if not atts:
            print("      ⚠️  No matching attachments.")
            continue

        print(f"      ✅  {len(atts)} attachment(s)")
        grouped.append((message, atts))
        total_attachments += len(atts)

    if total_attachments == 0:
        print("\n⚠️   No attachments found across all matched emails.")
        return

    print(f"\n📊  Total: {total_attachments} attachment(s) across {len(grouped)} email(s)\n")

    if not args.no_local_save:
        all_atts = [att for _, atts in grouped for att in atts]
        save_locally(all_atts, args.output_dir, organize_by_email=args.organize)

    if args.notion_token and args.notion_page_id:
        title = args.notion_title or (
            messages[0]["subject"] if len(messages) == 1 else "Gmail Attachment Archive"
        )
        if args.imgur:
            push_to_notion_imgur(args.notion_token, args.notion_page_id, grouped, title)
        else:
            push_to_notion_placeholders(args.notion_token, args.notion_page_id, grouped, title)
    elif args.notion_token or args.notion_page_id:
        print("⚠️   Both --notion-token and --notion-page-id are required for Notion.")
    else:
        print("💡  Tip: add --notion-token and --notion-page-id to also push to Notion.")

    print("\n✨  Done!")


if __name__ == "__main__":
    main()
