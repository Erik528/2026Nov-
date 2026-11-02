import json
import os
import re
import threading
import uuid
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
import numpy as np

from .imaging import SIZES, read_upload, work_image, region_masks, extract, default_layout, render, write_psd
from .provider import OpenAIImages, ProviderError, settings
from .psd_import import PSD_MAX_BYTES,import_psd,catalog,set_roles,normalize_ai_layout

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('AD_DATA_DIR',str(ROOT.parent/'data'))).resolve()
DATA.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Meta Ad Atelier')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
LOCK=threading.RLock()
POOL=ThreadPoolExecutor(max_workers=2)
ACTIVE=set()


def folder(job):
    if not re.fullmatch('[a-f0-9]{32}',job): raise HTTPException(404,'任务不存在')
    path=DATA/job
    if not (path/'job.json').is_file(): raise HTTPException(404,'任务不存在')
    return path


def read(path):
    with LOCK: return json.loads((path/'job.json').read_text())


def update(path,**values):
    with LOCK:
        state=read(path); state.update(values)
        tmp=path/'job.tmp'; tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)); tmp.replace(path/'job.json')
        return state


def run(job):
    path=folder(job)
    try:
        api=OpenAIImages()
        source=Image.open(path/'work.png').convert('RGB')
        update(path,stage='识别文字、Logo 与主体',progress=10,error=None,error_details=None,failed_stage=None,confirmed=False)
        if not (path/'elements.json').exists():
            elements=api.detect(source)
            state=read(path)
            for element in elements:
                b=element['box']
                element['source_box_px']=[round(b[0]*state['width']),round(b[1]*state['height']),round(b[2]*state['width']),round(b[3]*state['height'])]
                element['work_box_px']=[round(b[0]*source.width),round(b[1]*source.height),round(b[2]*source.width),round(b[3]*source.height)]
            (path/'elements.json').write_text(json.dumps(elements,ensure_ascii=False,indent=2))
        elements=json.loads((path/'elements.json').read_text())
        update(path,elements=elements,stage='移除前景并补全干净背景',progress=30)
        if not (path/'clean.png').exists():
            masks=region_masks(source.size,elements)
            api.clean(source,masks['text']|masks['graphics']).save(path/'clean.png')
        clean=Image.open(path/'clean.png').convert('RGB')
        update(path,stage='提取原图像素并检查合成',progress=55)
        layers,composite,quality=extract(source,clean,elements)
        for name,im in layers.items(): im.save(path/f'{name}.png')
        composite.save(path/'reconstructed.png')
        update(path,stage='向四周各扩展 50%（仅生成一次）',progress=65,quality=quality)
        if not (path/'expanded.png').exists(): api.expand(clean).save(path/'expanded.png')
        update(path,status='review',stage='待人工审核拆分质量',progress=100,
               assets=['text.png','graphics.png','clean.png','expanded.png','reconstructed.png','elements.json'],error=None)
    except (ProviderError,ValueError) as e:
        failed_stage=read(path)['stage']
        update(path,status='error',stage='处理未完成',failed_stage=failed_stage,
               error=f'失败步骤：{failed_stage}。{e}',error_details=getattr(e,'details',{}))
    except Exception:
        update(path,status='error',stage='处理未完成',error='本地图像处理失败。已保留检查点，请检查文件、内存与服务端日志。')
        # Log traceback without provider bodies or credentials.
        import logging
        logging.getLogger(__name__).error('Local processing error in job %s',job)
    finally:
        with LOCK: ACTIVE.discard(job)


@app.get('/')
def index(): return FileResponse(ROOT/'static/index.html')


@app.get('/api/config')
def config():
    return {'sizes':SIZES,'api_configured':bool(settings()['key']),
            'limits':{'min_side':256,'max_side':6000,'max_pixels':24000000,'max_mb':20,'psd_max_mb':100},
            'models':{'vision':settings()['vision'],'image':settings()['image']}}


@app.post('/api/jobs')
async def upload(file:UploadFile):
    raw=await file.read(PSD_MAX_BYTES+1)
    if raw[:4]==b'8BPS' or Path(file.filename or '').suffix.lower()=='.psd':
        job=uuid.uuid4().hex;path=DATA/job;path.mkdir()
        try:data,quality=import_psd(raw,path)
        except Exception as e:
            shutil.rmtree(path)
            if isinstance(e,ValueError):raise HTTPException(400,str(e))
            raise HTTPException(400,'无法导入此 PSD，请检查格式或将复杂效果栅格化后重试。')
        name=re.sub(r'[^\w\-.\u4e00-\u9fff]','_',Path(file.filename or 'design').stem)[:80] or 'design'
        state={'id':job,'filename':file.filename,'name':name,'source_type':'psd',
               'width':data['canvas'][0],'height':data['canvas'][1],'work_width':data['canvas'][0],'work_height':data['canvas'][1],
               'status':'review','stage':'PSD 图层已读取，待确认图层角色','progress':100,'confirmed':False,
               'assets':[i['file'] for i in data['layers']]+['psd-layers.json','source.psd'],
               'psd_layers':data['layers'],'quality':quality}
        (path/'job.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
        return state
    try: source=read_upload(raw)
    except ValueError as e: raise HTTPException(400,str(e))
    job=uuid.uuid4().hex; path=DATA/job; path.mkdir()
    source.save(path/'original.png'); work=work_image(source); work.save(path/'work.png')
    name=re.sub(r'[^\w\-.\u4e00-\u9fff]','_',Path(file.filename or 'advertisement').stem)[:80] or 'advertisement'
    state={'id':job,'filename':file.filename,'name':name,'source_type':'jpg','width':source.width,'height':source.height,
           'work_width':work.width,'work_height':work.height,'status':'uploaded','stage':'等待拆分','progress':0,'confirmed':False,'assets':[]}
    (path/'job.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    return state


@app.get('/api/jobs/{job}')
def state(job:str):
    path=folder(job); result=read(path)
    with LOCK:
        if result['status']=='processing' and job not in ACTIVE:
            result=update(path,status='error',stage='服务重启中断了任务',error='可点击继续处理，复用已保存结果。')
    return result


@app.post('/api/jobs/{job}/process')
def process(job:str, fresh:bool=False):
    path=folder(job)
    with LOCK:
        if read(path).get('source_type')=='psd':raise HTTPException(409,'PSD 已有独立图层，请确认图层角色后直接选择尺寸；不需要重新拆分。')
        if job in ACTIVE: raise HTTPException(409,'任务正在处理。')
        if read(path)['status']=='review' and not fresh: return read(path)
        if not settings()['key']: raise HTTPException(422,'未配置 AD_OPENAI_API_KEY；请在环境设置或 .env 中配置后重启服务。')
        if fresh:
            for name in ['elements.json','text.png','graphics.png','clean.png','expanded.png','reconstructed.png']:
                (path/name).unlink(missing_ok=True)
            for item in path.glob('layout-*.json'): item.unlink()
            for pattern in ['preview-*.png','*.psd','validation-*.json']:
                for item in path.glob(pattern): item.unlink()
        update(path,status='processing',confirmed=False,stage='准备调用 API',progress=2,error=None,error_details=None,failed_stage=None,assets=[])
        ACTIVE.add(job); POOL.submit(run,job)
    return read(path)


@app.post('/api/jobs/{job}/confirm')
def confirm(job:str):
    path=folder(job)
    if read(path)['status']!='review': raise HTTPException(409,'请先完成并审核拆分。')
    if read(path).get('source_type')=='psd' and not any(i['category']!='background' for i in catalog(path)['layers']):
        raise HTTPException(409,'请至少指定一个文字、主体或装饰前景层并保存角色。')
    return update(path,confirmed=True)


@app.get('/api/jobs/{job}/layout/{key}')
def get_layout(job:str,key:str,reset:bool=False):
    path=folder(job)
    if not read(path).get('confirmed'): raise HTTPException(409,'请先确认拆分结果。')
    if key not in SIZES: raise HTTPException(400,'未知尺寸')
    saved=path/f'layout-{key}.json'
    return json.loads(saved.read_text()) if saved.exists() and not reset else default_layout(path,key)


@app.post('/api/jobs/{job}/psd-roles')
def psd_roles(job:str,body:dict):
    path=folder(job)
    if read(path).get('source_type')!='psd':raise HTTPException(400,'此功能仅适用于 PSD。')
    with LOCK:
        if job in ACTIVE:raise HTTPException(409,'任务正在处理。')
        try:data=set_roles(path,body.get('layers'))
        except (ValueError,TypeError) as e:raise HTTPException(400,str(e))
        for pattern in ['layout-*.json','ai-layout-*.json','preview-*.png','validation-*.json','*.psd']:
            for item in path.glob(pattern):
                if item.name!='source.psd':item.unlink()
        return update(path,psd_layers=data['layers'],confirmed=False,stage='PSD 图层角色已更新，请确认')


@app.post('/api/jobs/{job}/ai-layout/{key}')
def ai_layout(job:str,key:str):
    path=folder(job)
    if read(path).get('source_type')!='psd' or not read(path).get('confirmed'):raise HTTPException(409,'请先确认 PSD 图层角色。')
    if key not in SIZES:raise HTTPException(400,'未知尺寸。')
    with LOCK:
        if job in ACTIVE:raise HTTPException(409,'任务正在处理。')
        saved=path/f'ai-layout-{key}.json'
        if saved.exists():
            (path/f'layout-{key}.json').write_text(saved.read_text())
            return json.loads(saved.read_text())
        ACTIVE.add(job)
    try:
        api=OpenAIImages()
        proposal=api.propose_psd_layout(Image.open(path/'original.png'),catalog(path),SIZES[key],default_layout(path,key))
        layout=normalize_ai_layout(path,proposal,key)
        with LOCK:
            saved.write_text(json.dumps(layout,ensure_ascii=False,indent=2))
            (path/f'layout-{key}.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2))
        return layout
    except ProviderError as e:raise HTTPException(422,str(e))
    except (ValueError,KeyError,TypeError) as e:raise HTTPException(422,'AI 构图未通过坐标与图层检查；保留原构图，可继续手动调整。')
    finally:
        with LOCK:ACTIVE.discard(job)


def save_render(job,layout):
    path=folder(job)
    if not read(path).get('confirmed'): raise HTTPException(409,'请先确认拆分结果。')
    with LOCK:
        if job in ACTIVE: raise HTTPException(409,'正在处理，不能调整或导出。')
        try: composite,placed,report=render(path,layout)
        except (ValueError,KeyError,TypeError) as e: raise HTTPException(400,str(e))
        key=layout['size']
        composite.save(path/f'preview-{key}.png')
        (path/f'layout-{key}.json').write_text(json.dumps(layout,indent=2))
    return path,composite,placed,report


@app.post('/api/jobs/{job}/preview')
def preview(job:str,layout:dict):
    _,_,_,report=save_render(job,layout)
    return {**report,'preview':f'/api/jobs/{job}/assets/preview-{layout["size"]}.png'}


@app.post('/api/jobs/{job}/export')
def export(job:str,layout:dict):
    path,composite,placed,report=save_render(job,layout)
    if report['warnings']: raise HTTPException(422,'关键图层超出安全区，请调整后再导出。')
    name=read(path)['name']+'-'+layout['size']+'.psd'
    with LOCK:
        try: validation=write_psd(path/name,composite.size,placed,composite)
        except ValueError as e:
            (path/name).unlink(missing_ok=True)
            raise HTTPException(500,str(e))
        (path/f'validation-{layout["size"]}.json').write_text(json.dumps(validation,indent=2))
    return {'url':f'/api/jobs/{job}/assets/{name}','preview':f'/api/jobs/{job}/assets/preview-{layout["size"]}.png',
            'validation':validation,'report':report}


@app.get('/api/jobs/{job}/assets/{name}')
def asset(job:str,name:str):
    path=folder(job)
    allowed={'original.png','work.png','text.png','graphics.png','clean.png','expanded.png','reconstructed.png','elements.json'}
    allowed|={f'{prefix}-{key}.{ext}' for key in SIZES for prefix,ext in [('preview','png'),('layout','json'),('validation','json')]}
    allowed|={read(path)['name']+'-'+key+'.psd' for key in SIZES}
    if read(path).get('source_type')=='psd':
        allowed|={'source.psd','psd-layers.json'}|{i['file'] for i in catalog(path)['layers']}
    if name not in allowed or not (path/name).is_file(): raise HTTPException(404,'结果不存在。')
    return FileResponse(path/name,filename=name)
