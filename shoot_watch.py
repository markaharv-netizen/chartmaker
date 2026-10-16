import asyncio,base64
from playwright.async_api import async_playwright
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1200},accept_downloads=True)
    errs=[];pg.on("pageerror",lambda e:errs.append(str(e)));pg.on("console",lambda m:m.type=="error" and errs.append(m.text))
    await pg.goto("http://localhost:18790/"); await pg.wait_for_timeout(1500)
    await pg.click("#mWatch"); await pg.wait_for_timeout(6000); print(await pg.inner_text("#status"))
    await pg.fill("#wadd","ZZZZQ, 3350.T, eth"); await pg.click("#waddb"); await pg.wait_for_timeout(5000); print(await pg.evaluate("window.__wl()"))
    await pg.click("#wrefresh"); await pg.wait_for_timeout(6000); print(await pg.inner_text("#status"))
    await pg.click("a[data-mv='1,1']"); await pg.click("a[data-rm='6']"); print(await pg.evaluate("window.__wl()"))
    for t in ["white","dark","black","cream"]:
      await pg.select_option("#theme",t); await pg.wait_for_timeout(500)
      async with pg.expect_download() as d: await pg.click("#wdl")
      await (await d.value).save_as(f"/workspace/chartmaker-req/p-watchlist/watch_{t}.png")
    # sort by 1D via clicking header at canvas coords (chart scale 1)
    box=await pg.locator("#chart").bounding_box()
    await pg.mouse.click(box["x"]+945,box["y"]+130+20); await pg.wait_for_timeout(500)
    open("/workspace/chartmaker-req/p-watchlist/watch_sorted.png","wb").write(base64.b64decode((await pg.evaluate("chart.getDataURL({type:'png'})")).split(",")[1]))
    await pg.reload(); await pg.wait_for_timeout(1500); print("persisted",await pg.evaluate("window.__wl()"))
    await pg.click("#mWatch"); await pg.wait_for_timeout(5000)
    await pg.mouse.click(box["x"]+70,box["y"]+130+56*1.5+2); await pg.wait_for_timeout(4000)
    print("after click:",await pg.evaluate("MODE"),await pg.input_value("#tickers"),await pg.inner_text("#status"))
    print("ERRORS",errs); await b.close()
asyncio.run(main())
