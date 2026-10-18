import asyncio
from playwright.async_api import async_playwright
OUT="/workspace/chartmaker-req/p-sentiment"
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path='/usr/bin/google-chrome'); pg=await b.new_page(viewport={"width":1800,"height":1200},accept_downloads=True)
    errs=[];pg.on("pageerror",lambda e:errs.append(str(e)));pg.on("console",lambda m:m.type=="error" and errs.append(m.text))
    await pg.goto("http://localhost:18795/"); await pg.wait_for_timeout(1500)
    await pg.click("#mSent"); await pg.wait_for_timeout(4000); print(await pg.inner_text("#status"))
    for t in ["white","dark"]:
      await pg.select_option("#theme",t); await pg.wait_for_timeout(1500)
      async with pg.expect_download() as d: await pg.click("#sdl")
      await (await d.value).save_as(f"{OUT}/sentiment_{t}.png")
    box=await pg.locator("#chart").bounding_box()
    await pg.mouse.move(box["x"]+1000,box["y"]+110+65*0.72+4); await pg.wait_for_timeout(400)
    await pg.screenshot(path=f"{OUT}/sentiment_hover.png",clip={"x":box["x"],"y":box["y"],"width":1600,"height":300})
    await pg.fill("#stickers","MSTR, TSLA, NVDA, ZZZ"); await pg.click("#sgo"); await pg.wait_for_timeout(1500); print(await pg.inner_text("#status"))
    await pg.screenshot(path=f"{OUT}/sentiment_filtered.png",clip={"x":box["x"],"y":box["y"],"width":1600,"height":900})
    print("ERRORS",errs); await b.close()
asyncio.run(main())
