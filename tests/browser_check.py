"""JPG editor regression using explicit local layer fixtures, not API results."""
from pathlib import Path
import sys
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import main
OUT=ROOT/'data/validation';OUT.mkdir(parents=True,exist_ok=True)
bg=Image.new('RGB',(640,480),'#e6eadc');text=Image.new('RGBA',bg.size);d=ImageDraw.Draw(text);d.text((40,50),'LOCAL TEST / BRAND',fill='#263e26',font_size=28)
graphic=Image.new('RGBA',bg.size);d=ImageDraw.Draw(graphic);d.ellipse((280,150,550,430),fill='#56865a')
original=bg.convert('RGBA');original.alpha_composite(graphic);original.alpha_composite(text);file=OUT/'constructed-fixture.jpg';original.convert('RGB').save(file)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1200});page.set_default_timeout(60000)
 errors=[];events=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('download',lambda d:events.append(d.suggested_filename))
 page.goto('http://127.0.0.1:8000');page.wait_for_selector('.size');page.set_input_files('#file',str(file));page.wait_for_selector('#sourceInfo',state='visible')
 assert page.locator('.size:disabled').count()==4
 job=page.evaluate("localStorage.getItem('ad-atelier-job')");directory=main.DATA/job
 bg.save(directory/'clean.png');bg.resize((1280,960)).save(directory/'expanded.png');text.save(directory/'text.png');graphic.save(directory/'graphics.png');original.save(directory/'reconstructed.png')
 main.update(directory,status='review',stage='本地 JPG 图层夹具，非 AI 结果',progress=100,quality={'warnings':['离线编辑器工程测试'],'reconstruction_mae':0})
 page.reload();page.wait_for_selector('#confirm',state='visible');page.click('#confirm');page.wait_for_function("!document.querySelector('#export').disabled")
 for key in ['1080x1080','1080x1350','1080x1920','1200x628']:
  before=len(events);page.click(f'[data-key="{key}"]');page.wait_for_function("!document.querySelector('#export').disabled");assert len(events)==before
  for button,ext in [('exportJpg','jpg'),('export','psd')]:
   with page.expect_download() as download:page.click('#'+button)
   out=OUT/f'jpg-workflow-{key}.{ext}';download.value.save_as(str(out))
   with Image.open(out) as image:assert image.size==tuple(map(int,key.split('x')))
 page.locator('#layer').select_option('text');page.locator('#x').fill('-500');page.locator('#x').dispatch_event('input');page.wait_for_selector('#safeReport.danger')
 assert page.locator('#export').is_disabled() and page.locator('#exportJpg').is_disabled()
 page.click('#reset');page.wait_for_function("!document.querySelector('#export').disabled")
 page.locator('#layer').select_option('text');page.locator('#whiteout').check();page.wait_for_function("!document.querySelector('#export').disabled")
 page.locator('#bgx').fill('0.25');page.locator('#bgx').dispatch_event('input');page.wait_for_function("!document.querySelector('#export').disabled")
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 assert not errors,errors;browser.close()
print('JPG browser verified: upload/review gate, preview-only size selection, 4 JPG+PSD downloads, safety block, whiteout, background pan and mobile. Offline layer fixtures, no API success simulated.')
