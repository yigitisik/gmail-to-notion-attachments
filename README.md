# gmail-to-notion-attachments

Download attachments from any Gmail message — save them locally and/or push them to a Notion page.

Find emails by **subject, sender, label, date range, or raw Gmail search query**. Works across single emails or multiple at once.

Three output modes:

| Mode | What happens |
|------|-------------|
| **Local only** | Files saved to `./attachments/` |
| **Notion (structured)** | Notion page with email headings + placeholder slots |
| **Notion (auto-embed)** | Notion page with images fully embedded via Imgur |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Gmail API credentials (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create or select a project
2. **APIs & Services → Library** → search **Gmail API** → Enable it
3. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop App**
4. Download and save as **`credentials.json`** in this folder
5. **APIs & Services → OAuth consent screen → Test users** → add your Gmail address

On first run, a browser window opens for sign-in. After that, a `token.json` is saved and reused automatically.

> ⚠️ **Never commit `credentials.json` or `token.json`** — they are already in `.gitignore`.

### 3. Notion credentials (optional)

Only needed if you want to push to Notion.

- **Token:** [notion.so/my-integrations](https://www.notion.so/my-integrations) → New integration → copy the **Internal Integration Token**
- **Page ID:** Open your target Notion page → share it with your integration → copy the ID from the URL:
  ```
  https://www.notion.so/My-Page-abc123def456...
                                ^^^^^^^^^^^^^^^^  ← page ID
  ```

---

## Usage

### Find emails by subject

```bash
python sync.py --subject "positive moments archive"
```

### Find emails by sender

```bash
python sync.py --from photos@family.com
```

### Find emails by Gmail label

```bash
python sync.py --label receipts
```

### Full Gmail search query

Any query syntax Gmail supports works here:

```bash
python sync.py --query "from:hr@company.com after:2025/01/01 before:2025/12/31"
```

### Specific message ID(s)

```bash
python sync.py --message-id 19a9d7f71c2c4504
python sync.py --message-id abc123,def456,ghi789
```

Find the message ID in the Gmail URL when an email is open:
```
https://mail.google.com/mail/u/0/#inbox/19a9d7f71c2c4504
                                         ^^^^^^^^^^^^^^^^
```

---

## Pushing to Notion

Add `--notion-token` and `--notion-page-id` to any command:

```bash
# Structured layout — drag downloaded files into placeholder blocks
python sync.py --subject "Q3 reports" \
  --notion-token secret_xxx \
  --notion-page-id abc123

# Auto-embed images via Imgur — no manual work needed
python sync.py --subject "Q3 reports" \
  --notion-token secret_xxx \
  --notion-page-id abc123 \
  --imgur
```

When processing multiple emails, each gets its own heading section on the Notion page with the date, subject, and sender.

---

## All options

```
MESSAGE TARGETING
  --message-id      Gmail message ID(s), comma-separated
  --subject         Filter by subject line
  --query           Raw Gmail search query
  --from            Filter by sender address
  --label           Filter by Gmail label
  --after           Emails after date (YYYY/MM/DD)
  --before          Emails before date (YYYY/MM/DD)
  --max-emails      Max emails to process when searching (default: 10)

OUTPUT
  --output-dir      Local folder for downloads (default: attachments)
  --organize        Organize into subfolders per email (date_subject/)
  --mime-filter     MIME prefix filter (default: "image/"  |  "" for all types)
  --no-local-save   Skip saving files locally
  --no-dedup        Disable cross-email duplicate detection

AUTH
  --credentials     Path to credentials.json (default: credentials.json)
  --token           Path to cached token file (default: token.json)

NOTION
  --notion-token    Notion integration token (secret_...)
  --notion-page-id  Parent Notion page ID
  --notion-title    Custom title for the Notion page
  --imgur           Auto-embed images via Imgur
```

---

## Examples

```bash
# Download all attachments from a label, organized into subfolders
python sync.py --label "receipts" --organize --max-emails 50

# All file types (not just images)
python sync.py --subject "Q4 budget" --mime-filter ""

# Pull from multiple emails, push to Notion with images embedded
python sync.py --from photos@family.com --after 2025/01/01 \
  --notion-token secret_xxx --notion-page-id abc123 --imgur

# Skip local save, go straight to Notion
python sync.py --subject "my archive" \
  --notion-token secret_xxx --notion-page-id abc123 \
  --imgur --no-local-save
```

---

## How it works

```
Gmail API (OAuth)
      │
      ▼
 Search / resolve messages  ──► filter by MIME type + deduplicate
      │
      ▼
 Attachment bytes
      │
      ├──► Save to ./attachments/          (local, re-run safe)
      │
      └──► Imgur upload (--imgur flag)
                │
                ▼
          Notion API  ──► Page with per-email sections + image blocks
```

---

## Notes

- **Deduplication** is on by default — if the same file appears in multiple emails, it's only saved/uploaded once. Disable with `--no-dedup`.
- **Re-run safe** — already-downloaded files are skipped automatically.
- **Notion block limit** — the Notion API accepts 100 blocks per request; the script batches automatically for larger sets.
- **Imgur** — anonymous uploads are public and may expire after 6 months of inactivity. For permanent storage, host images on S3 or Cloudinary and swap the URL into the script.

---

## License

MIT
