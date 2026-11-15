#!/usr/bin/env python3
"""Rolling archive of Lloyd's 9 AM ET news briefings for the Daily briefing tab.

One entry per calendar day in America/New_York. upsert() replaces a day only when
the new mail is the same message or was sent later. HTML is sanitized on write
and again when the page payload is built: no scripts, iframes, or remote images.
"""
from __future__ import annotations

import datetime
import html as htmlmod
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_REL = os.path.join("data", "lloyd_briefings.json")

ALLOWED_TAGS = {
    "p", "br", "div", "span", "strong", "b", "em", "i", "u", "a",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "code",
    "hr", "table", "thead", "tbody", "tr", "th", "td",
}
VOID_TAGS = {"br", "hr"}
ALLOWED_ATTR = {
    "a": {"href", "title"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
UNSUB_RE = re.compile(
    r"unsubscribe|opt[-_]?out|email[-_]?preferenc|manage[-_]?subscription|"
    r"list-manage|list-unsubscribe|mailing[-_]?list|safesubscribe|"
    r"cdn-cgi/track|email[_-]open|open[_-]pixel|tracking[_-]pixel",
    re.I,
)
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "mc_cid", "mc_eid", "fbclid", "gclid", "igshid",
    "msclkid", "dclid", "twclid", "yclid", "_hsenc", "_hsmi", "mkt_tok",
    "icid", "oly_anon_id", "oly_enc_id",
}
URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'<>]+", re.I)
# All-caps section labels used in Lloyd's later briefings (MARKETS, READS, …).
SECTION_RE = re.compile(r"^[A-Z][A-Z0-9 /,&'’+:-]{1,80}$")


def repo_of():
    return REPO


def data_path(repo=None):
    return os.path.join(repo or REPO, DATA_REL)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def dump_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)


def parse_sent(value):
    if isinstance(value, datetime.datetime):
        stamp = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("sent time is required")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


def et_local(stamp):
    return parse_sent(stamp).astimezone(ET)


def date_key(stamp):
    return et_local(stamp).date().isoformat()


def et_label(stamp):
    local = et_local(stamp)
    # "Sunday, Nov 15, 2026, 8:58 AM ET"
    return local.strftime("%A, %b ") + str(local.day) + local.strftime(", %Y, %-I:%M %p") + " ET"


def _safe_href(raw):
    href = htmlmod.unescape(str(raw or "")).strip()
    if not href:
        return None
    parsed = urlparse(href)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https", "mailto"):
        return None
    if scheme in ("http", "https") and not parsed.netloc:
        return None
    blob = href
    if UNSUB_RE.search(blob) or UNSUB_RE.search(parsed.path or "") or UNSUB_RE.search(parsed.query or ""):
        return None
    if scheme in ("http", "https"):
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if k.lower() not in TRACKING_PARAMS]
        parsed = parsed._replace(query=urlencode(query, doseq=True), fragment="")
        href = urlunparse(parsed)
    return href


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip = 0
        self._omit_text = 0
        self._open = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "link", "meta",
                   "base", "form", "input", "button", "textarea", "select",
                   "img", "source", "picture", "video", "audio", "svg", "canvas"}:
            if tag in {"script", "style", "iframe", "object", "embed", "svg", "picture", "video", "audio"}:
                self._skip += 1
            return
        if self._skip or self._omit_text or tag not in ALLOWED_TAGS:
            return
        allowed = ALLOWED_ATTR.get(tag, set())
        kept = []
        drop_tag = False
        omit_text = False
        for name, value in attrs:
            name = (name or "").lower()
            if name.startswith("on") or name in {"style", "src", "srcset", "poster"}:
                continue
            if name not in allowed:
                continue
            if name == "href":
                raw = htmlmod.unescape(str(value or "")).strip()
                href = _safe_href(raw)
                if not href:
                    drop_tag = True
                    omit_text = bool(UNSUB_RE.search(raw))
                    break
                kept.append(("href", href))
                kept.append(("rel", "noopener noreferrer"))
                kept.append(("target", "_blank"))
            else:
                kept.append((name, value or ""))
        if drop_tag:
            # Drop unsubscribe link text; keep the label of a rejected javascript: link.
            if omit_text:
                self._omit_text += 1
            else:
                self._open.append("#drop-a")
            return
        attr = "".join(
            f' {htmlmod.escape(n)}="{htmlmod.escape(v, quote=True)}"' for n, v in kept
        )
        self.out.append(f"<{tag}{attr}>")
        if tag not in VOID_TAGS:
            self._open.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "svg", "picture", "video", "audio"}:
            if self._skip:
                self._skip -= 1
            return
        if tag == "a" and self._omit_text:
            self._omit_text -= 1
            return
        if self._skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if "#drop-a" in self._open and tag == "a":
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i] == "#drop-a":
                    del self._open[i]
                    break
            return
        if not self._open or self._open[-1] != tag:
            if tag in self._open:
                # Close through to the matching tag, ignoring drops.
                while self._open:
                    opened = self._open.pop()
                    if opened == tag:
                        self.out.append(f"</{tag}>")
                        break
                    if opened != "#drop-a":
                        self.out.append(f"</{opened}>")
            return
        self._open.pop()
        self.out.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"img", "source"} or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._skip or self._omit_text or not data:
            return
        self.out.append(htmlmod.escape(data, quote=False))

    def handle_entityref(self, name):
        if self._skip or self._omit_text:
            return
        self.out.append(htmlmod.escape(f"&{name};", quote=False))

    def handle_charref(self, name):
        if self._skip or self._omit_text:
            return
        self.out.append(htmlmod.escape(f"&#{name};", quote=False))


def sanitize_html(raw):
    if not raw:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(str(raw))
        parser.close()
    except Exception:
        return htmlmod.escape(re.sub(r"<[^>]+>", "", str(raw)))
    html = "".join(parser.out)
    html = re.sub(r"(?:<p>\s*</p>\s*)+", "", html)
    return html.strip()


def _trim_url(url):
    return url.rstrip(".,;:!?)]}'\"")


def _linkify(text):
    out = []
    last = 0
    for match in URL_RE.finditer(text):
        out.append(htmlmod.escape(text[last:match.start()]))
        raw = match.group(0)
        trimmed = _trim_url(raw)
        trail = raw[len(trimmed):]
        href = _safe_href(trimmed)
        if href:
            out.append(
                f'<a href="{htmlmod.escape(href, quote=True)}" rel="noopener noreferrer" target="_blank">'
                f"{htmlmod.escape(trimmed)}</a>"
            )
        else:
            out.append(htmlmod.escape(trimmed))
        out.append(htmlmod.escape(trail))
        last = match.end()
    out.append(htmlmod.escape(text[last:]))
    return "".join(out)


def text_to_html(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    parts = re.split(r"\n\s*\n", text)
    chunks = []
    for part in parts:
        lines = [line.rstrip() for line in part.split("\n")]
        if not any(line.strip() for line in lines):
            continue
        if len(lines) == 1 and SECTION_RE.match(lines[0].strip()):
            chunks.append(f"<h3>{htmlmod.escape(lines[0].strip())}</h3>")
            continue
        body = "<br>\n".join(_linkify(line) for line in lines)
        chunks.append(f"<p>{body}</p>")
    return "\n".join(chunks)


def render_body(body_html=None, body_text=None):
    if body_html and str(body_html).strip():
        cleaned = sanitize_html(body_html)
        if cleaned:
            return cleaned
    return text_to_html(body_text or "")


def empty_store():
    return {"updatedAt": None, "briefings": {}}


def load_store(path=None, repo=None):
    path = path or data_path(repo)
    doc = load_json(path, None)
    if not isinstance(doc, dict) or not isinstance(doc.get("briefings"), dict):
        return empty_store()
    return doc


def _entry(subject, sent, body_html=None, body_text=None, message_id=None):
    stamp = parse_sent(sent)
    html = render_body(body_html=body_html, body_text=body_text)
    return {
        "date": date_key(stamp),
        "sentAt": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sentAtEt": et_label(stamp),
        "subject": str(subject or "").strip(),
        "bodyText": str(body_text or "").replace("\r\n", "\n").replace("\r", "\n").strip(),
        "bodyHtml": html,
        "messageId": str(message_id or "").strip(),
    }


def upsert(subject, sent, body_html=None, body_text=None, message_id=None,
           path=None, repo=None):
    """Insert or replace one day's briefing. Idempotent for the same message.

    A later email for the same ET date replaces the stored one. An earlier
    email is ignored. Returns (entry, status) where status is
    'inserted', 'updated', 'unchanged', or 'skipped'.
    """
    path = path or data_path(repo)
    entry = _entry(subject, sent, body_html=body_html, body_text=body_text,
                   message_id=message_id)
    if not entry["subject"]:
        raise ValueError("subject is required")
    if not entry["bodyHtml"] and not entry["bodyText"]:
        raise ValueError("briefing body is empty")
    store = load_store(path)
    briefings = store.setdefault("briefings", {})
    day = entry["date"]
    previous = briefings.get(day)
    if previous:
        if previous.get("messageId") and entry["messageId"] and previous["messageId"] == entry["messageId"]:
            # Same message: refresh sanitized body/subject if the script improved,
            # but treat identical payloads as unchanged.
            if (previous.get("subject") == entry["subject"]
                    and previous.get("bodyText") == entry["bodyText"]
                    and previous.get("bodyHtml") == entry["bodyHtml"]
                    and previous.get("sentAt") == entry["sentAt"]):
                return previous, "unchanged"
            briefings[day] = entry
            store["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            dump_json(path, store)
            return entry, "updated"
        if previous.get("sentAt") and previous["sentAt"] > entry["sentAt"]:
            return previous, "skipped"
        if previous.get("sentAt") == entry["sentAt"] and previous.get("subject") == entry["subject"] \
                and previous.get("bodyText") == entry["bodyText"]:
            return previous, "unchanged"
    status = "inserted" if not previous else "updated"
    briefings[day] = entry
    store["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dump_json(path, store)
    return entry, status


def list_briefings(store):
    items = [item for item in (store.get("briefings") or {}).values() if isinstance(item, dict)]
    items.sort(key=lambda item: (item.get("date") or "", item.get("sentAt") or ""), reverse=True)
    return items


def public_entry(entry):
    """Payload the page renders. Re-sanitize so a stale file cannot ship a script."""
    html = sanitize_html(entry.get("bodyHtml") or "") or text_to_html(entry.get("bodyText") or "")
    return {
        "date": entry.get("date"),
        "sentAt": entry.get("sentAt"),
        "sentAtEt": entry.get("sentAtEt") or (et_label(entry["sentAt"]) if entry.get("sentAt") else ""),
        "subject": entry.get("subject") or "",
        "bodyHtml": html,
    }


def build(repo=None, path=None):
    store = load_store(path or data_path(repo), repo)
    items = [public_entry(item) for item in list_briefings(store)]
    return {
        "title": "Daily briefing",
        "lede": "Lloyd's 9 AM ET news briefing, newest first.",
        "count": len(items),
        "briefings": items,
    }


def summary(doc):
    items = doc.get("briefings") or []
    if not items:
        return "No Lloyd briefings archived yet."
    lines = [f"{doc.get('title') or 'Daily briefing'}: {len(items)} briefing(s)"]
    for item in items[:8]:
        lines.append(f"  {item.get('sentAtEt') or item.get('date')} — {item.get('subject')}")
    if len(items) > 8:
        lines.append(f"  … {len(items) - 8} earlier")
    return "\n".join(lines)
