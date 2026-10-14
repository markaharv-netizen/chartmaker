import asyncio,base64,json,urllib.request
from playwright.async_api import async_playwright
THEMES=["white","cream","gray","dark","navy","black"]
async def shot(pg,name):
  url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
  open(f"/workspace/chartmaker/samples/{name}.png","wb").write(base64.b64decode(url.split(",")[1]))
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1200})
    errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
    pg.on("dialog",lambda d:asyncio.ensure_future(d.accept(pg.__dict__.get('_ans',''))))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(2500)
    await pg.fill("#tickers","NBIS,CRWV,BTC-USD"); await pg.fill("#names","Nebius,CoreWeave,Bitcoin")
    for t in THEMES:
      await pg.select_option("#theme",t)
      for typ in ["pct","bar"]:
        await pg.select_option("#type",typ); await pg.wait_for_timeout(2500)
        print(t,typ,await pg.inner_text("#status")); await shot(pg,f"theme_{t}_{typ}")
    # financials sample on dark + navy
    await pg.click("#mFin"); await pg.wait_for_timeout(4000)
    for t in ["dark","black","white"]:
      await pg.select_option("#theme",t); await pg.wait_for_timeout(3000)
      print("fin",t,await pg.inner_text("#status")); await shot(pg,f"theme_{t}_fin")
    # views lifecycle
    V=lambda:json.load(urllib.request.urlopen("http://localhost:18790/api/views"))
    await pg.select_option("#theme","navy"); await pg.select_option("#fmetric","fcf"); await pg.wait_for_timeout(3000)
    pg._ans="Test FCF navy"; await pg.click("#vsave"); await pg.wait_for_timeout(800)
    v=V(); print("saved:",[(x['name'],x['mode'],x['fields']['theme'],x['fields']['fmetric']) for x in v])
    await pg.click("#mPrices"); await pg.select_option("#theme","white"); await pg.wait_for_timeout(2500)
    await pg.select_option("#views",label="Test FCF navy"); await pg.wait_for_timeout(4000)
    print("reloaded mode/theme/metric:",await pg.evaluate("[MODE,$('theme').value,$('fmetric').value,T.bg]"), await pg.inner_text("#status"))
    await pg.select_option("#fperiod","annual"); await pg.select_option("#theme","black"); await pg.wait_for_timeout(2500)
    await pg.click("#vupd"); await pg.wait_for_timeout(800)
    print("updated:",[(x['name'],x['fields']['theme'],x['fields']['fperiod']) for x in V()])
    pg._ans="Renamed view"; await pg.click("#vren"); await pg.wait_for_timeout(800); print("renamed:",[x['name'] for x in V()])
    await pg.click("#vdel"); await pg.wait_for_timeout(800); print("deleted:",V())
    print("page errors:",errs); await b.close()
asyncio.run(main())
