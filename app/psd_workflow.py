"""Semantic PSD roles, cached 50% outpaint, and non-cropping background framing."""
import hashlib
import json
import math
from pathlib import Path

from PIL import Image,ImageDraw,ImageFont
from .psd_import import catalog,ROLES,white,resize_alpha


def atomic_json(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)


def background_signature(data):
    return hashlib.sha256(json.dumps([(i['id'],i['box']) for i in data['layers'] if i['category']=='background']).encode()).hexdigest()[:16]


def background_meta(directory,data=None):
    data=data or catalog(directory);path=directory/'psd-background.json'
    if path.exists():
        meta=json.loads(path.read_text())
        if meta.get('signature')==background_signature(data) and (directory/meta['file']).is_file():return meta
    return None


def contact_sheet(directory,data):
    cols=4;tw,th=280,220
    sheet=Image.new('RGB',(cols*tw,math.ceil(len(data['layers'])/cols)*th),'#e8ece8')
    draw=ImageDraw.Draw(sheet);font=ImageFont.load_default(size=17)
    for n,info in enumerate(data['layers']):
        x=(n%cols)*tw;y=(n//cols)*th
        draw.rectangle((x,y,x+tw-4,y+th-4),fill='#c1c9c4')
        draw.text((x+8,y+5),info['id'],fill='black',font=font)
        with Image.open(directory/info['file']) as image:
            image=image.convert('RGBA');image.thumbnail((tw-16,th-40),Image.Resampling.LANCZOS)
            # Mid-gray makes both white fades and black text visible.
            sheet.paste(image,(x+(tw-image.width)//2,y+32+(th-40-image.height)//2),image)
    return sheet


def apply_analysis(directory,proposal):
    data=catalog(directory);items=proposal.get('layers') if isinstance(proposal,dict) else None
    if not isinstance(items,list) or len(items)!=len(data['layers']):raise ValueError('图层识别没有包含全部 PSD 图层。')
    if not all(isinstance(i,dict) and isinstance(i.get('id'),str) and i.get('category') in ROLES for i in items):raise ValueError('图层识别角色无效。')
    if {i['id'] for i in items}!={i['id'] for i in data['layers']}:raise ValueError('图层识别返回了未知或重复 ID。')
    for info in data['layers']:
        item=next(i for i in items if i['id']==info['id'])
        # A user-confirmed role takes precedence over a cached AI classification.
        if info.get('role_source')!='manual':
            role=item['category']
            if info['source']['kind']=='type':role='text'
            elif role=='logo' or item.get('contains_logo') is True:role='logo'
            elif item.get('contains_text') is True:role='text'
            info.update(category=role,critical=role in {'text','logo'},role_source='ai')
        box=item.get('subject_box')
        if box is not None:
            if not isinstance(box,list) or len(box)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in box) or not(box[0]<box[2] and box[1]<box[3]):raise ValueError('主体区域坐标无效。')
            info['subject_box']=box
    if not any(i['category']=='background' for i in data['layers']):raise ValueError('未识别到独立背景。请指定背景图层并保存角色，再继续准备。')
    data['analysis_verified']=True
    atomic_json(directory/'psd-layers.json',data)
    return data


def build_background_source(directory,data):
    units=[i for i in data['layers'] if i['category']=='background']
    if not units:raise ValueError('请指定至少一个独立背景图层。')
    # Retain the FULL source photo, including pixels outside the PSD canvas.
    left=min(i['box'][0] for i in units);top=min(i['box'][1] for i in units)
    right=max(i['box'][2] for i in units);bottom=max(i['box'][3] for i in units)
    width,height=right-left,bottom-top
    if width*height>24000000:raise ValueError('完整背景范围超过 2400 万像素，请先裁去无关空白。')
    image=Image.new('RGBA',(width,height),'white');subjects=[]
    for info in units:
        with Image.open(directory/info['file']) as im:image.alpha_composite(im.convert('RGBA'),(info['box'][0]-left,info['box'][1]-top))
        if info.get('subject_box'):
            l,t,r,b=info['subject_box'];x,y,x2,y2=info['box']
            subjects.append([(x-left+l*(x2-x))/width,(y-top+t*(y2-y))/height,
                             (x-left+r*(x2-x))/width,(y-top+b*(y2-y))/height])
    subject=[min(b[0] for b in subjects),min(b[1] for b in subjects),max(b[2] for b in subjects),max(b[3] for b in subjects)] if subjects else [0,0,1,1]
    native_box=[left,top,right,bottom]
    image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
    return image.convert('RGB'),native_box,subject


def prepare(directory,api,progress):
    data=catalog(directory)
    analysis=directory/'psd-analysis.json'
    if not data.get('analysis_verified'):
        progress('视觉识别文字、Logo、智能对象与主体位置',15)
        if analysis.exists():proposal=json.loads(analysis.read_text())
        else:
            sheet=contact_sheet(directory,data);sheet.save(directory/'psd-contact-sheet.png')
            with Image.open(directory/'original.png') as original:proposal=api.analyze_psd(original,sheet,data)
            # Only cache complete, validated classifications.
            data=apply_analysis(directory,proposal)
            atomic_json(analysis,proposal)
        data=apply_analysis(directory,proposal)
    progress('准备完整背景（保留原画布外的左右内容）',40)
    source,native_box,subject=build_background_source(directory,data)
    source.save(directory/'background-original.png')
    signature=background_signature(data);file=f'psd-expanded-{signature}.png'
    if not (directory/file).exists():
        progress('背景向左、右、上、下各扩展 50%（只调用一次）',55)
        result=api.expand(source)
        if result.size!=(2*source.width,2*source.height):raise ValueError('扩图尺寸不等于工作背景的两倍。')
        tmp=directory/(file+'.tmp');result.save(tmp,format='PNG');tmp.replace(directory/file)
    else:progress('复用已缓存的四周扩图',80)
    meta={'signature':signature,'file':file,'source_size':list(source.size),'native_box':native_box,
          'subject_box':subject,'expanded_size':[2*source.width,2*source.height],
          'original_rect':[source.width//2,source.height//2,source.width//2+source.width,source.height//2+source.height]}
    atomic_json(directory/'psd-background.json',meta)
    return data,meta


def frame_geometry(meta,size,bg):
    w,h=size;l,t,r,b=meta['native_box'];sw,sh=r-l,b-t
    # Only generated margins stretch; the complete original photo stays uniform,
    # visible and contained, including extreme target aspect ratios.
    occupancy=.65+.35*(bg['zoom']-1)/2
    scale=min(w/sw,h/sh)*occupancy
    pw,ph=max(1,round(sw*scale)),max(1,round(sh*scale))
    x=round((w-pw)*bg['x']);y=round((h-ph)*bg['y'])
    return [x,y,x+pw,y+ph],scale


def center_subject(meta,size,bg):
    """Keep the detected subject centrally placed without cropping source pixels."""
    w,h=size;box=meta.get('subject_box',[0,0,1,1]);cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
    # Reduce only original-photo occupancy if an off-center native subject needs
    # more generated margin. All original content remains contained.
    original_zoom=bg['zoom']
    for step in range(41):
        bg['zoom']=max(1,original_zoom-step*.05)
        frame,_=frame_geometry(meta,size,bg);pw,ph=frame[2]-frame[0],frame[3]-frame[1]
        bg['x']=max(0,min(1,(w/2-cx*pw)/(w-pw))) if w>pw else .5
        bg['y']=max(0,min(1,(h/2-cy*ph)/(h-ph))) if h>ph else .5
        frame,_=frame_geometry(meta,size,bg);x,y=frame[:2]
        if .35*w-1<=x+cx*pw<=.65*w+1 and .35*h-1<=y+cy*ph<=.65*h+1:break
    return bg


def background_layers(directory,data,size,bg):
    meta=background_meta(directory,data)
    if not meta:
        # Honest local fallback; callers add a blocking "not expanded" warning.
        source,box,subject=build_background_source(directory,data)
        meta={'native_box':box,'subject_box':subject}
        frame,scale=frame_geometry(meta,size,bg)
        canvas=Image.new('RGBA',size,'white')
        canvas.alpha_composite(source.resize((frame[2]-frame[0],frame[3]-frame[1]),Image.Resampling.LANCZOS).convert('RGBA'),tuple(frame[:2]))
        return [('Background (not expanded)',canvas,0,0)],frame,meta,False
    frame,scale=frame_geometry(meta,size,bg);x,y,r,b=frame;w,h=size
    with Image.open(directory/meta['file']) as expanded:
        expanded=expanded.convert('RGBA');ew,eh=expanded.size
        original=meta.get('original_rect',[ew/4,eh/4,3*ew/4,3*eh/4])
        sx=[0,original[0],original[2],ew];sy=[0,original[1],original[3],eh]
        tx=[0,x,r,w];ty=[0,y,b,h]
        extension=Image.new('RGBA',size)
        for row in range(3):
            for col in range(3):
                if row==col==1:continue
                dw,dh=tx[col+1]-tx[col],ty[row+1]-ty[row]
                if dw<=0 or dh<=0:continue
                piece=expanded.resize((dw,dh),Image.Resampling.LANCZOS,box=(sx[col],sy[row],sx[col+1],sy[row+1]))
                extension.alpha_composite(piece,(tx[col],ty[row]))
    placed=[('AI background extension',extension,0,0)]
    left,top,right,bottom=meta['native_box']
    for info in data['layers']:
        if info['category']!='background':continue
        with Image.open(directory/info['file']) as image:
            nw=max(1,round(image.width*scale));nh=max(1,round(image.height*scale))
            image=resize_alpha(image.convert('RGBA'),(nw,nh))
        placed.append((info['name'],image,x+round((info['box'][0]-left)*scale),y+round((info['box'][1]-top)*scale)))
    # Blend only generated pixels at the seam; the complete original center is
    # never overwritten. This avoids a visible rectangular pasted-photo edge.
    pw,ph=r-x,b-y;center=Image.new('RGBA',(pw,ph),'white')
    for _,image,px,py in placed[1:]:center.alpha_composite(image,(px-x,py-y))
    def soften(edge,location,n,horizontal,reverse=False):
        if n<=0:return
        ramp=bytes(round(255*(i+1)/n) for i in range(n))
        if reverse:ramp=ramp[::-1]
        if horizontal:
            strip=edge.resize((pw,n));mask=Image.frombytes('L',(1,n),ramp).resize((pw,n))
        else:
            strip=edge.resize((n,ph));mask=Image.frombytes('L',(n,1),ramp).resize((n,ph))
        strip.putalpha(mask);extension.alpha_composite(strip,location)
    n=min(18,y);soften(center.crop((0,0,pw,1)),(x,y-n),n,True)
    n=min(18,h-b);soften(center.crop((0,ph-1,pw,ph)),(x,b),n,True,True)
    n=min(18,x);soften(center.crop((0,0,1,ph)),(x-n,y),n,False)
    n=min(18,w-r);soften(center.crop((pw-1,0,pw,ph)),(r,y),n,False,True)
    return placed,frame,meta,True
