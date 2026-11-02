"""Run against a live server. Uses local layer fixtures, NEVER fake API responses."""
import json
from pathlib import Path
import sys
import httpx
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import main

OUT=ROOT/'data'/'validation';OUT.mkdir(parents=True,exist_ok=True)
size=(640,480)
bg=Image.new('RGB',size,'#e6eadc')
text=Image.new('RGBA',size);d=ImageDraw.Draw(text);d.text((40,50),'LOCAL TEST / BRAND',fill='#263e26',font_size=28);d.text((40,95),'SUMMER SALE  $99',fill='#263e26',font_size=20)
graphic=Image.new('RGBA',size);d=ImageDraw.Draw(graphic);d.ellipse((280,150,550,430),fill='#56865a');d.rounded_rectangle((300,175,360,210),radius=5,fill='#ffffff');d.text((310,185),'LOGO',fill='#263e26')
original=bg.convert('RGBA');original.alpha_composite(graphic);original.alpha_composite(text)
original.convert('RGB').save(OUT/'constructed-fixture.jpg')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=1)
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8000');page.wait_for_selector('.size')
 page.screenshot(path=str(OUT/'empty.png'),full_page=True)
 page.set_input_files('#file',str(OUT/'constructed-fixture.jpg'))
 page.wait_for_function("document.querySelector('#sourceInfo').hidden === false")
 assert page.locator('#dimensions').inner_text().startswith('640 × 480')
 assert page.locator('.size:disabled').count()==4
 job=page.evaluate("localStorage.getItem('ad-atelier-job')")
 # Seed real local layer files for the editor test, not provider output.
 directory=main.DATA/job
 bg.save(directory/'clean.png');bg.resize((1280,960)).save(directory/'expanded.png')
 text.save(directory/'text.png');graphic.save(directory/'graphics.png');original.save(directory/'reconstructed.png')
 main.update(directory,status='review',stage='本地测试图层（未经 API 处理）',progress=100,
             quality={'warnings':['本图仅用于本地工程验证，不代表真实广告或 API 拆分结果。'],'reconstruction_mae':0})
 page.reload();page.wait_for_selector('#confirm',state='visible');page.click('#confirm')
 page.wait_for_selector('#editor',state='visible');page.wait_for_function("document.querySelector('#export').disabled === false")
 for key in ['1080x1080','1080x1350','1080x1920','1200x628']:
  page.click(f'[data-key="{key}"]')
  page.wait_for_function('(key)=>document.querySelector("#canvasTitle").textContent.startsWith(key.replace("x"," × "))',arg=key)
  page.wait_for_function("document.querySelector('#export').disabled === false")
  assert page.locator('#safeReport').inner_text().startswith('✓')
  page.click('#export');page.wait_for_selector(f'#downloads a[href$="{key}.psd"]')
  with page.expect_download() as dl: page.locator('#downloads a').first.click()
  dl.value.save_as(str(OUT/f'browser-{key}.psd'))
  with Image.open(OUT/f'browser-{key}.psd') as downloaded:
   assert downloaded.size==tuple(map(int,key.split('x'))),(key,downloaded.size)
  page.screenshot(path=str(OUT/f'{key}.png'),full_page=True)
 page.locator('#layer').select_option('text')
 page.locator('#x').fill('-500');page.locator('#x').dispatch_event('input')
 page.wait_for_function("document.querySelector('#safeReport').classList.contains('danger')")
 assert page.locator('#export').is_disabled()
 page.locator('#reset').click();page.wait_for_function("document.querySelector('#export').disabled === false")
 page.locator('#safeToggle').uncheck();assert not page.locator('#safeToggle').is_checked()
 # Drag the active layer and wait for the server-validated updated bounds.
 canvas=page.locator('#canvas');box=canvas.bounding_box();before=page.locator('#x').input_value()
 page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+8,box['y']+box['height']/2+4,steps=3);page.mouse.up()
 page.wait_for_timeout(700);assert page.locator('#x').input_value()!=before
 page.locator('#bgx').fill('0.25');page.locator('#bgx').dispatch_event('input');page.wait_for_timeout(700)
 assert page.locator('#bgx').input_value()=='0.25'
 before_scale=float(page.locator('#scale').input_value())
 page.locator('#scale').fill(str(round(before_scale*.95,1)));page.locator('#scale').dispatch_event('input');page.wait_for_timeout(700)
 assert float(page.locator('#scale').input_value())<before_scale
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
 assert not errors,errors
 browser.close()
print('Browser checks passed: upload, review gate, 4 formats and PSD downloads, safety block, reset, overlay, drag, background pan, mobile, no JS errors.')
