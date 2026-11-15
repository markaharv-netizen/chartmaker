#!/usr/bin/env python3
"""Upsert one of Lloyd's 9 AM ET briefings into data/lloyd_briefings.json.

A daily routine can pass today's email and optionally commit and push to main.
The GitHub token is read from CHARTMAKER_GITHUB_TOKEN and is never printed.

  python3 scripts/add_lloyd_briefing.py \
    --subject "$SUBJECT" \
    --sent-at "$SENT_AT" \
    --body-file "$BODY_PATH" \
    --commit --push

  python3 scripts/add_lloyd_briefing.py --from-json briefing.json --commit --push
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lloyd_briefings as lloyd

REPO_SLUG = "markaharv-netizen/chartmaker"
MAIN_BRANCH = "main"


def read_file(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def load_payload(args):
    subject = args.subject
    sent = args.sent_at
    body_text = args.body
    body_html = args.html
    message_id = args.message_id
    if args.body_file:
        body_text = read_file(args.body_file)
    if args.html_file:
        body_html = read_file(args.html_file)
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as handle:
            doc = json.load(handle)
        subject = subject or doc.get("subject")
        sent = sent or doc.get("sentAt") or doc.get("sent") or doc.get("date")
        body_text = body_text if body_text is not None else doc.get("bodyText") or doc.get("body") or doc.get("text")
        body_html = body_html if body_html is not None else doc.get("bodyHtml") or doc.get("html")
        message_id = message_id or doc.get("messageId") or doc.get("id")
    return subject, sent, body_html, body_text, message_id


def git(repo, *argv, env=None, capture=True):
    run = subprocess.run(
        ["git", "-C", repo, *argv],
        check=False,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if run.returncode != 0:
        err = (run.stderr or run.stdout or "").strip()
        # Never echo a command line that might contain a token.
        raise SystemExit(f"git {' '.join(argv[:1])} failed: {err or run.returncode}")
    return (run.stdout or "").strip()


def commit_and_push(repo, path, message, push):
    rel = os.path.relpath(path, repo)
    git(repo, "add", "--", rel)
    cached = subprocess.run(
        ["git", "-C", repo, "diff", "--cached", "--quiet", "--", rel],
        check=False,
    )
    if cached.returncode == 0:
        print("no archive changes to commit")
        return False
    git(repo, "commit", "-m", message)
    print(f"committed {rel}")
    if not push:
        return True
    token = os.environ.get("CHARTMAKER_GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("CHARTMAKER_GITHUB_TOKEN is not set; commit is local only")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    dest = f"https://x-access-token:{token}@github.com/{REPO_SLUG}.git"
    run = subprocess.run(
        ["git", "-C", repo, "push", dest, f"HEAD:{MAIN_BRANCH}"],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if run.returncode != 0:
        err = (run.stderr or run.stdout or "").strip()
        err = err.replace(token, "[redacted]")
        raise SystemExit(f"git push failed: {err or run.returncode}")
    print(f"pushed HEAD to {MAIN_BRANCH}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Upsert Lloyd's 9 AM briefing into the Chartmaker archive")
    parser.add_argument("--repo", default=lloyd.repo_of())
    parser.add_argument("--data", help="archive JSON path (default data/lloyd_briefings.json)")
    parser.add_argument("--subject")
    parser.add_argument("--sent-at", help="send time, ISO-8601 (UTC or offset)")
    parser.add_argument("--body", help="plain-text body")
    parser.add_argument("--body-file", help="read the plain-text body from this file")
    parser.add_argument("--html", help="HTML body (sanitized on write)")
    parser.add_argument("--html-file", help="read the HTML body from this file")
    parser.add_argument("--message-id", help="optional Gmail or RFC message id, used for idempotent upserts")
    parser.add_argument("--from-json", help="JSON object with subject, sentAt, bodyText and/or bodyHtml")
    parser.add_argument("--commit", action="store_true", help="git add and commit the archive file")
    parser.add_argument("--push", action="store_true",
                        help="push the commit to main; requires CHARTMAKER_GITHUB_TOKEN")
    parser.add_argument("--message", help="commit message (default names the briefing date)")
    args = parser.parse_args()
    subject, sent, body_html, body_text, message_id = load_payload(args)
    if not subject or not sent:
        raise SystemExit("subject and sent time are required")
    path = args.data or lloyd.data_path(args.repo)
    entry, status = lloyd.upsert(
        subject, sent, body_html=body_html, body_text=body_text,
        message_id=message_id, path=path, repo=args.repo,
    )
    print(f"{status} {entry['date']}: {entry['subject']}")
    if args.commit or args.push:
        message = args.message or f"Lloyd 9am briefing {entry['date']}"
        commit_and_push(args.repo, path, message, push=args.push)


if __name__ == "__main__":
    main()
