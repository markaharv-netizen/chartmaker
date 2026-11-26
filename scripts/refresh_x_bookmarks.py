#!/usr/bin/env python3
"""Rebuild the Chartmaker X Bookmarks bundle from raw X API pages.

The pull itself is done by an agent through the user-X connector (signed in
as Mark, id 127712777), never by calling api.x.com from this script. A full
pull is:

1. get_users_bookmarks, max_results 100, following meta.next_token as
   pagination_token until it is absent (X stops at ~800). Save each page in
   order as /workspace/x-bookmarks/raw/pageNN.json. This script refuses a
   chain whose last page still has a next_token (pass --allow-partial to
   override), so a refresh always paginates to the end.
2. get_users_bookmark_folders -> raw/folders.json, then
   get_users_bookmarks_by_folder_id for each folder -> raw/folders/<id>.json
   (more pages: <id>_2.json ...). Posts filed in a folder do not appear in
   the main list, and the folder call returns ids only.
3. Hydrate the folder-only ids with get_posts_by_ids (100 ids per call; the
   built-in public X reads work and do not spend Mark's credits), same
   fields as step 1 -> raw/hydrateNN.json.

  python3 scripts/refresh_x_bookmarks.py --raw /workspace/x-bookmarks/raw/page*.json \
      --folders-list /workspace/x-bookmarks/raw/folders.json \
      --folder-dir /workspace/x-bookmarks/raw/folders \
      --hydrated /workspace/x-bookmarks/raw/hydrate*.json --api-calls N --publish

Steps: merge -> download each post's first image (photo url, or
preview_image_url for video/GIF) from pbs.twimg.com -> compress to a small
JPEG -> seal everything with the app password -> optionally commit the sealed
file to the data-only branch x-bookmarks-data. Plain bookmark data and
thumbnails never go into the repo in plain form. Pulls spend Mark's X API credits: pull
on demand only, no polling.

Env: CHARTMAKER_APP_PASSWORD (or XB_KEY) for the key; CHARTMAKER_GITHUB_TOKEN
for --publish. Neither is printed.
"""
import argparse, base64, datetime, glob, io, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import x_bookmarks as xb

THUMB_W, THUMB_H, THUMB_MAX = 360, 360, 22_000
REPO = os.environ.get("VIEWS_REPO") or "markaharv-netizen/chartmaker"


def load_page(path):
    with open(path, encoding="utf-8") as f:
        t = f.read()
    return json.loads(t[t.index("{"): t.rindex("}") + 1])


def compress(raw):
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
    best = None
    for q in (62, 54, 46, 38, 30):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        best = buf.getvalue()
        if len(best) <= THUMB_MAX:
            break
    return best


def fetch_image(src):
    u = urllib.parse.urlparse(src)
    if u.scheme != "https" or u.hostname != "pbs.twimg.com":
        raise ValueError("thumbnail host not allowed")
    if u.path.startswith("/media/") and "name=" not in u.query:
        src += ("&" if u.query else "?") + "name=small"
    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(8_000_000)


def gh(method, path, body=None, token=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                          "User-Agent": "chartmaker", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def publish(blob, token, message):
    """Commit the sealed file to the data-only branch (creates it as an orphan the first time)."""
    branch = xb.DATA_BRANCH
    parent = None
    try:
        parent = gh("GET", f"git/ref/heads/{branch}", token=token)["object"]["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    readme = ("Data-only branch for Chartmaker's X Bookmarks tab. x_bookmarks.enc is encrypted "
              "with a key derived from the app password; it is not readable without it. "
              "Written by scripts/refresh_x_bookmarks.py on main.\n")
    tree = gh("POST", "git/trees", {"tree": [
        {"path": xb.DATA_PATH, "mode": "100644", "type": "blob",
         "sha": gh("POST", "git/blobs", {"content": base64.b64encode(blob).decode(), "encoding": "base64"}, token)["sha"]},
        {"path": "README.md", "mode": "100644", "type": "blob", "content": readme},
    ]}, token)["sha"]
    commit = gh("POST", "git/commits", {"message": message, "tree": tree, "parents": [parent] if parent else []}, token)["sha"]
    if parent:
        gh("PATCH", f"git/refs/heads/{branch}", {"sha": commit, "force": False}, token)
    else:
        gh("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit}, token)
    return commit


FOLDER_PAGE_CAP = 20  # the folder endpoint has returned at most 20 ids with no next_token


def load_folder_dir(path):
    """folder id -> [pages], from <id>.json, <id>_2.json, ... in path."""
    out = {}
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        m = re.fullmatch(r"(\d+)(?:_(\d+))?\.json", os.path.basename(f))
        if m:
            out.setdefault(m.group(1), []).append((int(m.group(2) or 1), load_page(f)))
    return {k: [p for _, p in sorted(v, key=lambda x: x[0])] for k, v in out.items()}


def build(pages, api_calls=None, user=None, fetch=fetch_image, now=None, hydrated=None, folder_list=None, folder_pages=None):
    fmap = xb.folder_map(folder_list, folder_pages)
    items, sources = xb.normalize(list(pages) + list(hydrated or []), fmap)
    have = {x["id"] for x in items}
    names = {str(f.get("id")): f.get("name") for f in (folder_list or {}).get("data") or []}
    capped = sorted(names.get(fid) or fid for fid, ps in (folder_pages or {}).items()
                    if ps and len(ps[-1].get("data") or []) >= FOLDER_PAGE_CAP and not (ps[-1].get("meta") or {}).get("next_token"))
    thumbs, failed = {}, []
    for it in items:
        src = sources.get(it["id"])
        if not src:
            continue
        try:
            thumbs[it["id"]] = base64.b64encode(compress(fetch(src))).decode()
            it["thumb"] = True
        except Exception as e:  # keep the row; it just shows no preview
            failed.append((it["id"], str(e)[:120]))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    bundle = {
        "version": 1,
        "pulled_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pulled_et": xb.et_label(now.strftime("%Y-%m-%dT%H:%M:%SZ")),
        "user": user or {"id": "127712777", "username": "thepowerfulhrv"},
        "api_calls": api_calls if api_calls is not None else len(pages),
        "complete": xb.page_chain_complete(pages),
        "folders": [f.get("name") for f in (folder_list or {}).get("data") or []],
        "unavailable": len(set(fmap) - have),
        "folder_capped": capped,
        "items": items,
        "thumbs": thumbs,
    }
    return bundle, failed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", nargs="+", required=True, help="raw get_users_bookmarks page files, in pull order")
    ap.add_argument("--allow-partial", action="store_true", help="accept a page chain that still has a next_token")
    ap.add_argument("--folders-list", help="get_users_bookmark_folders response")
    ap.add_argument("--folder-dir", help="dir of get_users_bookmarks_by_folder_id pages named <folder id>[_N].json")
    ap.add_argument("--hydrated", nargs="*", default=[], help="get_posts_by_ids responses for folder-only posts")
    ap.add_argument("--api-calls", type=int, default=None, help="X API calls the pull used (default: page count)")
    ap.add_argument("--out", default="/workspace/x-bookmarks/x_bookmarks.enc")
    ap.add_argument("--publish", action="store_true", help="commit the sealed file to branch x-bookmarks-data")
    a = ap.parse_args(argv)
    key = os.environ.get("XB_KEY") or os.environ.get("CHARTMAKER_APP_PASSWORD") or ""
    pages = [load_page(p) for p in sorted(a.raw)]
    errs = xb.page_chain_errors(pages)
    if errs and not a.allow_partial:
        raise SystemExit("incomplete pull: " + "; ".join(errs) + " (fetch the remaining pages, or --allow-partial)")
    hydrated = [load_page(p) for p in sorted(a.hydrated)]
    folder_list = load_page(a.folders_list) if a.folders_list else None
    folder_pages = load_folder_dir(a.folder_dir) if a.folder_dir else None
    bundle, failed = build(pages, a.api_calls, hydrated=hydrated, folder_list=folder_list, folder_pages=folder_pages)
    blob = xb.seal(bundle, key)
    if xb.open_sealed(blob, key)["items"] != bundle["items"]:
        raise SystemExit("round-trip check failed")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(blob)
    n = len(bundle["items"])
    print(f"bookmarks={n} with_media={sum(1 for x in bundle['items'] if x['media'])} thumbs={len(bundle['thumbs'])} "
          f"thumb_failures={len(failed)} complete={bundle['complete']} in_folders={sum(1 for x in bundle['items'] if x['folders'])} "
          f"unavailable={bundle['unavailable']} folder_capped={bundle['folder_capped']} sealed_bytes={len(blob)} out={a.out}")
    for pid, err in failed:
        print(f"  no thumbnail for {pid}: {err}")
    if a.publish:
        token = os.environ.get("CHARTMAKER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not token:
            raise SystemExit("--publish needs CHARTMAKER_GITHUB_TOKEN")
        sha = publish(blob, token, f"X bookmarks refresh {bundle['pulled_at']} ({n} posts)")
        print(f"published {xb.DATA_BRANCH}@{sha[:12]}")


if __name__ == "__main__":
    main()
