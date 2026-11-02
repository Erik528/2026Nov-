"""Regression for one-click resize/download and a white gradient image bleed."""
from pathlib import Path
import runpy,sys
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
raw=runpy.run_path(str(ROOT/'tests/test_psd_import.py'))['bleed_psd']()
OUT=ROOT/'data/validation';OUT.mkdir(parents=True,exist_ok=True)
file=OUT/'portrait-gradient.psd';file.write_bytes(raw)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8000');page.wait_for_selector('.size')
 page.set_input_files('#file',str(file));page.wait_for_selector('#psdLayersPanel',state='visible')
 assert page.locator('.size:not(:disabled)').count()==4
 assert page.locator('#confirm').is_hidden()
 assert page.locator('#psdLayerRoles select').nth(2).input_value()=='graphics'
 assert page.locator('#psdLayerRoles select').nth(4).input_value()=='logo'
 for key in ['1080x1350','1080x1080','1080x1920','1200x628']:
  with page.expect_download(timeout=60000) as download:
   page.click(f'[data-key="{key}"]')
  path=OUT/f'auto-gradient-{key}.psd';download.value.save_as(str(path))
  with Image.open(path) as image:assert image.size==tuple(map(int,key.split('x')))
  assert page.locator('#safeReport.danger').count()==0
 page.locator('#layer').select_option(label='白色渐变')
 page.locator('#x').fill('-100');page.locator('#x').dispatch_event('input')
 page.wait_for_timeout(900)
 assert not page.locator('#export').is_disabled()
 assert page.locator('#safeReport.danger').count()==0
 page.screenshot(path=str(OUT/'gradient-bleed-allowed.png'),full_page=True)
 page.locator('#layer').select_option(label='Logo');page.locator('#x').fill('-100');page.locator('#x').dispatch_event('input')
 page.wait_for_selector('#safeReport.danger')
 assert 'Logo' in page.locator('#safeReport').inner_text()
 assert page.locator('#export').is_disabled()
 page.click('#reset');page.wait_for_function("document.querySelector('#export').disabled===false")
 page.reload();page.wait_for_selector('#psdLayersPanel',state='visible')
 assert page.locator('.size:not(:disabled)').count()==4
 assert not errors,errors
 browser.close()
print('One-click source-canvas resize/download passed for all 4 actual PSDs; white gradient bleed allowed, Logo bleed blocked, no confirm gate, reload and no JS errors verified.')
