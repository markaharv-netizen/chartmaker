#!/usr/bin/env python3
"""Screenshot the interactive pilot beside each PNG. No Google calls.

Writes PNGs under /opt/cursor/artifacts. Uses the page's own renderer.
"""
import base64, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_cotd_build import Chrome  # noqa: E402

OUT = os.environ.get("SHEETS_SHOT_DIR", "/opt/cursor/artifacts")
PORT = os.environ.get("SHEETS_SHOT_PORT", "8765")
CHROME = shutil.which("google-chrome") or shutil.which("google-chrome-stable")

PILOT = [
    ("mstr-debt-holdings", "sheets/data/bot3/1145735379.json", "sheets/full/bot3/1145735379.png", "tip"),
    ("mstr-debt-qq", "sheets/data/bot3/1145735379_2.json", "sheets/full/bot3/1145735379_2.png", ""),
    ("mstr-debt-sats", "sheets/data/bot3/1145735379_3.json", "sheets/full/bot3/1145735379_3.png", ""),
    ("mstr-debt-mnav", "sheets/data/bot3/1145735379_4.json", "sheets/full/bot3/1145735379_4.png", ""),
    ("mstr-vs-asst-leverage", "sheets/data/bot2/1690294676.json", "sheets/full/bot2/1690294676.png", "zoom"),
    ("usd-reserve", "sheets/data/bot2/49428948.json", "sheets/full/bot2/49428948.png", ""),
    ("btc-vol-vix", "sheets/data/bot1/1546024641.json", "sheets/full/bot1/1546024641.png", ""),
    ("btc-200wma", "sheets/data/bot1/1810821762.json", "sheets/full/bot1/1810821762.png", ""),
    ("real-sp500", "sheets/data/bot1/1540677114.json", "sheets/full/bot1/1540677114.png", ""),
]


def wait_until(cdp, expr, pred, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = cdp.evaluate(expr)
        if pred(last):
            return last
        time.sleep(0.15)
    raise TimeoutError("%s -> %r" % (expr, last))


def shot(cdp, name):
    clip = cdp.evaluate("""(() => {
      const el = document.querySelector('[data-shot]');
      const r = el.getBoundingClientRect();
      return {x: r.left + window.scrollX, y: r.top + window.scrollY, width: Math.ceil(r.width), height: Math.ceil(r.height), scale: 1};
    })()""")
    result = cdp.call("Page.captureScreenshot", {
        "format": "png",
        "captureBeyondViewport": True,
        "clip": clip,
    }, timeout=30)
    path = os.path.join(OUT, name + ".png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(result["data"]))
    print("wrote", path, os.path.getsize(path))
    return path


def main():
    if not CHROME:
        raise SystemExit("google-chrome is required")
    os.makedirs(OUT, exist_ok=True)
    env = os.environ.copy()
    env.pop("APP_PASSWORD", None)
    env["PORT"] = PORT
    env["SHEETS_LIVE"] = ""
    server = subprocess.Popen(
        [sys.executable, "server.py"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 15
        import urllib.request
        while time.time() < deadline:
            if server.poll() is not None:
                err = server.stderr.read().decode("utf-8", "replace")[:500]
                raise SystemExit("server exited: " + err)
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/" % PORT, timeout=1).read(64)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("server did not start")
        with Chrome(CHROME) as browser:
            cdp = browser.cdp
            cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": 1500, "height": 1100, "deviceScaleFactor": 1, "mobile": False,
            })
            cdp.call("Page.navigate", {"url": "http://127.0.0.1:%s/?tab=sheets" % PORT})
            wait_until(cdp, "document.readyState==='complete' && typeof sheetLiveToggle==='function'", lambda v: v is True)
            wait_until(cdp, "!!document.getElementById('shq')", lambda v: v is True, timeout=15)
            # Real library: search, open Interactive on the first MSTR Debt chart.
            cdp.evaluate("""(() => {
              const q = document.getElementById('shq');
              q.value = 'MSTR Debt';
              q.dispatchEvent(new Event('input', {bubbles:true}));
            })()""")
            wait_until(
                cdp,
                """(() => {
                  const n = document.getElementById('shcount');
                  const rows = [...document.querySelectorAll('#shlist .row')].map(r => (r.querySelector('h3')||{}).textContent||'');
                  return {count: n ? n.textContent : '', rows: rows.slice(0, 6)};
                })()""",
                lambda v: isinstance(v, dict) and v.get("rows") and "MSTR Debt" in v["rows"] and len(v["rows"]) <= 6,
                timeout=20,
            )
            time.sleep(0.4)
            cdp.evaluate("""(() => {
              const row = [...document.querySelectorAll('#shlist .row')].find(r => (r.querySelector('h3')||{}).textContent === 'MSTR Debt');
              const btn = row && row.querySelector('button.live');
              if (!btn) return 'no button';
              btn.click();
              return 'clicked';
            })()""")
            time.sleep(0.8)
            diag = cdp.evaluate("""(() => {
              const row = [...document.querySelectorAll('#shlist .row')].find(r => (r.querySelector('h3')||{}).textContent === 'MSTR Debt');
              const box = row && row.querySelector('.livebox');
              return {
                clicked: row ? (row.querySelector('button.live')||{}).textContent : '',
                box: box ? box.innerText.slice(0, 400) : '',
                canvas: row ? row.querySelectorAll('canvas').length : 0,
                err: window.__liveErr || ''
              };
            })()""")
            print("DIAG", diag)
            wait_until(cdp, """(() => {
              const row = [...document.querySelectorAll('#shlist .row')].find(r => (r.querySelector('h3')||{}).textContent === 'MSTR Debt');
              return !!(row && row.querySelector('.lv canvas'));
            })()""", lambda v: v is True, timeout=10)
            time.sleep(0.4)
            cdp.evaluate("""(() => {
              const row = [...document.querySelectorAll('#shlist .row')].find(r => (r.querySelector('h3')||{}).textContent === 'MSTR Debt');
              if (row) row.setAttribute('data-shot','1');
              const chart = row && row.querySelector('.lv') && row.querySelector('.lv')._ec;
              if (chart) chart.dispatchAction({type:'showTip', seriesIndex:0, dataIndex:4});
            })()""")
            time.sleep(0.3)
            shot(cdp, "sheets-library-mstr-debt-interactive")
            cdp.evaluate("document.querySelector('#shlist [data-shot]') && document.querySelector('#shlist [data-shot]').removeAttribute('data-shot')")

            # Side-by-side gallery using the same renderer.
            specs = []
            for name, data, png, mode in PILOT:
                specs.append({"name": name, "data": data, "png": png, "mode": mode})
            cdp.evaluate("window.__pilotSpecs = " + __import__("json").dumps(specs))
            built = cdp.call("Runtime.evaluate", {
                "expression": r"""(async () => {
              const root = document.getElementById('sheets');
              root.style.maxWidth = '1460px';
              root.innerHTML = '<h2>Interactive pilot beside the PNG</h2>';
              const style = document.createElement('style');
              style.textContent = '.cmp{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px;margin:0 0 16px}.cmp h3{margin:0 0 8px;font-size:16px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-items:start}.pair img{width:100%;height:auto;border:1px solid #eee;background:#fff}.cmp .livebox{flex:none;width:auto;margin:0;padding:0;border:0}.cmp .lv{width:100%;height:420px}';
              document.head.appendChild(style);
              for (const spec of window.__pilotSpecs) {
                const doc = await (await fetch(spec.data)).json();
                const box = document.createElement('section');
                box.className = 'cmp';
                box.innerHTML = '<h3></h3><div class="pair"><img alt=""><div class="livebox"><div class="lv"></div><p class="lvnote"></p></div></div>';
                box.querySelector('h3').textContent = doc.title || spec.name;
                const img = box.querySelector('img');
                img.alt = doc.title || '';
                img.src = spec.png;
                box.querySelector('.lvnote').textContent = liveCaption(doc);
                root.appendChild(box);
                const chart = window.__sheetLiveRender(box.querySelector('.lv'), doc);
                box.querySelector('.lv')._ec = chart;
                box.dataset.ready = chart ? '1' : '0';
                if (spec.mode === 'tip' && chart) chart.dispatchAction({type:'showTip', seriesIndex:0, dataIndex: Math.min(8, (doc.categories||[]).length-1)});
                if (spec.mode === 'zoom' && chart) chart.dispatchAction({type:'dataZoom', start:62, end:100});
                box.dataset.name = spec.name;
              }
              const combo = document.createElement('section');
              combo.className = 'cmp';
              combo.dataset.name = 'combo-fixture';
              combo.innerHTML = '<h3>Combo fixture: price line and volume columns</h3><div class="pair"><div class="livebox" style="grid-column:1 / -1"><div class="lv"></div><p class="lvnote"></p></div></div>';
              const comboDoc = {supported:true,type:'combo',title:'Combo fixture: price line and volume columns',legend:true,sample:false,
                source:'Renderer fixture, not a library tab',categories:['2024-01','2024-02','2024-03','2024-04','2024-05','2024-06'],
                x:{title:''},y:[{title:'USD',format:'number'},{title:'Shares',format:'integer'}],
                series:[
                  {name:'Price',type:'line',axis:'left',color:'#F7931A',data:[20,24,22,28,26,30]},
                  {name:'Volume',type:'column',axis:'right',color:'#8B5CF6',data:[1200,1800,900,2100,1600,2400]}
                ]};
              root.appendChild(combo);
              combo.querySelector('.lvnote').textContent = liveCaption(comboDoc);
              const cc = window.__sheetLiveRender(combo.querySelector('.lv'), comboDoc);
              combo.querySelector('.lv')._ec = cc;
              combo.dataset.ready = cc ? '1' : '0';
              return 'ok';
            })()""",
                "awaitPromise": True,
                "returnByValue": True,
            }, timeout=30)
            if built.get("exceptionDetails"):
                raise SystemExit(str(built["exceptionDetails"])[:800])
            wait_until(cdp, "document.querySelectorAll('.cmp[data-ready=\"1\"]').length", lambda n: n == len(PILOT) + 1, timeout=20)
            wait_until(cdp, "[...document.querySelectorAll('.cmp img')].every(img => img.complete && img.naturalWidth > 0)", lambda v: v is True, timeout=15)
            time.sleep(0.6)
            for name, _data, _png, _mode in PILOT:
                cdp.evaluate("document.querySelectorAll('[data-shot]').forEach(el => el.removeAttribute('data-shot'))")
                cdp.evaluate("document.querySelector('.cmp[data-name=%s]').setAttribute('data-shot','1')" % __import__("json").dumps(name))
                shot(cdp, "pilot-" + name)
            cdp.evaluate("document.querySelectorAll('[data-shot]').forEach(el => el.removeAttribute('data-shot'))")
            cdp.evaluate("document.querySelector('.cmp[data-name=\"combo-fixture\"]').setAttribute('data-shot','1')")
            shot(cdp, "pilot-combo-dual-axis")
            print("gallery", (built.get("result") or {}).get("value"))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
