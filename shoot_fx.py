import asyncio,base64,json
from playwright.async_api import async_playwright
T14="ASST,HOGPF,SWC.L,MSTR,MARA,BLSH,ALCPB.PA,OBTC3.SA,XXI,3350.T,NAKA,DJT,ABTC,DDC"
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    pg.on("pageerror",lambda e:print("ERR",e))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    for rng in ["YTD","1M"]:
      await pg.evaluate("window.__setMode('prices')"); await pg.fill("#tickers",T14); await pg.select_option("#range",rng); await pg.select_option("#type","bar")
      await pg.evaluate("window.__render()"); await pg.wait_for_timeout(9000)
      bar=await pg.evaluate("Object.fromEntries(last.opt.xAxis.data.map((l,i)=>[l.replace(/\\n/g,' '),last.opt.series[0].data[i].value]))")
      if rng=="YTD":
        open("/workspace/chartmaker-req/fx_bar_ytd.png","wb").write(base64.b64decode((await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")).split(",")[1]))
      await pg.evaluate("window.__setMode('heat')"); await pg.wait_for_timeout(500)
      await pg.select_option("#huni","custom"); await pg.fill("#htickers",T14); await pg.select_option("#hrange",rng)
      await pg.evaluate("window.__hrender()"); await pg.wait_for_timeout(9000); print("heat",rng,await pg.inner_text("#status"))
      heat=await pg.evaluate("Object.fromEntries(hlast.rows.map(d=>[d.symbol,+d.chg.toFixed(2)]))")
      for l,v in bar.items():
        import re; m=re.search(r"\(([^)]+)\)",l); tk=m.group(1) if m else l.strip('*')
        print(rng,tk,v,heat.get(tk),"OK" if heat.get(tk) is not None and abs(heat[tk]-v)<0.06 else "DIFF")
      open(f"/workspace/chartmaker-req/fx_heat_{rng}.png","wb").write(base64.b64decode((await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")).split(",")[1]))
    await b.close()
asyncio.run(main())
