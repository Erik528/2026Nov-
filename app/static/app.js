'use strict';
const $=id=>document.getElementById(id);
let cfg,job,layout,report,images={},polling=false,renderTimer,previewVersion=0,busy=false,pointer=null;
const ctx=$('canvas').getContext('2d');
function show(id,on=true){$(id).hidden=!on;}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);show('notice',!!text);}
async function api(url,options={}){
 const r=await fetch(url,options);let data;try{data=await r.json()}catch{throw Error('服务器响应无效，请检查服务是否运行。')}
 if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
}
function post(url,data){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});}
function asset(name){return `/api/jobs/${job.id}/assets/${name}`;}
function image(url){return new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(Error('无法加载图像素材'));im.src=url;});}
function steps(n){[1,2,3].forEach(i=>$(`step${i}`).classList.toggle('active',i===n));}
function sizeEnabled(on){document.querySelectorAll('.size').forEach(b=>b.disabled=!on);}
function setBusy(on){busy=on;sizeEnabled(!on&&!!job?.confirmed);for(const id of ['x','y','scale','bgx','bgy','bgzoom','layer','reset'])$(id).disabled=on;$('file').disabled=on;$('process').disabled=on||!job;$('resume').disabled=on;$('retry').disabled=on;}
function syncJob(){
 $('status').textContent=job.stage+(job.error?'：'+job.error:'');$('progress').value=job.progress;
 show('progress',job.status==='processing');show('retry',job.status==='review'||job.status==='error');show('resume',job.status==='error');
 setBusy(job.status==='processing');
 if(job.status==='error')notice(job.error,true);
}
async function upload(file){
 if(busy)return;
 if(!file)return;
 try{
  setBusy(true);notice('正在读取广告图…');
  const form=new FormData();form.append('file',file);
  job=await api('/api/jobs',{method:'POST',body:form});
  localStorage.setItem('ad-atelier-job',job.id);
  layout=null;report=null;images={};previewVersion++;clearTimeout(renderTimer);
  ['review','editor','assetsPanel','controls','safeToggleWrap'].forEach(id=>show(id,false));show('empty');
  $('export').disabled=true;$('downloads').replaceChildren();sizeEnabled(false);steps(1);
  $('source').src=asset('original.png');$('filename').textContent=job.filename;
  $('dimensions').textContent=`${job.width} × ${job.height} px · EXIF 方向已校正`;
  show('sourceInfo');notice('原图已就绪。点击“拆分并扩图”开始真实 API 处理。');syncJob();
 }catch(e){notice(e.message,true);setBusy(false);}
}
$('file').onchange=e=>upload(e.target.files[0]);
$('dropzone').ondragover=e=>{e.preventDefault();$('dropzone').classList.add('dragging');};
$('dropzone').ondragleave=()=>$('dropzone').classList.remove('dragging');
$('dropzone').ondrop=e=>{e.preventDefault();$('dropzone').classList.remove('dragging');upload(e.dataTransfer.files[0]);};
async function process(fresh=false){
 if(!job||busy)return;
 if(fresh&&!window.confirm('重新拆分将删除此任务的素材、构图和导出，并重新调用付费 API。继续？'))return;
 try{
  job=await post(`/api/jobs/${job.id}/process?fresh=${fresh}`);
  layout=null;report=null;images={};previewVersion++;clearTimeout(renderTimer);
  ['review','editor','assetsPanel','controls','safeToggleWrap'].forEach(id=>show(id,false));show('empty');sizeEnabled(false);$('export').disabled=true;$('downloads').replaceChildren();steps(1);
  notice('正在执行真实图像处理。可以保留此页面等待；刷新后也可恢复任务。');syncJob();poll();
 }catch(e){notice(e.message,true);}
}
$('process').onclick=()=>process();$('resume').onclick=()=>process();$('retry').onclick=()=>process(true);
async function poll(){
 if(polling)return;polling=true;
 try{
  while(job&&job.status==='processing'){
   await new Promise(r=>setTimeout(r,1800));job=await api(`/api/jobs/${job.id}`);syncJob();
  }
  if(job?.status==='review')await review();
 }catch(e){notice(e.message+' 可刷新页面恢复任务。',true);setBusy(false);}finally{polling=false;}
}
async function review(){
 syncJob();steps(2);show('empty',false);show('review');show('assetsPanel');
 const cache=`?v=${Date.now()}`;
 $('reviewOriginal').src=asset('work.png')+cache;$('reconstruction').src=asset('reconstructed.png')+cache;
 $('quality').replaceChildren();
 for(const warning of job.quality?.warnings||[]){const li=document.createElement('li');li.textContent=warning;$('quality').append(li);}
 const li=document.createElement('li');li.textContent=`原图重建平均像素误差：${job.quality?.reconstruction_mae??'—'} / 255（数值低不保证拆分正确）。`;$('quality').append(li);
 $('assets').replaceChildren();
 for(const [name,title,desc] of [['text.png','文字透明层','原始字形与颜色 · 栅格'],['graphics.png','图案透明层','Logo / 产品 / 人物'],['clean.png','干净底图','移除前景后的补全结果'],['expanded.png','四周扩展底图','宽、高各 2 倍 · 所有尺寸共用']]){
  const card=document.createElement('div');card.className='asset';const im=document.createElement('img');im.src=asset(name)+cache;im.alt=title;
  const a=document.createElement('a');a.href=asset(name);a.download=name;a.textContent=title+' ↓';
  const small=document.createElement('small');small.textContent=desc;card.append(im,a,small);$('assets').append(card);
 }
 notice('拆分完成，等待你审核。可下载素材放大检查；发现明显残留或破损时请重新处理。');
 if(job.confirmed){sizeEnabled(true);notice('已恢复已确认的拆分结果，可选择尺寸继续构图。');}
}
$('confirm').onclick=async()=>{try{job=await post(`/api/jobs/${job.id}/confirm`);sizeEnabled(true);await choose('1080x1080');}catch(e){notice(e.message,true);}};
async function choose(key,reset=false){
 if(busy)return;
 setBusy(true);$('downloads').replaceChildren();report=null;
 try{
  if(reset){ // Ask server for automatic layout explicitly, preserving saved versions unless reset is chosen.
   layout=await api(`/api/jobs/${job.id}/layout/${key}?reset=true`);
  }else layout=await api(`/api/jobs/${job.id}/layout/${key}`);
  const loaded=await Promise.all(['expanded','text','graphics'].map(async n=>[n,await image(asset(n+'.png')+`?v=${job.id}`)]));
  images=Object.fromEntries(loaded);show('review',false);show('empty',false);show('editor');show('controls');show('safeToggleWrap');steps(3);
  document.querySelectorAll('.size').forEach(b=>b.classList.toggle('selected',b.dataset.key===key));
  $('canvasTitle').textContent=key.replace('x',' × ')+' px';$('previewLabel').textContent='拖动选中图层 · 粉色为安全区外区域';
  updateFields();await refresh();notice('构图已生成。调整图层或背景裁切后，程序会重新检查安全区。');
 }catch(e){notice(e.message,true);}finally{setBusy(false);}
}
function selected(){return layout.layers.find(l=>l.id===$('layer').value);}
function updateFields(){
 const t=selected();$('x').value=t.x;$('y').value=t.y;$('scale').value=Math.round(t.scale*1000)/10;
 $('bgx').value=layout.background.x;$('bgy').value=layout.background.y;$('bgzoom').value=layout.background.zoom;
}
$('layer').onchange=()=>{if(layout){updateFields();draw();}};
function changed(){
 $('downloads').replaceChildren();$('export').disabled=true;report=null;previewVersion++;clearTimeout(renderTimer);draw();
 renderTimer=setTimeout(()=>refresh().catch(e=>notice(e.message,true)),250);
}
for(const id of ['x','y','scale'])$(id).oninput=()=>{if(!layout)return;const value=Number($(id).value);if(!Number.isFinite(value))return;selected()[id==='scale'?'scale':id]=id==='scale'?value/100:value;changed();};
for(const [id,key] of [['bgx','x'],['bgy','y'],['bgzoom','zoom']])$(id).oninput=()=>{if(layout){layout.background[key]=Number($(id).value);changed();}};
$('safeToggle').onchange=draw;
$('reset').onclick=()=>layout&&choose(layout.size,true);
async function refresh(){
 if(!layout)return;
 const version=++previewVersion;const copy=structuredClone(layout);$('export').disabled=true;
 const response=await post(`/api/jobs/${job.id}/preview`,copy);
 if(version!==previewVersion)return;
 report=response;images.preview=await image(response.preview+`?v=${Date.now()}`);
 if(version!==previewVersion)return;
 $('safeReport').textContent=report.warnings.length?report.warnings.join('；'):'✓ 文字与图案完整位于安全区内。';
 $('safeReport').classList.toggle('danger',!!report.warnings.length);
 $('export').disabled=!!report.warnings.length;draw();
}
function draw(){
 if(!layout||!images.expanded)return;
 const {width:w,height:h,safe}=cfg.sizes[layout.size];const canvas=$('canvas');canvas.width=w;canvas.height=h;
 const bg=images.expanded,b=layout.background,s=Math.max(w/bg.width,h/bg.height)*b.zoom;
 const cw=w/s,ch=h/s;ctx.drawImage(bg,(bg.width-cw)*b.x,(bg.height-ch)*b.y,cw,ch,0,0,w,h);
 // Use the server-rendered exact alpha crops and transforms when available. During
 // drag only that layer is translated visually, with final bounds checked server-side.
 if(images.preview&&report){ctx.drawImage(images.preview,0,0,w,h);}else{
  // Crop alpha extents once per source to match backend source-layer coordinates.
  for(const name of ['graphics','text']){
   const im=images[name];if(!im.crop){const c=document.createElement('canvas');c.width=im.width;c.height=im.height;const cx=c.getContext('2d',{willReadFrequently:true});cx.drawImage(im,0,0);const d=cx.getImageData(0,0,c.width,c.height).data;let l=c.width,t=c.height,r=0,b=0;for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++)if(d[(y*c.width+x)*4+3]){l=Math.min(l,x);t=Math.min(t,y);r=Math.max(r,x+1);b=Math.max(b,y+1);}im.crop=[l,t,r-l,b-t];}
   const [x,y,cw,ch]=im.crop,tr=layout.layers.find(l=>l.id===name);ctx.drawImage(im,x,y,cw,ch,tr.x,tr.y,Math.round(cw*tr.scale),Math.round(ch*tr.scale));
  }
 }
 if($('safeToggle').checked){const [l,t,r,b]=safe;ctx.fillStyle='rgba(235,104,147,.25)';ctx.fillRect(0,0,w,t);ctx.fillRect(0,b,w,h-b);ctx.fillRect(0,t,l,b-t);ctx.fillRect(r,t,w-r,b-t);ctx.strokeStyle='rgba(230,81,128,.8)';ctx.lineWidth=2;ctx.setLineDash([9,7]);ctx.strokeRect(l,t,r-l,b-t);ctx.setLineDash([]);}
 const bounds=report?.bounds.find(b=>b.id===$('layer').value);
 if(bounds){const [l,t,r,b]=bounds.box;ctx.strokeStyle='#254d36';ctx.lineWidth=3;ctx.strokeRect(l,t,r-l,b-t);}
}
function point(e){const rect=$('canvas').getBoundingClientRect();return [(e.clientX-rect.left)*$('canvas').width/rect.width,(e.clientY-rect.top)*$('canvas').height/rect.height];}
$('canvas').onpointerdown=e=>{if(!layout||busy)return;const [x,y]=point(e);pointer={x,y,startX:selected().x,startY:selected().y};$('canvas').setPointerCapture(e.pointerId);};
$('canvas').onpointermove=e=>{if(!pointer)return;const [x,y]=point(e);selected().x=Math.round(pointer.startX+x-pointer.x);selected().y=Math.round(pointer.startY+y-pointer.y);updateFields();changed();};
$('canvas').onpointerup=()=>{pointer=null;};$('canvas').onpointercancel=()=>{pointer=null;};
$('export').onclick=async()=>{
 try{setBusy(true);$('export').disabled=true;notice('正在写入并重新读取 PSD，检查图层、透明度与合成…');
  const out=await post(`/api/jobs/${job.id}/export`,structuredClone(layout));$('downloads').replaceChildren();
  for(const [url,label] of [[out.url,'下载分层 PSD ↓'],[out.preview,'下载合成 PNG ↓'],[asset('layout-'+layout.size+'.json'),'下载构图 JSON ↓'],[asset('validation-'+layout.size+'.json'),'PSD 检查报告 ↓']]){const a=document.createElement('a');a.href=url;a.download='';a.textContent=label;$('downloads').append(a);}
  notice(`PSD 已通过读取验证：${out.validation.canvas.join(' × ')} px，3 个独立图层。请下载后在 Photoshop 中检查。`);
 }catch(e){notice(e.message,true);}finally{setBusy(false);$('export').disabled=!report||!!report.warnings.length;}
};
(async()=>{
 try{
  cfg=await api('/api/config');
  for(const [key,spec] of Object.entries(cfg.sizes)){
   const b=document.createElement('button');b.className='size';b.dataset.key=key;b.disabled=true;b.innerHTML=`<span class="icon"></span><b>${spec.width} × ${spec.height}</b><small>${{'1080x1080':'FEED · 1:1','1080x1350':'FEED · 4:5','1080x1920':'STORY · 9:16','1200x628':'LANDSCAPE'}[key]}</small>`;b.onclick=()=>choose(key);$('sizes').append(b);
  }
  if(!cfg.api_configured)notice('API 尚未配置：在环境设置或本机 .env 配置 AD_OPENAI_API_KEY 后重启。上传、构图和 PSD 功能不需要外部服务；拆分与扩图需要。');
  const previous=localStorage.getItem('ad-atelier-job');
  if(previous){try{job=await api('/api/jobs/'+previous);$('source').src=asset('original.png');$('filename').textContent=job.filename;$('dimensions').textContent=`${job.width} × ${job.height} px`;show('sourceInfo');syncJob();if(job.status==='processing')poll();else if(job.status==='review')await review();}catch{localStorage.removeItem('ad-atelier-job');job=null;}}
 }catch(e){notice(e.message,true);}
})();
