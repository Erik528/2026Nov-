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
    assert client.get(f'/api/jobs/{jid}/layout/1080x1080').status_code==409
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
