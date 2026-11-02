"""Import existing PSD layers as independently movable rendered units.

Native typography, smart objects and group internals remain in source.psd only;
resized exports are independent raster layers. No AI segmentation is used here.
"""
import io
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import AdjustmentLayer

PSD_MAX_BYTES=100*1024*1024
ROLES={'background','text','logo','graphics','decoration'}
MAX_UNITS=60
RESIZE_POLICY='canvas-cover-text-logo-v2'


def is_logo_name(name):
    return bool(re.search(r'logo|商标|标志|品牌标识|品牌标志',name,re.I))


def white(image):
    result=Image.new('RGBA',image.size,'white');result.alpha_composite(image.convert('RGBA'))
    return result


def resize_alpha(image,size,**kwargs):
    # Lanczos ringing must not make a partially opaque source layer more opaque.
    ceiling=image.getchannel('A').getextrema()[1]
    result=image.resize(size,Image.Resampling.LANCZOS,**kwargs)
    if ceiling<255:result.putalpha(result.getchannel('A').point([min(v,ceiling) for v in range(256)]))
    return result


def catalog(directory):
    path=directory/'psd-layers.json'
    data=json.loads(path.read_text())
    if data.get('resize_policy')!=RESIZE_POLICY:
        backup=directory/'psd-layers.before-resize-v2.json'
        if not backup.exists():backup.write_text(path.read_text())
        for info in data['layers']:
            if info['category']=='graphics' and is_logo_name(info['name']):info['category']='logo'
            info['critical']=info['category'] in {'text','logo'}
        data['warnings']=[message.replace('请确认各层角色；背景采用 cover 裁切，文字与主体默认要求位于安全区内。',
            '可直接点击画幅 resize 并下载。仅文字与 Logo 受安全区约束；图像和渐变允许出血。') for message in data.get('warnings',[])]
        data['resize_policy']=RESIZE_POLICY
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    # Role, not a stale caller-supplied critical flag, controls export blocking.
    for info in data['layers']:info['critical']=info['category'] in {'text','logo'}
    return data


def metadata(layer):
    result={'name':layer.name,'kind':layer.kind,'box':list(layer.bbox),
            'visible':bool(layer.visible),'opacity':int(layer.opacity),
            'blend_mode':getattr(layer.blend_mode,'value',b'pass' if layer.is_group() else b'norm').decode('ascii','replace')}
    if layer.kind=='type':
        try:result['text']=layer.text[:500]
        except Exception:result['text']=''
    if layer.is_group():result['children']=[metadata(child) for child in layer]
    return result


def guess_role(layer,index,canvas):
    name=layer.name.lower()
    if layer.kind=='type':return 'text'
    if is_logo_name(name):return 'logo'
    if re.search(r'background|backdrop|背景|底图|底色|^bg(?:\b|_)',name):return 'background'
    if layer.kind=='gradientfill' or re.search(r'gradient|渐变',name):return 'graphics'
    if re.search(r'text|headline|title|body.?copy|price|cta|文字|标题|文案|价格',name):return 'text'
    if layer.is_group() and any(child.kind=='type' and child.is_visible() for child in layer.descendants()):return 'text'
    l,t,r,b=layer.bbox;w,h=canvas
    if index==0 and l<=0 and t<=0 and r>=w and b>=h:return 'background'
    return 'graphics'


def import_psd(raw,directory):
    if len(raw)>PSD_MAX_BYTES:raise ValueError('PSD 文件超过 100 MB 限制。')
    if raw[:4]!=b'8BPS':raise ValueError('不是有效的 PSD 文件。')
    if raw[4:6]!=b'\x00\x01':raise ValueError('暂不支持 PSB，请保存为标准 PSD。')
    try:psd=PSDImage.open(io.BytesIO(raw))
    except Exception as e:raise ValueError('无法读取 PSD，文件可能损坏或格式不受支持。') from e
    w,h=psd.size
    if min(w,h)<256 or max(w,h)>6000 or w*h>24000000:
        raise ValueError('PSD 短边至少 256 px、长边最多 6000 px、画布最多 2400 万像素。')
    if psd.depth!=8 or psd.color_mode.name!='RGB':
        raise ValueError('请在 Photoshop 中将 PSD 转换为 RGB / 8 位后上传。')
    total=sum(1 for _ in psd.descendants())
    if total>500:raise ValueError('PSD 总图层数超过 500，请先整理图层。')
    units=[layer for layer in psd if layer.visible]
    if not 2<=len(units)<=MAX_UNITS:
        raise ValueError('PSD 需要 2–60 个可见顶层图层/图层组。请将需要独立移动的元素分开放置，不要全部放在一个顶层组中。')
    data={'canvas':[w,h],'resize_policy':RESIZE_POLICY,'layers':[],'tree':[metadata(layer) for layer in psd],
          'warnings':['PSD 直接读取已有分层，不进行 JPG 拆分，也不调用背景补全或扩图。',
                      '顶层图层/组分别渲染为独立栅格层；原生可编辑文字、智能对象、组内层级保留在原始 PSD 中，resize 导出不保留这些编辑属性。',
                      '可直接点击画幅 resize 并下载。仅文字和 Logo 受安全区约束；照片、渐变与装饰可出血。图层角色可随时修正。']}
    if any(not layer.visible for layer in psd):
        data['warnings'].append('隐藏顶层仅记录元数据并保留在原始 PSD 中，本次 resize 不导出隐藏层。')
    budget=0
    rebuilt=Image.new('RGBA',(w,h),'white')
    for index,layer in enumerate(units):
        if layer.clipping_layer or isinstance(layer,AdjustmentLayer) or getattr(layer.blend_mode,'value',b'pass' if layer.is_group() else b'norm') not in {b'norm',b'pass'}:
            raise ValueError(f'图层“{layer.name}”包含顶层剪贴、调整或非普通混合模式。请在 Photoshop 中与相关对象编组或栅格化后再上传。')
        box=tuple(layer.bbox);lw,lh=box[2]-box[0],box[3]-box[1]
        if lw<=0 or lh<=0:
            data['warnings'].append(f'跳过空边界图层“{layer.name[:200]}”。')
            continue
        budget+=lw*lh
        if lw*lh>24000000 or budget>60000000 or max(map(abs,box))>20000:
            raise ValueError('PSD 图层像素总量或范围过大，请裁去多余的画布外内容。')
        try:im=layer.composite(viewport=box,force=True)
        except Exception as e:raise ValueError(f'无法渲染图层“{layer.name}”，请在 Photoshop 中栅格化该层后重试。') from e
        if im is None or im.convert('RGBA').getchannel('A').getbbox() is None:
            data['warnings'].append(f'跳过无可见像素的图层“{layer.name[:200]}”。')
            continue
        im=im.convert('RGBA')
        # Remove transparent padding but retain absolute source coordinates.
        crop=im.getchannel('A').getbbox();im=im.crop(crop)
        x,y=box[0]+crop[0],box[1]+crop[1]
        file=f'layer-{index:03d}.png';im.save(directory/file)
        role=guess_role(layer,index,(w,h))
        info={'id':f'psd-{index:03d}','name':layer.name[:200],'file':file,
              'box':[x,y,x+im.width,y+im.height],'category':role,
              'critical':role in {'text','logo'},'source':metadata(layer)}
        data['layers'].append(info)
        rebuilt.alpha_composite(im,(x,y))
    if len(data['layers'])<2:raise ValueError('PSD 中不足两个可渲染的独立图层，请检查可见性与图层内容。')
    try:reference=psd.composite()
    except Exception as e:raise ValueError('无法生成 PSD 原始合成预览。') from e
    if reference is None:raise ValueError('PSD 没有可读取的合成画面。')
    reference=white(reference)
    mae=float(np.abs(np.asarray(reference.convert('RGB')).astype(float)-np.asarray(rebuilt.convert('RGB'))).mean())
    if mae>8:raise ValueError('PSD 独立图层渲染与原始合成差异过大。可能含有依赖外部图层的效果，请将相关层编组或栅格化后重试。')
    if mae>1:data['warnings'].append('原始 PSD 合成与导入后的重叠合成存在差异，请重点检查蒙版、混合与图层效果。')
    (directory/'source.psd').write_bytes(raw)
    reference.convert('RGB').save(directory/'original.png');reference.convert('RGB').save(directory/'work.png')
    rebuilt.convert('RGB').save(directory/'reconstructed.png')
    (directory/'psd-layers.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    rebuild_background(directory,data)
    return data,{'reconstruction_mae':round(mae,3),'warnings':data['warnings']}


def rebuild_background(directory,data):
    bg=Image.new('RGBA',tuple(data['canvas']),'white')
    for info in data['layers']:
        if info['category']=='background':
            with Image.open(directory/info['file']) as im:bg.alpha_composite(im.convert('RGBA'),tuple(info['box'][:2]))
    bg.save(directory/'expanded.png')


def set_roles(directory,roles):
    data=catalog(directory)
    if not isinstance(roles,list) or len(roles)!=len(data['layers']) or not all(isinstance(r,dict) for r in roles):raise ValueError('请为每个 PSD 图层指定角色。')
    ids=[r.get('id') for r in roles]
    if len(set(ids))!=len(ids) or set(ids)!={r['id'] for r in data['layers']}:raise ValueError('图层列表不匹配。')
    if any(r.get('category') not in ROLES for r in roles):raise ValueError('未知图层角色。')
    if not any(r['category']!='background' for r in roles):raise ValueError('至少保留一个可移动的文字、主体或装饰层。')
    for info in data['layers']:
        info['category']=next(r['category'] for r in roles if r['id']==info['id'])
        info['critical']=info['category'] in {'text','logo'}
    (directory/'psd-layers.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
    rebuild_background(directory,data)
    return data


def layer_info(data):
    return [{k:info[k] for k in ['id','name','file','box','category','critical']} for info in data['layers'] if info['category']!='background']


def default_psd_layout(directory,key):
    from .imaging import SIZES
    data=catalog(directory);infos=layer_info(data)
    if not infos:raise ValueError('没有可移动前景层，请重新指定 PSD 图层角色。')
    cfg=SIZES[key];w,h=cfg['width'],cfg['height'];sw,sh=data['canvas']
    scale=max(w/sw,h/sh);dx=(w-sw*scale)/2;dy=(h-sh*scale)/2
    layers=[]
    for info in infos:
        x,y,right,bottom=info['box'];iw,ih=right-x,bottom-y
        layers.append({'id':info['id'],'x':round(dx+x*scale),'y':round(dy+y*scale),'scale':scale})
    layout={'size':key,'layers':layers,'background':{'x':.5,'y':.5,'zoom':1},
            'layer_info':infos,'resize_policy':RESIZE_POLICY,
            'source_transform':{'canvas':[sw,sh],'scale':scale,'x':dx,'y':dy}}
    constrain_critical(layout,infos,cfg['safe'],margin=24)
    return layout


def constrain_critical(layout,infos,safe,margin=4):
    l,t,r,b=safe
    for tr in layout['layers']:
        info=next(i for i in infos if i['id']==tr['id'])
        if not info['critical']:continue
        iw,ih=info['box'][2]-info['box'][0],info['box'][3]-info['box'][1]
        old_scale=tr['scale'];cx=tr['x']+iw*old_scale/2;cy=tr['y']+ih*old_scale/2
        tr['scale']=min(old_scale,(r-l-2*margin)/iw,(b-t-2*margin)/ih)
        nw,nh=max(1,round(iw*tr['scale'])),max(1,round(ih*tr['scale']))
        tr['x']=max(l+margin,min(round(cx-nw/2),r-margin-nw))
        tr['y']=max(t+margin,min(round(cy-nh/2),b-margin-nh))


def render_psd(directory,layout):
    from .imaging import SIZES,validate_layout
    data=catalog(directory);infos=layer_info(data)
    validate_layout(layout,[i['id'] for i in infos])
    cfg=SIZES[layout['size']];w,h=cfg['width'],cfg['height'];size=(w,h)
    bgcfg=layout['background'];sw,sh=data['canvas'];bgscale=max(w/sw,h/sh)*bgcfg['zoom']
    cw,ch=w/bgscale,h/bgscale;left=(sw-cw)*bgcfg['x'];top=(sh-ch)*bgcfg['y']
    placed=[('Canvas',Image.new('RGBA',size,'white'),0,0)]
    # Keep source background units separate in the exported PSD.
    for info in data['layers']:
        if info['category']!='background':continue
        full=Image.new('RGBA',(sw,sh))
        with Image.open(directory/info['file']) as im:full.alpha_composite(im.convert('RGBA'),tuple(info['box'][:2]))
        cropped=resize_alpha(full,size,box=(left,top,left+cw,top+ch))
        placed.append((info['name'],cropped,0,0))
    warnings=[];bounds=[];safe=cfg['safe']
    for info in infos:
        tr=next(l for l in layout['layers'] if l['id']==info['id'])
        with Image.open(directory/info['file']) as im:
            nw=max(1,round(im.width*tr['scale']));nh=max(1,round(im.height*tr['scale']))
            if nw*nh>32000000:raise ValueError('图层缩放后过大。')
            im=resize_alpha(im.convert('RGBA'),(nw,nh))
        x,y=round(tr['x']),round(tr['y']);rect=[x,y,x+nw,y+nh]
        outside=rect[0]<safe[0] or rect[1]<safe[1] or rect[2]>safe[2] or rect[3]>safe[3]
        if outside and info['critical']:warnings.append(f'“{info["name"]}”超出安全区')
        bounds.append({'id':info['id'],'name':info['name'],'box':rect,'outside':outside,'critical':info['critical']})
        placed.append((info['name'],im,x,y))
    composite=placed[0][1].copy()
    for _,im,x,y in placed[1:]:composite.alpha_composite(im,(x,y))
    return composite,placed,{'warnings':warnings,'bounds':bounds,'safe':safe,'width':w,'height':h}


def normalize_ai_layout(directory,proposal,key):
    """AI proposes; the program validates IDs, exact pixels and safe bounds."""
    from .imaging import SIZES,validate_layout
    baseline=default_psd_layout(directory,key);infos=baseline['layer_info'];ids=[i['id'] for i in infos]
    if not isinstance(proposal,dict):raise ValueError('AI 构图数据无效。')
    candidate={'size':key,'layers':proposal.get('layers'),'background':proposal.get('background',baseline['background'])}
    validate_layout(candidate,ids)
    constrain_critical(candidate,infos,SIZES[key]['safe'])
    candidate['layer_info']=infos
    candidate['resize_policy']=RESIZE_POLICY
    render_psd(directory,candidate)
    return candidate
