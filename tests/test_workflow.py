"""Offline engineering checks. These do NOT simulate or certify image API quality."""
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from psd_tools import PSDImage

from app import main
from app.imaging import SIZES, read_upload, extract, default_layout, render, write_psd
from app.provider import OpenAIImages, ProviderError


def jpg(image):
    out=io.BytesIO(); image.convert('RGB').save(out,'JPEG',quality=95); return out.getvalue()


@pytest.fixture
def layers(tmp_path):
    # Constructed fixture, explicitly not a real advertisement or AI output.
    size=(640,480); bg=Image.new('RGB',size,'#e6eadc')
    text=Image.new('RGBA',size); d=ImageDraw.Draw(text);d.text((50,60),'TEST BRAND $99',fill='#123428',stroke_width=1)
    graphic=Image.new('RGBA',size);d=ImageDraw.Draw(graphic);d.ellipse((300,140,560,430),fill='#56865a');d.rectangle((320,160,350,200),fill='#ffffff80')
    for name,im in [('clean',bg),('text',text),('graphics',graphic)]: im.save(tmp_path/f'{name}.png')
    bg.resize((1280,960)).save(tmp_path/'expanded.png')
    return tmp_path


@pytest.mark.parametrize('key',list(SIZES))
def test_all_size_psd_roundtrips(layers,key):
    layout=default_layout(layers,key)
    preview,placed,report=render(layers,layout)
    assert not report['warnings']
    out=layers/f'{key}.psd'
    validation=write_psd(out,preview.size,placed,preview)
    assert validation['verified']
    psd=PSDImage.open(out)
    assert psd.size==(SIZES[key]['width'],SIZES[key]['height'])
    assert [x.name for x in psd]==['Background','Graphics','Text (raster)']
    assert psd[1].topil().getchannel('A').getextrema()[0] < 255
    assert psd[2].topil().getchannel('A').getextrema()[0] == 0
    assert validation['composite_mae']<1


def test_safety_and_invalid_transforms(layers):
    layout=default_layout(layers,'1080x1920')
    layout['layers'][1]['x']=-25
    assert '文字' in render(layers,layout)[2]['warnings'][0]
    layout['layers'][0]['scale']=float('nan')
    with pytest.raises(ValueError): render(layers,layout)


def test_upload_exif_limits_and_wrong_format():
    im=Image.new('RGB',(400,300)); exif=Image.Exif();exif[274]=6
    out=io.BytesIO();im.save(out,'JPEG',exif=exif)
    assert read_upload(out.getvalue()).size==(300,400)
    for raw in [b'not a jpeg',jpg(Image.new('RGB',(100,100)))]:
        with pytest.raises(ValueError): read_upload(raw)
    out=io.BytesIO();im.save(out,'PNG')
    with pytest.raises(ValueError,match='JPG'):read_upload(out.getvalue())


def test_extraction_preserves_source_pixels(layers):
    bg=Image.open(layers/'clean.png').convert('RGBA'); source=bg.copy()
    for name in ['graphics','text']:source.alpha_composite(Image.open(layers/f'{name}.png'))
    source=Image.open(io.BytesIO(jpg(source))).convert('RGB')
    regions=[{'category':'text','box':[0,.05,.5,.25]}, {'category':'graphics','box':[.4,.25,.95,.95]}]
    separated,reconstructed,quality=extract(source,bg.convert('RGB'),regions)
    for layer in separated.values():
        arr=np.asarray(layer); opaque=arr[:,:,3]>0
        assert opaque.any()
        assert np.array_equal(arr[:,:,:3][opaque],np.asarray(source)[opaque])
    assert quality['reconstruction_mae']<2


def test_http_upload_gating_and_missing_credentials(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path)
    monkeypatch.delenv('AD_OPENAI_API_KEY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    client=TestClient(main.app)
    assert client.get('/').status_code==200
    assert client.get('/api/config').json()['api_configured'] is False
    assert client.post('/api/jobs',files={'file':('bad.jpg',b'bad','image/jpeg')}).status_code==400
    res=client.post('/api/jobs',files={'file':('real-name.jpg',jpg(Image.new('RGB',(512,512))),'image/jpeg')})
    assert res.status_code==200
    job=res.json()['id']
    assert client.post(f'/api/jobs/{job}/process').status_code==422
    assert client.post(f'/api/jobs/{job}/confirm').status_code==409
    assert client.get(f'/api/jobs/{job}/layout/1080x1080').status_code==409
    assert client.get(f'/api/jobs/{job}/assets/job.json').status_code==404
    assert client.get(f'/api/jobs/{job}/assets/original.png').headers['content-type']=='image/png'
    assert client.get('/api/jobs/not-an-id').status_code==404


def test_missing_key_is_not_a_fake_success(monkeypatch):
    monkeypatch.delenv('AD_OPENAI_API_KEY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    with pytest.raises(ProviderError,match='未配置'):OpenAIImages()


def test_local_http_preview_export_workflow(layers,tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path/'jobs');main.DATA.mkdir()
    client=TestClient(main.app)
    job=client.post('/api/jobs',files={'file':('fixture.jpg',jpg(Image.new('RGB',(640,480))),'image/jpeg')}).json()['id']
    directory=main.DATA/job
    # Explicit local layer fixture. No provider responses or AI processing mocked.
    for name in ['text.png','graphics.png','clean.png','expanded.png']:
        (directory/name).write_bytes((layers/name).read_bytes())
    main.update(directory,status='review')
    assert client.post(f'/api/jobs/{job}/confirm').status_code==200
    for key in SIZES:
        layout=client.get(f'/api/jobs/{job}/layout/{key}').json()
        assert client.post(f'/api/jobs/{job}/preview',json=layout).status_code==200
        res=client.post(f'/api/jobs/{job}/export',json=layout)
        assert res.status_code==200,res.text
        assert res.json()['validation']['verified']
        out=client.get(res.json()['url']);assert out.status_code==200
        assert out.content[:4]==b'8BPS'
        assert Image.open(io.BytesIO(client.get(res.json()['preview']).content)).size==(SIZES[key]['width'],SIZES[key]['height'])
    layout['layers'][1]['x']=-500
    assert client.post(f'/api/jobs/{job}/export',json=layout).status_code==422


def test_safety_configuration_is_exact():
    assert [s['safe'] for s in SIZES.values()]==[[100,100,980,980],[0,250,1080,1100],[65,268,1015,1255],[120,60,1080,570]]
