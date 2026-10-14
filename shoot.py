import asyncio,base64
from playwright.async_api import async_playwright
CASES=[("line","BTC-USD","",""),("pct","NBIS,CRWV,BTC-USD","Nebius,CoreWeave,Bitcoin",""),
       ("bar","NBIS,CRWV,BTC-USD","Nebius,CoreWeave,Bitcoin","2026 return YTD"),("ratio","NBIS,BTC-USD","","Nebius priced in Bitcoin"),
       ("line","CRWV","CoreWeave","")]
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1100})
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    for i,(t,tk,nm,ti) in enumerate(CASES):
      await pg.fill("#tickers",tk); await pg.fill("#names",nm); await pg.fill("#title",ti)
      await pg.select_option("#type",t); await pg.evaluate("window.__render()"); await pg.wait_for_timeout(2500)
      print(t,tk,await pg.inner_text("#status"))
      url=await pg.evaluate("chart.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#fff'})")
      open(f"/workspace/chartmaker/samples/{i+1}_{t}_{tk.replace(',','_')}.png","wb").write(base64.b64decode(url.split(",")[1]))
    await b.close()
asyncio.run(main())
