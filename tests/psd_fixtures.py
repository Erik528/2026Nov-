"""Explicit LOCAL geometry fixture; NOT an AI result or end-to-end API claim."""
import json
from PIL import Image
from app.psd_workflow import build_background_source,background_signature,atomic_json
from app.psd_import import catalog


def prepared_geometry(directory):
    data=catalog(directory)
    for info in data['layers']:info['role_source']='manual'
    data['analysis_verified']=True
    atomic_json(directory/'psd-layers.json',data)
    source,box,subject=build_background_source(directory,data)
    source.save(directory/'background-original.png')
    # Known plain/synthetic margin pixels exercise the framing math. This helper
    # never touches a provider and is used ONLY by offline tests.
    expanded=Image.new('RGB',(source.width*2,source.height*2),'#91a6aa')
    x,y=source.width//2,source.height//2;expanded.paste(source,(x,y))
    signature=background_signature(data);file=f'psd-expanded-{signature}.png';expanded.save(directory/file)
    meta={'signature':signature,'file':file,'native_box':box,'subject_box':subject,
          'source_size':list(source.size),'expanded_size':list(expanded.size),
          'original_rect':[x,y,x+source.width,y+source.height]}
    atomic_json(directory/'psd-background.json',meta)
    return data,meta
