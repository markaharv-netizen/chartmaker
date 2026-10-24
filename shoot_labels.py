import asyncio,base64
from playwright.async_api import async_playwright
T14="ASST,HOGPF,SWC.L,MSTR,MARA,BLSH,ALCPB.PA,OBTC3.SA,XXI,3350.T,NAKA,DJT,ABTC,DDC"
T6="NVDA,AMD,INTC,TSM,AVGO,QCOM"
T25="AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,BRK-B,JPM,LLY,V,WMT,ORCL,MA,XOM,NFLX,COST,JNJ,HD,PLTR,ABBV,BAC,PG,KO"
CASES=[("bar",T14,"labels_14_bar_ytd"),("bardd",T14,"labels_14_dd_ytd"),("bar",T6,"labels_6_bar_ytd"),("bar",T25,"labels_25_bar_ytd")]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    pg.on("pageerror",lambda e:print("ERR",e))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    await pg.select_option("#theme","cream"); await pg.evaluate("applyTheme()")
    for t,tk,fn in CASES:
      await pg.fill("#tickers",tk); await pg.select_option("#range","YTD"); await pg.select_option("#type",t)
      await pg.evaluate("window.__render()"); await pg.wait_for_timeout(9000)
      print(fn,await pg.inner_text("#status"))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
      open(f"/workspace/chartmaker-req/{fn}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await b.close()
asyncio.run(main())
