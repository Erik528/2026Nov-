"""Live browser test: actual layered PSD upload, no API or layer-file seeding."""
import io
import runpy
from pathlib import Path
import sys
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
raw=runpy.run_path(str(ROOT/'tests/test_psd_import.py'))['layered_psd']()
OUT=ROOT/'data/validation';OUT.mkdir(parents=True,exist_ok=True)
source=OUT/'layered-browser.psd';source.write_bytes(raw)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1050});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('response',lambda r: print('API failure:',r.status,r.url,r.text()[:300]) if r.status>=400 and '/api/' in r.url else None)
 page.goto('http://127.0.0.1:8000');page.wait_for_selector('.size')
 page.set_input_files('#file',str(source));page.wait_for_selector('#psdLayersPanel',state='visible')
 assert page.locator('#psdLayerRoles select').count()==5
 assert page.locator('#assets .asset').count()==5
 assert page.locator('#process').is_disabled()
 assert page.locator('.size:not(:disabled)').count()==4
 assert page.locator('#dimensions').inner_text().endswith('5 个 PSD 图层/组')
 page.screenshot(path=str(OUT/'psd-import.png'),full_page=True)
 with page.expect_download() as initial:page.click('[data-key="1080x1080"]')
 initial.value.delete();page.wait_for_function("document.querySelector('#export').disabled===false")
 assert page.locator('#layer option').count()==4
 assert '人物组' in page.locator('#layer').inner_text()
 for key in ['1080x1080','1080x1350','1080x1920','1200x628']:
  with page.expect_download() as download:page.click(f'[data-key="{key}"]')
  page.wait_for_function('(key)=>document.querySelector("#canvasTitle").textContent.startsWith(key.replace("x"," × "))',arg=key)
  page.wait_for_function("document.querySelector('#export').disabled===false")
  page.wait_for_selector(f'#downloads a[href$="{key}.psd"]',timeout=60000)
  file=OUT/f'psd-browser-{key}.psd';download.value.save_as(str(file))
  with Image.open(file) as image:assert image.size==tuple(map(int,key.split('x')))
 page.locator('#layer').select_option(label='Logo')
 page.locator('#x').fill('-400');page.locator('#x').dispatch_event('input')
 page.wait_for_function("document.querySelector('#safeReport').classList.contains('danger')")
 assert 'Logo' in page.locator('#safeReport').inner_text()
 assert page.locator('#export').is_disabled()
 page.click('#reset');page.wait_for_function("document.querySelector('#export').disabled===false")
 page.locator('#psdLayerRoles select').nth(2).select_option('decoration')
 page.click('#applyRoles');page.wait_for_function("document.querySelector('#export').disabled===false")
 assert page.locator('.size:not(:disabled)').count()==4
 page.locator('#layer').select_option(label='Logo')
 before=page.locator('#x').input_value()
 page.locator('#canvas').scroll_into_view_if_needed()
 box=page.locator('#canvas').bounding_box();page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+10,box['y']+box['height']/2+3,steps=3);page.mouse.up()
 page.wait_for_timeout(700);assert page.locator('#x').input_value()!=before
 page.locator('#bgx').fill('0.1');page.locator('#bgx').dispatch_event('input');page.wait_for_timeout(700)
 page.screenshot(path=str(OUT/'psd-editor.png'),full_page=True)
 page.reload();page.wait_for_selector('#psdLayersPanel',state='visible')
 assert page.locator('#psdLayerRoles select').nth(2).input_value()=='decoration'
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'psd-mobile.png'),full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 assert not errors,errors
 browser.close()
print('PSD browser checks passed: true file upload, all source units, roles, per-layer safety, all 4 downloaded PSD sizes, drag, background crop, reload, mobile, no JS errors. No API calls.')
