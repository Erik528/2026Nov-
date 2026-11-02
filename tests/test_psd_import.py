"""Real PSD encode/decode fixtures; no simulated image processing or API calls."""
import io
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image,ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer,Group
from psd_tools.constants import BlendMode

from app import main
from app.imaging import SIZES,default_layout,render,write_psd
from app.psd_import import import_psd,catalog,normalize_ai_layout


def layered_psd():
    psd=PSDImage.new('RGBA',(640,480),color=(255,255,255,255))
    def add(parent,name,size,xy,color,opacity=255):
        im=Image.new('RGBA',size)
        ImageDraw.Draw(im).rounded_rectangle((2,2,size[0]-2,size[1]-2),radius=8,fill=color)
        layer=PixelLayer.frompil(im,psd,left=xy[0],top=xy[1]);layer.name=name;layer.opacity=opacity;parent.append(layer)
        return layer
    add(psd,'背景',(640,480),(0,0),'#dfeada')
    add(psd,'标题',(230,55),(40,45),'#22332a')
    add(psd,'Logo',(80,35),(45,130),'#ee3344')
    add(psd,'Product',(170,240),(340,200),'#67946d',opacity=160)
    group=Group.new('People',parent=psd);group.name='人物组'
    add(group,'Inner portrait',(50,100),(260,250),'#dd7722')
    hidden=add(psd,'Hidden reference',(100,100),(100,100),'red');hidden.visible=False
    out=io.BytesIO();psd.save(out);return out.getvalue()


@pytest.fixture
def imported(tmp_path):
    data,quality=import_psd(layered_psd(),tmp_path)
    assert quality['reconstruction_mae']<1
    return tmp_path,data


def test_import_retains_group_metadata_names_alpha_and_original(imported):
    directory,data=imported
    assert [i['name'] for i in data['layers']]==['背景','标题','Logo','Product','人物组']
    assert data['tree'][-1]['visible'] is False
    assert data['layers'][-1]['source']['children'][0]['name']=='Inner portrait'
    product=data['layers'][3]
    assert product['source']['opacity']==160
    with Image.open(directory/product['file']) as image:assert image.getchannel('A').getextrema()[1]==160
    assert (directory/'source.psd').read_bytes()==layered_psd()
    assert data['layers'][0]['category']=='background'


@pytest.mark.parametrize('key',list(SIZES))
def test_multi_layer_resize_export_all_sizes(imported,key):
    directory,data=imported
    layout=default_layout(directory,key)
    assert len(layout['layers'])==4
    preview,placed,report=render(directory,layout)
    assert report['warnings']==[]
    out=directory/f'{key}.psd';check=write_psd(out,preview.size,placed,preview)
    assert check['verified']
    assert check['layers']==['Canvas','背景','标题','Logo','Product','人物组']
    opened=PSDImage.open(out)
    assert opened.size==(SIZES[key]['width'],SIZES[key]['height'])
    assert opened[4].topil().getchannel('A').getextrema()[1]==160


def test_per_object_safety_and_noncritical_decoration(imported):
    directory,data=imported
    layout=default_layout(directory,'1080x1080')
    layout['layers'][0]['x']=-200
    assert '标题' in render(directory,layout)[2]['warnings'][0]
    data['layers'][1]['category']='decoration';data['layers'][1]['critical']=False
    (directory/'psd-layers.json').write_text(json.dumps(data))
    assert render(directory,layout)[2]['warnings']==[]


def test_ai_coordinates_are_checked_and_clamped_without_api(imported):
    directory,data=imported
    proposal=default_layout(directory,'1080x1920');proposal['layers'][0].update(x=-300,y=0,scale=7)
    normalized=normalize_ai_layout(directory,proposal,'1080x1920')
    assert not render(directory,normalized)[2]['warnings']
    proposal['layers'][0]['id']='invented-layer'
    with pytest.raises(ValueError):normalize_ai_layout(directory,proposal,'1080x1920')


def test_psd_http_without_api_key_and_roles_invalidation(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.delenv('AD_OPENAI_API_KEY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    client=TestClient(main.app)
    result=client.post('/api/jobs',files={'file':('real-layered.psd',layered_psd(),'image/vnd.adobe.photoshop')})
    assert result.status_code==200,result.text
    job=result.json();jid=job['id']
    assert job['source_type']=='psd' and job['status']=='review'
    assert len(job['psd_layers'])==5
    assert client.post(f'/api/jobs/{jid}/process').status_code==409
    assert client.post(f'/api/jobs/{jid}/confirm').status_code==200
    for key in SIZES:
        layout=client.get(f'/api/jobs/{jid}/layout/{key}').json()
        assert client.post(f'/api/jobs/{jid}/preview',json=layout).status_code==200
        res=client.post(f'/api/jobs/{jid}/export',json=layout)
        assert res.status_code==200,res.text
        assert len(res.json()['validation']['layers'])==6
    assert client.post(f'/api/jobs/{jid}/ai-layout/1080x1080').status_code==422
    cached=default_layout(tmp_path/jid,'1080x1080')
    (tmp_path/jid/'ai-layout-1080x1080.json').write_text(json.dumps(cached))
    changed=json.loads(json.dumps(cached));changed['layers'][0]['x']+=3
    (tmp_path/jid/'layout-1080x1080.json').write_text(json.dumps(changed))
    assert client.post(f'/api/jobs/{jid}/ai-layout/1080x1080').json()==cached
    assert client.get(f'/api/jobs/{jid}/layout/1080x1080').json()==cached
    roles=[{'id':i['id'],'category':i['category']} for i in job['psd_layers']];roles[1]['category']='decoration'
    assert client.post(f'/api/jobs/{jid}/psd-roles',json={'layers':roles}).status_code==200
    assert client.get(f'/api/jobs/{jid}/layout/1080x1080').status_code==200
    assert not list((tmp_path/jid).glob('layout-*.json'))
    assert not list((tmp_path/jid).glob('real-layered-*.psd'))
    assert client.get(f'/api/jobs/{jid}/assets/source.psd').content==layered_psd()
    assert client.get(f'/api/jobs/{jid}/assets/psd-layers.json').status_code==200
    assert client.get(f'/api/jobs/{jid}/assets/layer-001.png').status_code==200
    assert client.get(f'/api/jobs/{jid}/assets/job.json').status_code==404


def test_invalid_and_flat_psd_are_rejected_without_orphan_jobs(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);client=TestClient(main.app)
    for raw in [b'corrupt',b'8BPS\x00\x02'+b'\x00'*100]:
        result=client.post('/api/jobs',files={'file':('bad.psd',raw,'application/octet-stream')})
        assert result.status_code==400
        assert not list(tmp_path.iterdir())
    flat=PSDImage.new('RGBA',(640,480));out=io.BytesIO();flat.save(out)
    assert client.post('/api/jobs',files={'file':('flat.psd',out.getvalue(),'application/octet-stream')}).status_code==400


def test_top_level_blending_is_not_silently_flattened(tmp_path):
    psd=PSDImage.open(io.BytesIO(layered_psd()));psd[1].blend_mode=BlendMode.MULTIPLY
    out=io.BytesIO();psd.save(out)
    with pytest.raises(ValueError,match='混合模式'):import_psd(out.getvalue(),tmp_path)


def test_small_foreground_auto_layout_respects_scale_limit(tmp_path):
    psd=PSDImage.new('RGBA',(400,400),color=(255,255,255,255))
    for name,size,xy in [('Background',(400,400),(0,0)),('Title',(20,20),(180,180))]:
        layer=PixelLayer.frompil(Image.new('RGBA',size,'white' if name=='Background' else 'black'),psd,left=xy[0],top=xy[1]);layer.name=name;psd.append(layer)
    out=io.BytesIO();psd.save(out);import_psd(out.getvalue(),tmp_path)
    for key in SIZES:
        layout=default_layout(tmp_path,key)
        assert layout['layers'][0]['scale']<=10
        assert render(tmp_path,layout)[2]['warnings']==[]


def bleed_psd():
    """A portrait ad with text/logo and a large white gradient image overlay."""
    psd=PSDImage.new('RGBA',(1080,1350),color=(255,255,255,255))
    def add(name,image,xy):
        layer=PixelLayer.frompil(image,psd,left=xy[0],top=xy[1]);layer.name=name;psd.append(layer)
    add('Background',Image.new('RGBA',(1080,1350),'#d5a99a'),(0,0))
    add('照片',Image.new('RGBA',(650,760),'#53745e'),(250,350))
    overlay=Image.new('RGBA',(1000,700),'white')
    a=Image.linear_gradient('L').resize(overlay.size);overlay.putalpha(a)
    add('白色渐变',overlay,(20,650))
    add('标题文字',Image.new('RGBA',(500,160),'#004499'),(80,280))
    add('Logo',Image.new('RGBA',(140,55),'#dd3333'),(760,1020))
    out=io.BytesIO();psd.save(out);return out.getvalue()


@pytest.mark.parametrize('key',list(SIZES))
def test_resize_uses_original_canvas_and_only_text_logo_are_safe(imported,tmp_path,key):
    directory=tmp_path/'portrait';directory.mkdir()
    data,_=import_psd(bleed_psd(),directory)
    layout=default_layout(directory,key);by_id={i['id']:i for i in data['layers']}
    w,h=SIZES[key]['width'],SIZES[key]['height'];scale=max(w/1080,h/1350)
    dx,dy=(w-1080*scale)/2,(h-1350*scale)/2
    for tr in layout['layers']:
        info=by_id[tr['id']]
        if info['category']=='graphics':
            assert not info['critical']
            assert tr['scale']==scale
            assert (tr['x'],tr['y'])==(round(dx+info['box'][0]*scale),round(dy+info['box'][1]*scale))
    preview,placed,report=render(directory,layout)
    assert not report['warnings']
    gradient=next(i for i in data['layers'] if i['name']=='白色渐变')
    bound=next(b for b in report['bounds'] if b['id']==gradient['id'])
    assert bound['outside'] and not bound['critical']
    if key=='1080x1350':
        tr=next(tr for tr in layout['layers'] if tr['id']==gradient['id'])
        assert tr['scale']==1
        assert (tr['x'],tr['y'])==tuple(gradient['box'][:2])
    write_psd(directory/f'bleed-{key}.psd',preview.size,placed,preview)


def test_photo_and_gradient_can_bleed_but_text_and_logo_block_export(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);client=TestClient(main.app)
    job=client.post('/api/jobs',files={'file':('portrait.psd',bleed_psd(),'application/octet-stream')}).json()
    assert job['confirmed'] is True
    jid=job['id'];base=f'/api/jobs/{jid}'
    layout=client.get(base+'/layout/1080x1350').json()
    info={i['name']:i['id'] for i in layout['layer_info']}
    next(t for t in layout['layers'] if t['id']==info['白色渐变'])['x']=-100
    assert client.post(base+'/export',json=layout).status_code==200
    for name in ['标题文字','Logo']:
        changed=json.loads(json.dumps(layout));next(t for t in changed['layers'] if t['id']==info[name])['x']=-100
        report=client.post(base+'/preview',json=changed).json()
        assert any(name in w for w in report['warnings'])
        assert client.post(base+'/export',json=changed).status_code==422


def test_old_imports_and_layouts_migrate_without_losing_source(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);client=TestClient(main.app)
    job=client.post('/api/jobs',files={'file':('legacy.psd',bleed_psd(),'application/octet-stream')}).json()
    directory=tmp_path/job['id'];base=f'/api/jobs/{job["id"]}'
    old=catalog(directory);old.pop('resize_policy')
    for i in old['layers']:
        if i['category']=='logo':i['category']='graphics'
        i['critical']=i['category'] in {'text','graphics'}
    (directory/'psd-layers.json').write_text(json.dumps(old))
    previous=default_layout(directory,'1080x1350');previous.pop('resize_policy');previous['layers'][0]['x']=999
    (directory/'psd-layers.json').write_text(json.dumps(old))
    (directory/'layout-1080x1350.json').write_text(json.dumps(previous))
    main.update(directory,confirmed=False,psd_layers=old['layers'],resize_policy=None)
    migrated=client.get(base).json()
    assert migrated['confirmed']
    assert next(i for i in migrated['psd_layers'] if i['name']=='Logo')['category']=='logo'
    assert not next(i for i in migrated['psd_layers'] if i['name']=='白色渐变')['critical']
    layout=client.get(base+'/layout/1080x1350').json()
    assert layout['layers'][0]['x']!=999
    assert client.post(base+'/preview',json=layout).status_code==200
    assert (directory/'layout-1080x1350.before-resize-v2.json').exists()
    assert (directory/'psd-layers.before-resize-v2.json').exists()
    assert (directory/'source.psd').read_bytes()==bleed_psd()


def test_gradient_copy_is_an_image_not_copy_text(tmp_path):
    psd=PSDImage.open(io.BytesIO(bleed_psd()));psd[2].name='White gradient copy'
    out=io.BytesIO();psd.save(out)
    data,_=import_psd(out.getvalue(),tmp_path)
    gradient=next(i for i in data['layers'] if i['name']=='White gradient copy')
    assert gradient['category']=='graphics' and not gradient['critical']
