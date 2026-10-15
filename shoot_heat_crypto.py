import asyncio,base64
from playwright.async_api import async_playwright
CASES=[("crypto50","1D","white"),("crypto50","7D","black"),("crypto100","1M","white"),("custom","1D","dark")]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    pg.on("pageerror",lambda e:print("ERR",e))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    await pg.evaluate("window.__setMode('heat')"); await pg.wait_for_timeout(3000)
    await pg.fill("#htickers","BTC,ETH,SOL,MSTR,COIN,IBIT,HOOD")
    for i,(u,r,t) in enumerate(CASES):
      await pg.select_option("#theme",t); await pg.evaluate("applyTheme()")
      await pg.select_option("#huni",u); await pg.select_option("#hrange",r)
      await pg.evaluate("window.__hrender()"); await pg.wait_for_timeout(6000)
      print(u,r,await pg.inner_text("#status"))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:T.bg})")
      open(f"/workspace/chartmaker-req/heatc_{i+1}_{u}_{r}_{t}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await b.close()
asyncio.run(main())
