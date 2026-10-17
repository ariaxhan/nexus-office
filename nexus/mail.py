"""The one body-bearing Gmail send: a raw draft composed from a file, then sent.

There is no reply mode and no inline body. `gmail +reply` appends the whole
quoted thread under the note (Aria's catch on TBS invoice 003, 2026-10-14), so
the route that can do that does not exist here: the recipient gets exactly the
body file, nothing else. Attachments pass the freshness gate first and are
recorded only after Gmail accepts the send (nexus-office#306).

    python3 -m nexus.mail --to ADDR --subject S --body-file F [--attach P ...]
"""

from __future__ import annotations

import argparse
import base64
import email
import json
import mimetypes
import sys
from email import policy
from email.message import EmailMessage
from pathlib import Path

from nexus import standing_gates

CONNECTORS = "/Users/slowember/Developer/Vaults/_meta/services/connectors"


def compose(to, subject, body: str, attachments=()) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, subtype="html" if body.lstrip().lower().startswith(("<!doctype", "<html")) else "plain")
    for path in attachments:
        kind = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=kind[0], subtype=kind[1], filename=path.name)
    return msg


def decode(raw: str) -> EmailMessage:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)


def _service():
    sys.path.insert(0, CONNECTORS)
    from google_connector import get_credentials
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=get_credentials())


def send(to, subject, body_file, attachments=(), service=None, ledger=standing_gates.LEDGER) -> dict:
    paths = [Path(a).resolve(strict=True) for a in attachments]
    if any(not p.is_file() for p in paths):
        raise ValueError("an attachment is not a regular file")
    names = [p.name for p in paths]
    standing_gates.check_fresh(to, names, ledger)
    svc = service or _service()
    for recipient in standing_gates._recipients(to):
        for name in names:
            safe_name = name.replace('"', '\\"')
            query = f'in:sent to:{recipient} filename:"{safe_name}"'
            res = svc.users().messages().list(userId="me", q=query, maxResults=1).execute()
            if res.get("messages"):
                raise standing_gates.StaleAttachment(
                    f"filename {name} was already sent to {recipient} (Gmail history)")
    body = Path(body_file).read_text()
    if not body.strip():
        raise ValueError("the body file is empty")
    raw = base64.urlsafe_b64encode(compose(to, subject, body, paths).as_bytes()).decode()
    gmail = svc.users().drafts()
    draft = gmail.create(userId="me", body={"message": {"raw": raw}}).execute()
    result = gmail.send(userId="me", body={"id": draft["id"]}).execute()
    standing_gates.record_sent(to, names, ledger)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nexus.mail", description=__doc__.split("\n\n")[0])
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True, help="the complete email; sent exactly as written")
    parser.add_argument("--attach", action="append", default=[], help="a file never sent to this recipient under this name")
    args = parser.parse_args(argv)
    try:
        result = send(args.to, args.subject, args.body_file, args.attach)
    except standing_gates.StaleAttachment as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({"id": result.get("id"), "threadId": result.get("threadId", "")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
