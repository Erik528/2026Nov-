"""Source-pixel foreground extraction, deterministic layouts and validated PSDs."""
import io
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

SIZES = json.loads(Path(__file__).with_name('sizes.json').read_text())
MAX_PIXELS = 24_000_000
MAX_SIDE = 6000
WORK_SIDE = 1600
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def read_upload(raw):
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError('文件超过 20 MB 限制。')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format != 'JPEG':
                raise ValueError('只接受真实 JPG/JPEG 文件。')
            w, h = im.size
            if min(w, h) < 256:
                raise ValueError('图片短边至少需要 256 px。')
            if w*h > MAX_PIXELS or max(w, h) > MAX_SIDE:
                raise ValueError('图片最多 2400 万像素，长边不超过 6000 px。')
            im.load()
            return ImageOps.exif_transpose(im).convert('RGB')
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise ValueError('图片像素超过处理限制。') from e
    except (OSError, SyntaxError) as e:
        raise ValueError('无法读取 JPG，文件可能已损坏。') from e


def work_image(im):
    im = im.copy()
    im.thumbnail((WORK_SIDE, WORK_SIDE), Image.Resampling.LANCZOS)
    return im


def region_masks(size, elements):
    w, h = size
    masks = {'text': Image.new('L', size), 'graphics': Image.new('L', size)}
    for e in elements:
        draw = ImageDraw.Draw(masks[e['category']])
        box = e['box']
        coords = [round(box[0]*w), round(box[1]*h), round(box[2]*w), round(box[3]*h)]
        if e.get('polygon') and e['category'] == 'graphics':
            draw.polygon([(round(x*w),round(y*h)) for x,y in e['polygon']], fill=255)
        else:
            draw.rectangle(coords, fill=255)
    # Small safety band to include outlines and shadows.
    k = max(3, round(min(w,h)*.006) | 1)
    return {name: cv2.dilate(np.asarray(mask), np.ones((k,k),np.uint8)) for name,mask in masks.items()}


def extract(source, clean, elements):
    """Never regenerate foreground. Binary alpha retains the exact JPEG RGB pixels.

    Difference matting is approximate: antialiasing can retain background fringes.
    Every result requires human review, including low-error reconstructions.
    """
    src = np.asarray(source.convert('RGB'))
    bg = np.asarray(clean.convert('RGB'))
    diff = np.max(np.abs(src.astype(np.int16)-bg.astype(np.int16)), axis=2)
    masks = region_masks(source.size, elements)
    significant = (diff > 18).astype(np.uint8)*255
    significant = cv2.morphologyEx(significant, cv2.MORPH_CLOSE, np.ones((3,3),np.uint8))
    text = significant & masks['text']
    graphics = significant & masks['graphics'] & cv2.bitwise_not(text)
    # Include uncertain foreground pixels within identified regions; no generated RGB.
    layers = {}
    for name, alpha in [('text',text),('graphics',graphics)]:
        layers[name] = Image.fromarray(np.dstack([src,alpha]),'RGBA')
    composite = clean.convert('RGBA')
    composite.alpha_composite(layers['graphics'])
    composite.alpha_composite(layers['text'])
    mae = float(np.mean(np.abs(np.asarray(composite.convert('RGB')).astype(float)-src)))
    outside = ((masks['text'] | masks['graphics']) == 0)
    outside_change = float(np.mean(diff[outside] > 28)) if outside.any() else 0
    warnings = ['拆分为近似差分遮罩，细边、透明物体和相互遮挡必须人工审核。',
                '文字是保留原图像素的栅格层，不能直接编辑文案。']
    for name, layer in layers.items():
        if layer.getchannel('A').getbbox() is None:
            raise ValueError(f'{name} 层为空。请检查识别区域并重新处理。')
    if mae > 8 or outside_change > .08:
        warnings.append('存在未识别元素或背景变化：重叠合成与原图差异较大，请勿直接确认。')
    return layers, composite, {'reconstruction_mae':round(mae,3),'background_changed_fraction':round(outside_change,4),'warnings':warnings}


def default_layout(directory, key):
    if (directory/'psd-layers.json').exists():
        from .psd_import import default_psd_layout
        return default_psd_layout(directory,key)
    cfg = SIZES[key]
    w,h = cfg['width'],cfg['height']
    l,t,r,b = cfg['safe']
    # An additional visual margin also applies to the full-width 4:5 safe area.
    margin = 24
    boxes = {}
    for name in ['graphics','text']:
        with Image.open(directory/f'{name}.png') as im:
            box = im.getchannel('A').getbbox()
            if not box: raise ValueError(f'{name} 层为空')
            boxes[name] = box
    union = [min(x[0] for x in boxes.values()), min(x[1] for x in boxes.values()),
             max(x[2] for x in boxes.values()),max(x[3] for x in boxes.values())]
    uw,uh = union[2]-union[0],union[3]-union[1]
    scale = min(10,(r-l-2*margin)/uw,(b-t-2*margin)/uh)
    # Preserve the source hierarchy and relative spacing, anchored in the safe zone.
    x0 = l+margin+(r-l-2*margin-uw*scale)/2
    y0 = t+margin+(b-t-2*margin-uh*scale)/2
    layers=[]
    for name,box in boxes.items():
        layers.append({'id':name,'x':round(x0+(box[0]-union[0])*scale),
                       'y':round(y0+(box[1]-union[1])*scale),'scale':scale})
    return {'size':key,'layers':layers,'background':{'x':.5,'y':.5,'zoom':1}}


def validate_layout(layout,expected_ids=None):
    key=layout.get('size')
    if key not in SIZES: raise ValueError('未知目标尺寸。')
    expected_ids=expected_ids if expected_ids is not None else ['text','graphics']
    if not isinstance(layout.get('layers'),list) or len(layout['layers']) != len(expected_ids):
        raise ValueError('构图必须包含全部可移动图层。')
    if not all(isinstance(x,dict) and isinstance(x.get('id'),str) for x in layout['layers']) or {x.get('id') for x in layout['layers']} != set(expected_ids):
        raise ValueError('图层名称无效。')
    for layer in layout['layers']:
        for k in ['x','y','scale']:
            if not isinstance(layer.get(k),(int,float)) or not math.isfinite(layer[k]):
                raise ValueError('图层参数必须为有限数值。')
        if not .01 <= layer['scale'] <= 10 or max(abs(layer['x']),abs(layer['y']))>20000:
            raise ValueError('图层变换超出限制。')
    bg=layout.get('background',{})
    if not isinstance(bg,dict): raise ValueError('背景参数必须为对象。')
    for k,low,high in [('x',0,1),('y',0,1),('zoom',1,3)]:
        v=bg.get(k)
        if not isinstance(v,(int,float)) or not math.isfinite(v) or not low<=v<=high:
            raise ValueError('背景参数超出限制。')


def render(directory, layout):
    if (directory/'psd-layers.json').exists():
        from .psd_import import render_psd
        return render_psd(directory,layout)
    validate_layout(layout)
    cfg=SIZES[layout['size']]; size=(cfg['width'],cfg['height'])
    with Image.open(directory/'expanded.png') as img:
        bw,bh=img.size; zoom=layout['background']['zoom']
        scale=max(size[0]/bw,size[1]/bh)*zoom
        # Crop before resize to avoid huge intermediates.
        cw,ch=size[0]/scale,size[1]/scale
        left=(bw-cw)*layout['background']['x']; top=(bh-ch)*layout['background']['y']
        bg=img.convert('RGBA').resize(size,Image.Resampling.LANCZOS,box=(left,top,left+cw,top+ch))
    placed=[('Background',bg,0,0)]
    warnings=[]; bounds=[]
    safe=cfg['safe']
    elements=[]
    if (directory/'elements.json').exists():
        elements=json.loads((directory/'elements.json').read_text())
    from .psd_import import is_logo_name
    logo_checks=0
    for name in ['graphics','text']:
        transform=next(x for x in layout['layers'] if x['id']==name)
        with Image.open(directory/f'{name}.png') as img:
            source_w,source_h=img.size
            box=img.getchannel('A').getbbox()
            img=img.crop(box).convert('RGBA')
            nw=max(1,round(img.width*transform['scale'])); nh=max(1,round(img.height*transform['scale']))
            if nw*nh>32_000_000: raise ValueError('图层缩放后过大。')
            img=img.resize((nw,nh),Image.Resampling.LANCZOS)
        x,y=round(transform['x']),round(transform['y'])
        rect=[x,y,x+nw,y+nh]
        outside=rect[0]<safe[0] or rect[1]<safe[1] or rect[2]>safe[2] or rect[3]>safe[3]
        if outside and name=='text':warnings.append('文字层超出安全区')
        logo_regions=[]
        if name=='graphics':
            for element in elements:
                if element.get('category')!='graphics' or not (element.get('role')=='logo' or is_logo_name(element.get('label',''))):continue
                l,t,r,b=element['box'];s=transform['scale']
                region=[x+round((max(box[0],l*source_w)-box[0])*s),y+round((max(box[1],t*source_h)-box[1])*s),
                        x+round((min(box[2],r*source_w)-box[0])*s),y+round((min(box[3],b*source_h)-box[1])*s)]
                if region[2]<=region[0] or region[3]<=region[1]:continue
                region_outside=region[0]<safe[0] or region[1]<safe[1] or region[2]>safe[2] or region[3]>safe[3]
                label=element.get('label','Logo');logo_checks+=1
                logo_regions.append({'name':label,'box':region,'outside':region_outside})
                if region_outside:warnings.append(f'Logo“{label}”超出安全区')
        bounds.append({'id':name,'box':rect,'outside':outside,'critical':name=='text','logo_regions':logo_regions})
        placed.append(('Text (raster)' if name=='text' else 'Graphics',img,x,y))
    composite=bg.copy()
    for _,img,x,y in placed[1:]: composite.alpha_composite(img,(x,y))
    return composite,placed,{'warnings':warnings,'bounds':bounds,'safe':safe,'width':size[0],'height':size[1],'logo_regions_checked':logo_checks}


def write_psd(path, size, placed, preview):
    psd=PSDImage.new('RGBA',size,color=(255,255,255,255))
    for name,img,x,y in placed:
        layer=PixelLayer.frompil(img,psd,layer_name=name,left=x,top=y)
        layer.name=name
        psd.append(layer)
    psd.save(path)
    check=PSDImage.open(path)
    if check.size!=size or len(check)!=len(placed):
        raise ValueError('PSD 画布尺寸或图层数量验证失败。')
    expected=[p[0] for p in placed]
    if [x.name for x in check]!=expected:
        raise ValueError('PSD 图层顺序验证失败。')
    for layer,(_,img,x,y) in zip(check,placed):
        if layer.opacity!=255 or not layer.visible or (layer.left,layer.top)!=(x,y):
            raise ValueError('PSD 图层属性验证失败。')
        actual=layer.topil().convert('RGBA')
        if not np.array_equal(np.asarray(actual),np.asarray(img)):
            raise ValueError('PSD 图层像素或透明度验证失败。')
    flat=check.composite(force=True).convert('RGB')
    error=float(np.abs(np.asarray(flat).astype(float)-np.asarray(preview.convert('RGB'))).mean())
    if error>1: raise ValueError(f'PSD 合成验证失败（平均差异 {error:.2f}）。')
    # Pillow separately decodes the merged PSD image used by thumbnail viewers.
    with Image.open(path) as merged:
        merged_error=float(np.abs(np.asarray(merged.convert('RGB')).astype(float)-np.asarray(preview.convert('RGB'))).mean())
    if merged_error>1: raise ValueError('PSD 内嵌合成预览验证失败。')
    return {'canvas':list(size),'layers':expected,'composite_mae':round(error,4),'merged_mae':round(merged_error,4),'verified':True}
