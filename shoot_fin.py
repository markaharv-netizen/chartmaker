import asyncio,base64
from playwright.async_api import async_playwright
CASES=[("tsla_revenue_quarterly","TSLA","","revenue","quarterly","2021","line",""),
 ("nvda_amd_intc_netincome_annual","NVDA,AMD,INTC","Nvidia,AMD,Intel","net_income","annual","2019","bars",""),
 ("hyperscaler_capex_ttm_rank","MSFT,GOOGL,AMZN,META","Microsoft,Alphabet,Amazon,Meta","capex","ttm","2024","rank","Big Tech capex, trailing 12 months"),
 ("mstr_shares","MSTR","Strategy","shares","quarterly","2020","line","")]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1400})
    pg.on("console",lambda m:print("console:",m.text))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500); await pg.evaluate("window.__setMode('fin')"); await pg.wait_for_timeout(3000)
    for n,tk,nm,m,per,y1,t,ti in CASES:
      await pg.fill("#ftickers",tk); await pg.fill("#fnames",nm); await pg.fill("#ftitle",ti); await pg.fill("#fy1",y1)
      for sel,v in (("#fmetric",m),("#fperiod",per),("#ftype",t)): await pg.eval_on_selector(sel,f"e=>e.value='{v}'")
      await pg.evaluate("window.__frender()"); await pg.wait_for_timeout(4000)
      print(n,"|",await pg.inner_text("#status"),"|",await pg.inner_text("#fnote"))
      print((await pg.inner_text("#ftable"))[:400].replace("\n"," / "))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'})")
      open(f"samples/fin_{n}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await pg.screenshot(path="/tmp/ui.png",full_page=True)
    await b.close()
asyncio.run(main())
