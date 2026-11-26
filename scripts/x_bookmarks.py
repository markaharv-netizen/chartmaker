"""X Bookmarks for Chartmaker: normalize X API bookmark pages and seal them.

The repository is private and the tab is login-gated. Bookmark data still
never goes into the repo in plain form: refresh_x_bookmarks.py builds one
bundle (posts + compressed thumbnails), encrypts it with a key derived from
the Chartmaker app password, and stores the ciphertext on the data-only
branch x-bookmarks-data. The server decrypts it in memory and serves it only
to signed-in browsers.

Stdlib only (server side). Cipher: PBKDF2-HMAC-SHA256 key derivation, a
SHAKE-256 keystream (key || random nonce) XORed with the zlib'd JSON, and an
HMAC-SHA256 tag over header + ciphertext (encrypt-then-MAC).
"""
import base64, datetime, hashlib, hmac, json, os, re, secrets, struct, zlib
from zoneinfo import ZoneInfo

MAGIC = b"XBM1"
ITERATIONS = 200_000
ET = ZoneInfo("America/New_York")
DATA_BRANCH = "x-bookmarks-data"
DATA_PATH = "x_bookmarks.enc"


class SealError(ValueError):
    pass


def _keys(password, salt, iterations):
    if not password:
        raise SealError("no key: set APP_PASSWORD (server) or CHARTMAKER_APP_PASSWORD (refresh)")
    k = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 64)
    return k[:32], k[32:]


def _xor(data, enc_key, nonce):
    ks = hashlib.shake_256(b"xbm-ks" + enc_key + nonce).digest(len(data))
    n = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big") if n else b""


def seal(doc, password, iterations=ITERATIONS):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    enc_key, mac_key = _keys(password, salt, iterations)
    plain = zlib.compress(json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode(), 9)
    header = MAGIC + salt + nonce + struct.pack(">I", iterations)
    ct = _xor(plain, enc_key, nonce)
    tag = hmac.new(mac_key, header + ct, hashlib.sha256).digest()
    return header + ct + tag


def open_sealed(blob, password):
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < 4 + 16 + 16 + 4 + 32 or blob[:4] != MAGIC:
        raise SealError("not an X bookmarks bundle")
    salt, nonce = blob[4:20], blob[20:36]
    (iterations,) = struct.unpack(">I", blob[36:40])
    if not 10_000 <= iterations <= 5_000_000:
        raise SealError("bad iteration count")
    header, ct, tag = blob[:40], blob[40:-32], blob[-32:]
    enc_key, mac_key = _keys(password, salt, iterations)
    if not hmac.compare_digest(tag, hmac.new(mac_key, header + ct, hashlib.sha256).digest()):
        raise SealError("wrong key or damaged bundle")
    return json.loads(zlib.decompress(_xor(ct, enc_key, nonce)))


# ---------- normalizing X API pages ----------

def _full_text(post):
    note = post.get("note_post") or {}
    text = note.get("text") if isinstance(note, dict) and note.get("text") else post.get("text") or ""
    urls = ((post.get("entities") or {}).get("urls")) or []
    # note_post text carries its own entities; the post's t.co map still covers most links.
    note_urls = ((note.get("entities") or {}).get("urls")) if isinstance(note, dict) else None
    for u in list(urls) + list(note_urls or []):
        short = u.get("url")
        if not short:
            continue
        if u.get("media_key") or re.match(r"https?://(x|twitter)\.com/[^/]+/status/\d+/(photo|video)/\d+$", u.get("expanded_url") or ""):
            text = text.replace(short, "")
        elif u.get("expanded_url"):
            text = text.replace(short, u["expanded_url"])
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def et_label(iso):
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
    except (AttributeError, ValueError):
        return ""
    return dt.strftime("%b %-d, %Y %-I:%M %p ET")


METRICS = ("like_count", "repost_count", "reply_count", "quote_count", "bookmark_count", "impression_count")


def normalize(pages):
    """Merge raw get_users_bookmarks pages into an ordered list of display rows.

    Order is X's bookmark order (most recently bookmarked first). Duplicate ids keep the first.
    Returns (items, media_sources) where media_sources maps post id -> thumbnail source URL.
    """
    users, media = {}, {}
    for p in pages:
        inc = p.get("includes") or {}
        for u in inc.get("users") or []:
            users[u.get("id")] = u
        for m in inc.get("media") or []:
            media[m.get("media_key")] = m
    items, sources, seen = [], {}, set()
    for p in pages:
        for post in p.get("data") or []:
            pid = str(post.get("id") or "")
            if not pid.isdigit() or pid in seen:
                continue
            seen.add(pid)
            u = users.get(post.get("author_id")) or {}
            handle = u.get("username") or ""
            if not handle:
                m = re.match(r"https://x\.com/([^/]+)/status/", post.get("url") or "")
                handle = m.group(1) if m else "i"
            keys = ((post.get("attachments") or {}).get("media_keys")) or []
            mlist = []
            for k in keys:
                m = media.get(k)
                if not m:
                    continue
                src = m.get("url") if m.get("type") == "photo" else m.get("preview_image_url")
                src = src or m.get("url") or m.get("preview_image_url")
                mlist.append({"type": m.get("type") or "", "src": src or ""})
            first = next((x["src"] for x in mlist if x["src"]), "")
            if first:
                sources[pid] = first
            pm = post.get("public_metrics") or {}
            items.append({
                "id": pid,
                "author": {"id": str(post.get("author_id") or ""), "name": u.get("name") or handle, "username": handle},
                "text": _full_text(post),
                "created_at": post.get("created_at") or "",
                "created_et": et_label(post.get("created_at") or ""),
                "url": f"https://x.com/{handle}/status/{pid}",
                "metrics": {k: pm[k] for k in METRICS if isinstance(pm.get(k), int)},
                "media": [{"type": x["type"]} for x in mlist],
                "media_type": mlist[0]["type"] if mlist else "",
                "thumb": False,
            })
    return items, sources


def page_chain_complete(pages):
    """True when the last page has no next_token (the pull reached the end)."""
    return bool(pages) and not ((pages[-1].get("meta") or {}).get("next_token"))


def public_doc(bundle):
    """The JSON the browser gets: everything except thumbnail bytes."""
    return {
        "pulled_at": bundle.get("pulled_at"),
        "pulled_et": bundle.get("pulled_et"),
        "user": bundle.get("user"),
        "count": len(bundle.get("items") or []),
        "with_media": sum(1 for x in bundle.get("items") or [] if x.get("media")),
        "complete": bundle.get("complete", True),
        "api_calls": bundle.get("api_calls"),
        "items": bundle.get("items") or [],
    }


def thumb_bytes(bundle, pid):
    b = (bundle.get("thumbs") or {}).get(str(pid))
    return base64.b64decode(b) if b else None
