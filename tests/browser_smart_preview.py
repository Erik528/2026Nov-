"""Offline browser editor regression; geometry fixtures are explicitly not AI results.

A separate live API run verifies preparation/model calls. This test seeds ONLY
known local layer/background/layout artifacts and never invokes external APIs.
"""
import io,json,sys,uuid
from pathlib import Path
from PIL import Image
import numpy as np
from psd_tools import PSDImage
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import main
from app.psd_import import import_psd,catalog,RESIZE_POLICY
from app.imaging import SIZES,default_layout,render
from tests.test_smart_workflow import unknown_psd,known_roles
from tests.psd_fixtures import prepared_geometry
from app.psd_workflow import apply_analysis

OUT=ROOT/'data/validation';OUT.mkdir(exist_ok=True,parents=True)
job=uuid.uuid4().hex;directory=main.DATA/job;directory.mkdir()
data,quality=import_psd(unknown_psd(),directory);apply_analysis(directory,known_roles(data));data,meta=prepared_geometry(directory)
(directory/'job.json').write_text(json.dumps({'id':job,'name':'editor-fixture','filename':'editor-fixture.psd','source_type':'psd','width':400,'height':400,'status':'review','stage':'离线几何测试夹具（非 AI 生成）','progress':100,'confirmed':True,'prepared':True,'psd_layers':data['layers'],'background_meta':meta,'resize_policy':RESIZE_POLICY,'quality':quality}))
for key in SIZES:
 layout=default_layout(directory,key);layout['layout_notice']='离线编辑器几何夹具；不代表 AI 结果。';(directory/f'layout-{key}.json').write_text(json.dumps(layout))
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1200});page.set_default_timeout(60000)
 errors=[];downloads=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('download',lambda d:downloads.append(d.suggested_filename))
 page.add_init_script(f"localStorage.setItem('ad-atelier-job','{job}')")
 page.goto('http://127.0.0.1:8000');page.wait_for_selector('#psdLayersPanel',state='visible')
 for key in SIZES:
  before_downloads=len(downloads)
  page.click(f'[data-key="{key}"]');page.wait_for_function("!document.querySelector('#export').disabled")
  assert page.locator('#canvasTitle').inner_text().startswith(key.replace('x',' × '))
  page.wait_for_timeout(250);assert len(downloads)==before_downloads,'Format selection unexpectedly started a download'
  for button,ext in [('exportJpg','jpg'),('export','psd')]:
   with page.expect_download() as d:page.click('#'+button)
   file=OUT/f'preview-editor-{key}.{ext}';d.value.save_as(str(file))
   with Image.open(file) as im:assert im.size==tuple(map(int,key.split('x')))
 page.click('[data-key="1080x1080"]');page.wait_for_function("!document.querySelector('#export').disabled")
 page.locator('#canvas').scroll_into_view_if_needed()
 def screen(x,y):
  b=page.locator('#canvas').bounding_box();return b['x']+x*b['width']/1080,b['y']+y*b['height']/1080
 box=page.evaluate("bounds('psd-001')")
 x,y=screen((box[0]+box[2])/2,(box[1]+box[3])/2)
 page.mouse.click(x,y);assert page.locator('#layer').input_value()=='psd-001'
 before=float(page.locator('#x').input_value());page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+10,y+8,steps=3);page.mouse.up();page.wait_for_function("!document.querySelector('#export').disabled")
 assert float(page.locator('#x').input_value())>before
 box=page.evaluate("bounds('psd-001')");hx,hy=screen(box[2],box[3]);scale=float(page.locator('#scale').input_value())
 page.mouse.move(hx,hy);page.mouse.down();page.mouse.move(hx+8,hy+4,steps=3);page.mouse.up();page.wait_for_function("!document.querySelector('#export').disabled")
 assert float(page.locator('#scale').input_value())>scale
 page.click('#canvasWhiteout');page.wait_for_function("!document.querySelector('#export').disabled")
 assert page.locator('#whiteout').is_checked()
 page.locator('#safeToggle').uncheck()
 latest=page.evaluate('structuredClone(layout)');expected,_,_=render(directory,latest)
 for button,ext in [('exportJpg','jpg'),('export','psd')]:
  with page.expect_download() as d:page.click('#'+button)
  path=OUT/f'latest-manual-whiteout.{ext}';d.value.save_as(str(path))
  if ext=='jpg':
   actual=Image.open(path).convert('RGB');assert np.abs(np.asarray(actual).astype(float)-np.asarray(expected.convert('RGB'))).mean()<2
  else:
   psd=PSDImage.open(path);title=next(l for l in psd if l.name=='Unnamed 1');assert title.left==round(latest['layers'][0]['x']);assert np.asarray(title.topil().convert('RGBA'))[:,:,:3].min()==255
 page.screenshot(path=str(OUT/'smart-editor-whiteout.png'),full_page=True)
 page.locator('#layer').select_option('psd-002');old=page.locator('#x').input_value();page.locator('#x').fill('-100');page.locator('#x').dispatch_event('input');page.wait_for_selector('#safeReport.danger')
 assert page.locator('#export').is_disabled() and page.locator('#exportJpg').is_disabled()
 page.locator('#x').fill(old);page.locator('#x').dispatch_event('input');page.wait_for_function("!document.querySelector('#export').disabled")
 page.reload();page.wait_for_selector('#psdLayersPanel',state='visible');page.click('[data-key="1080x1080"]');page.wait_for_function("!document.querySelector('#export').disabled")
 page.locator('#layer').select_option('psd-001');assert page.locator('#whiteout').is_checked()
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'smart-editor-mobile.png'),full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 assert not errors,errors
 browser.close()
print('Browser verified: format=preview only; 4 JPG+PSD sizes, click-select, drag, corner scale, whiteout, latest-edit downloads, no overlay export, both safety gates, edit persistence and mobile. Offline geometry fixtures, no API calls.')
