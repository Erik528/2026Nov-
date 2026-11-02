"""Offline, deterministic regressions. Margin fixtures are NOT AI output claims."""
import io,json
from pathlib import Path
import numpy as np
import pytest
from PIL import Image,ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
from fastapi.testclient import TestClient
from app import main
from app.imaging import SIZES,default_layout,render
from app.psd_import import import_psd,catalog
from app.psd_workflow import apply_analysis,prepare,background_meta,build_background_source
from tests.psd_fixtures import prepared_geometry


def unknown_psd():
    p=PSDImage.new('RGBA',(400,400),color=(255,255,255,255))
    bg=Image.new('RGBA',(600,400),'#6281aa');d=ImageDraw.Draw(bg);d.rectangle((0,0,35,399),fill='red');d.rectangle((564,0,599,399),fill='lime')
    for name,im,xy in [('Layer 0',bg,(-100,0)),('Unnamed 1',Image.new('RGBA',(180,50),'black'),(30,50)),('Unnamed 2',Image.new('RGBA',(65,30),'#a62f55'),(260,310))]:
        layer=PixelLayer.frompil(im,p,left=xy[0],top=xy[1]);layer.name=name;p.append(layer)
    stream=io.BytesIO();p.save(stream);return stream.getvalue()


def known_roles(data):
    return {'layers':[{'id':i['id'],'category':role,'contains_text':role=='text','contains_logo':role=='logo','subject_box':None} for i,role in zip(data['layers'],['background','text','logo'])]}


@pytest.fixture
def prepared(tmp_path):
    data,_=import_psd(unknown_psd(),tmp_path);apply_analysis(tmp_path,known_roles(data));prepared_geometry(tmp_path)
    return tmp_path


def test_unknown_objects_get_visual_roles_not_name_guess(tmp_path):
    data,_=import_psd(unknown_psd(),tmp_path)
    assert data['layers'][1]['category']=='graphics'
    classified=apply_analysis(tmp_path,known_roles(data))
    assert classified['layers'][1]['category']=='text' and classified['layers'][1]['critical']
    assert classified['layers'][2]['category']=='logo' and classified['layers'][2]['critical']
    assert classified['analysis_verified']
    with pytest.raises(ValueError):apply_analysis(tmp_path,{'layers':[]})


@pytest.mark.parametrize('key',list(SIZES))
def test_background_full_outside_canvas_content_survives_all_formats(prepared,key):
    meta=background_meta(prepared)
    assert meta['native_box']==[-100,0,500,400]
    preview,placed,report=render(prepared,default_layout(prepared,key))
    assert not report['warnings']
    x,y,r,b=report['background_frame'];w,h=preview.size
    assert 0<=x<r<=w and 0<=y<b<=h
    bg=np.asarray(Image.open(prepared/f'background-view-{key}.png').convert('RGB'))
    assert ((bg[:,:,0]>210)&(bg[:,:,1]<50)&(bg[:,:,2]<50)).sum()>100
    assert ((bg[:,:,1]>210)&(bg[:,:,0]<50)&(bg[:,:,2]<50)).sum()>100
    assert any(p[0]=='AI background extension' for p in placed)


def test_no_false_green_for_unverified_layers_and_unexpanded_background(tmp_path):
    import_psd(unknown_psd(),tmp_path)
    report=render(tmp_path,default_layout(tmp_path,'1080x1080'))[2]
    assert not report['roles_verified'] and not report['background_expanded']
    assert len(report['warnings'])>=2


def test_cached_expansion_can_resume_without_any_provider(prepared):
    data=catalog(prepared);meta=background_meta(prepared);path=prepared/meta['file'];stamp=path.stat().st_mtime_ns
    result,new_meta=prepare(prepared,None,lambda stage,percent:None)
    assert new_meta['file']==meta['file'] and path.stat().st_mtime_ns==stamp
    assert new_meta['expanded_size']==[2*new_meta['source_size'][0],2*new_meta['source_size'][1]]


def test_whiteout_keeps_alpha_and_both_downloads_use_latest_edits(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);client=TestClient(main.app)
    job=client.post('/api/jobs',files={'file':('latest.psd',unknown_psd(),'application/octet-stream')}).json();root=tmp_path/job['id']
    data=catalog(root);apply_analysis(root,known_roles(data));prepared_geometry(root)
    base='/api/jobs/'+job['id'];layout=client.get(base+'/layout/1080x1080').json()
    assert client.post(base+'/preview',json=layout).status_code==200
    _,old_layers,_=render(root,layout)
    tr=layout['layers'][0];tr['whiteout']=True;tr['x']+=12
    expected,new_layers,report=render(root,layout);assert not report['warnings']
    old=next(i for name,i,_,_ in old_layers if name=='Unnamed 1');new=next(i for name,i,_,_ in new_layers if name=='Unnamed 1')
    assert np.array_equal(np.asarray(old.getchannel('A')),np.asarray(new.getchannel('A')))
    assert np.asarray(new)[:,:,:3].min()==255
    r=client.post(base+'/export-jpg',json=layout);assert r.status_code==200,r.text
    jpg=Image.open(io.BytesIO(client.get(r.json()['url']).content)).convert('RGB')
    assert jpg.size==(1080,1080)
    assert np.abs(np.asarray(jpg).astype(float)-np.asarray(expected.convert('RGB'))).mean()<2
    r=client.post(base+'/export',json=layout);assert r.status_code==200,r.text
    psd=PSDImage.open(io.BytesIO(client.get(r.json()['url']).content));layer=next(l for l in psd if l.name=='Unnamed 1')
    assert layer.left==round(tr['x']) and np.asarray(layer.topil().convert('RGBA'))[:,:,:3].min()==255
    cached=json.loads((root/'layout-1080x1080.json').read_text());assert cached['layers'][0]['whiteout'] is True


def test_upload_does_not_fake_preparation_without_api_key(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);client=TestClient(main.app)
    job=client.post('/api/jobs',files={'file':('need-api.psd',unknown_psd(),'application/octet-stream')}).json()
    assert not job['prepared'] and not job['confirmed']
    assert client.post('/api/jobs/'+job['id']+'/process').status_code==422
    assert client.post('/api/jobs/'+job['id']+'/generate-layout/1080x1080').status_code==409


def test_real_pixel_collision_is_not_reported_safe(prepared):
    layout=default_layout(prepared,'1080x1080');text,logo=layout['layers'];logo.update(x=text['x']+5,y=text['y']+5)
    warnings=render(prepared,layout)[2]['warnings']
    assert any('像素遮挡' in w for w in warnings)


def test_off_center_subject_can_be_centered_without_cropping():
    from app.psd_workflow import center_subject,frame_geometry
    meta={'native_box':[0,0,1000,1000],'subject_box':[.8,.75,.98,.98]}
    bg=center_subject(meta,(1080,1920),{'x':.5,'y':.5,'zoom':3})
    frame,_=frame_geometry(meta,(1080,1920),bg);x,y,r,b=frame
    assert 0<=x<r<=1080 and 0<=y<b<=1920
    assert .35*1080-1<=x+.89*(r-x)<=.65*1080+1
    assert .35*1920-1<=y+.865*(b-y)<=.65*1920+1
