import asyncio,base64
from playwright.async_api import async_playwright
T14="ASST,HOGPF,SWC.L,MSTR,MARA,BLSH,ALCPB.PA,OBTC3.SA,XXI,3350.T,NAKA,DJT,ABTC,DDC"
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    pg.on("pageerror",lambda e:print("ERR",e))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    for t,tk,fn in [("bar",T14,"short_14_bar_ytd"),("pct","SWC.L,MSTR,XXI","short_pct_ytd")]:
      await pg.fill("#tickers",tk); await pg.select_option("#range","YTD"); await pg.select_option("#type",t)
      await pg.evaluate("window.__render()"); await pg.wait_for_timeout(9000); print(fn,await pg.inner_text("#status"))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
      open(f"/workspace/chartmaker-req/{fn}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await pg.evaluate("window.__setMode('heat')"); await pg.wait_for_timeout(2000)
    await pg.select_option("#huni","custom"); await pg.fill("#htickers",T14); await pg.select_option("#hrange","YTD")
    await pg.evaluate("window.__hrender()"); await pg.wait_for_timeout(9000); print("heat",await pg.inner_text("#status"))
    url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
    open("/workspace/chartmaker-req/short_heat_ytd.png","wb").write(base64.b64decode(url.split(",")[1]))
    await b.close()
asyncio.run(main())
