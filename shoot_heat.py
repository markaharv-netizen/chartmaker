import asyncio,base64
from playwright.async_api import async_playwright
CASES=[("sp100","1D",False,"white"),("mag7","1D",False,"white"),("sp100","1D",True,"black"),("custom","YTD",False,"dark")]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    pg.on("console",lambda m:print("console",m.text)); pg.on("pageerror",lambda e:print("ERR",e))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    await pg.evaluate("window.__setMode('heat')"); await pg.wait_for_timeout(4000)
    for i,(u,r,g,t) in enumerate(CASES):
      await pg.select_option("#theme",t); await pg.evaluate("applyTheme()")
      await pg.select_option("#huni",u); await pg.select_option("#hrange",r); await pg.set_checked("#hsector",g)
      await pg.evaluate("window.__hrender()"); await pg.wait_for_timeout(5000)
      print(u,r,g,await pg.inner_text("#status"))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
      open(f"/workspace/chartmaker-req/heat_{i+1}_{u}_{r}{'_sector' if g else ''}_{t}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await pg.evaluate("window.__setMode('prices')"); await pg.wait_for_timeout(3000); print("prices:",await pg.inner_text("#status"))
    await b.close()
asyncio.run(main())
