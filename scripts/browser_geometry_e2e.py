#!/usr/bin/env python3
"""Real PDF.js geometry qualification against generated, independent PDF fixtures."""
from __future__ import annotations
import json, os, subprocess, tempfile, time, traceback
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]; REPORT=ROOT/'validation'/'reports'/'browser-geometry.json'
BASE=os.getenv('FARSEAT_E2E_BASE','http://127.0.0.1:8081'); TOL=2.0

CASES=[
 ('rotation-0',0,None,(0,0,800,600)),('rotation-90',90,None,(0,0,800,600)),
 ('rotation-180',180,None,(0,0,800,600)),('rotation-270',270,None,(0,0,800,600)),
 ('cropbox',0,(100,80,700,520),(0,0,800,600)),('origin',0,None,(100,200,900,800)),
 ('crop-origin',0,(180,260,820,740),(100,200,900,800)),
 ('crop-90',90,(100,80,700,520),(0,0,800,600)),('crop-270',270,(100,80,700,520),(0,0,800,600)),
]

def identity():
 p=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True)
 d=subprocess.run(['git','status','--porcelain'],cwd=ROOT,text=True,capture_output=True,check=True)
 return p.stdout.strip(),not not d.stdout.strip()

def fixture(path:Path, name:str, rotation:int, crop, media):
 # Independent fixture coordinates are expressed in the final source frame.
 # pypdf's later MediaBox-origin change offsets ReportLab's original x=0
 # content coordinate, so compensate while drawing to keep the target at the
 # intended asymmetrical visible-frame location.
 tx,ty=260+media[0],330
 # A rotated page needs equally rotated source text to remain visually horizontal;
 # this is the supported case covered by the parser's page-rotation contract.
 b=BytesIO(); c=canvas.Canvas(b,pagesize=(800,600));
 if rotation: c.saveState(); c.translate(400,300); c.rotate(rotation); c.translate(-400,-300)
 c.setFillColorRGB(.95,.85,.2); c.rect(tx,ty,360,30,fill=1,stroke=1)
 c.setFillColorRGB(0,0,0); c.setFont('Helvetica',10); c.drawString(tx+15,ty+12,f'FARSEAT_GEOMETRY_{name.upper()}')
 if rotation: c.restoreState()
 c.save()
 r=PdfReader(b); w=PdfWriter(); page=r.pages[0]; page.mediabox.lower_left=(media[0],media[1]); page.mediabox.upper_right=(media[2],media[3])
 if crop: page.cropbox.lower_left=(crop[0],crop[1]); page.cropbox.upper_right=(crop[2],crop[3])
 if rotation: page.rotate(rotation)
 w.add_page(page)
 with path.open('wb') as f:w.write(f)
 # The marker rectangle provides the independent visible-frame oracle.
 frame=crop or media; return {'id':name,'marker':f'FARSEAT_GEOMETRY_{name.upper()}','rotation':rotation,'cropbox':crop,'mediabox':media,'source_rect':[tx,ty,tx+360,ty+30],'visible_frame':frame,'content_compensation_deg':rotation}

def upload_analyze(page,path):
 page.goto(BASE,wait_until='networkidle'); page.locator('input[type=file]').set_input_files(str(path))
 try: expect(page.get_by_text('PRESENTATION READY')).to_be_visible(timeout=30000)
 except Exception as e: raise AssertionError(f"upload {path.name} did not become ready: {page.locator('body').inner_text()[-1800:]}") from e
 page.get_by_role('button',name='Analyze modeled seats').click()
 expect(page.locator('#results-heading')).to_be_visible(timeout=20000); page.locator('.seat').last.click()
 expect(page.get_by_text('Loading seat details…')).not_to_be_visible(timeout=15000)

def check(page,meta,shots:Path):
 rows=page.locator('button.element-row'); row=rows.filter(has_text=meta['marker']).first
 if row.count()!=1: raise AssertionError(f"{meta['id']}: target detail unavailable ({rows.count()} rows); page={page.locator('body').inner_text()[-1200:]}")
 row.click(); expect(page.locator('.highlight')).to_be_visible(timeout=15000)
 page.wait_for_function("""() => { const c=document.querySelector('.pdf-wrap canvas'); const r=c?.getBoundingClientRect(); return !!r && r.width>100 && r.height>100; }""",timeout=15000)
 wrap=page.locator('.pdf-wrap').bounding_box(); hi=page.locator('.highlight').bounding_box(); canvas_box=page.locator('.pdf-wrap canvas').bounding_box()
 if not wrap or not hi or not canvas_box: raise AssertionError(f"{meta['id']}: missing PDF.js geometry")
 # Overlay edge agreement is tested against its CSS percentage mapping, read directly from style.
 style=page.locator('.highlight').get_attribute('style') or ''
 vals={k:float(v.strip('%'))/100 for k,v in __import__('re').findall(r'(left|top|width|height):\s*([0-9.]+)%',style)}
 # The percentage containing block is the wrapper's inner content box. The
 # rendered PDF.js canvas is that content box; the wrapper's 1px border is not.
 expected={'left':canvas_box['x']+vals['left']*canvas_box['width'],'top':canvas_box['y']+vals['top']*canvas_box['height'],'right':canvas_box['x']+(vals['left']+vals['width'])*canvas_box['width'],'bottom':canvas_box['y']+(vals['top']+vals['height'])*canvas_box['height']}
 actual={'left':hi['x'],'top':hi['y'],'right':hi['x']+hi['width'],'bottom':hi['y']+hi['height']}
 delta=max(abs(expected[k]-actual[k]) for k in expected)
 if delta>TOL: raise AssertionError(f"{meta['id']}: CSS mapping delta {delta:.3f}px expected={expected} actual={actual} wrap={wrap} canvas={canvas_box} style={style}")
 # Independent fixture oracle: the source-owned yellow rectangle must be
 # visible beneath the browser overlay. It is not derived from API geometry.
 shot=shots/f"{meta['id']}.png"; page.screenshot(path=str(shot),full_page=True)
 with Image.open(shot).convert('RGB') as image:
  x0=max(0,int(hi['x']-4)); y0=max(0,int(hi['y']-4)); x1=min(image.width,int(hi['x']+hi['width']+4)); y1=min(image.height,int(hi['y']+hi['height']+4))
  yellow=sum(1 for r,g,b in image.crop((x0,y0,x1,y1)).getdata() if r>190 and g>130 and b<110)
 if yellow<3: raise AssertionError(f"{meta['id']}: overlay does not intersect independently drawn target rectangle")
 return {'id':meta['id'],'rotation':meta['rotation'],'cropbox':bool(meta['cropbox']),'non_zero_origin':meta['mediabox'][:2]!=(0,0),'max_edge_delta_px':round(delta,3),'independent_oracle_yellow_pixels':yellow,'result':'PASS','canvas_css':[round(canvas_box['width'],2),round(canvas_box['height'],2)]}

def run_once(dpr=1):
 with tempfile.TemporaryDirectory(prefix='farseat-geometry-') as td, sync_playwright() as p:
  td=Path(td); shots=ROOT/'validation'/'reports'/'geometry-screenshots'; shots.mkdir(parents=True,exist_ok=True)
  launch={'headless':True,'args':['--no-sandbox']}; exe=os.getenv('FARSEAT_CHROMIUM_EXECUTABLE');
  if exe: launch['executable_path']=exe
  browser=p.chromium.launch(**launch); context=browser.new_context(viewport={'width':1200,'height':900},device_scale_factor=dpr); page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
  out=[]
  try:
   for n,rot,crop,media in CASES:
    path=td/f'{n}.pdf'; meta=fixture(path,n,rot,crop,media); upload_analyze(page,path); out.append(check(page,meta,shots)); page.get_by_role('button',name='Close').click()
    with page.expect_response(lambda r: r.request.method=='DELETE' and '/api/presentations/' in r.url,timeout=15000): page.get_by_role('button',name='New analysis').click()
   return out,errors
  finally: context.close(); browser.close()

def main():
 commit,dirty=identity(); started=time.monotonic()
 try:
  one,errors=run_once(1); two,more=run_once(1); three,more2=run_once(1); dpr2,more3=run_once(2); errors+=more+more2+more3
  selected=[x for x in dpr2 if x['id'] in {'rotation-0','rotation-90','crop-90'}]
  report={'status':'PASS','source_commit':commit,'source_dirty':dirty,'runs':[one,two,three],'dpr2':selected,'console_errors':errors,'duration_seconds':round(time.monotonic()-started,2)}; code=0
 except Exception as e: report={'status':'FAIL','source_commit':commit,'source_dirty':dirty,'error':repr(e),'traceback':traceback.format_exc()}; code=1
 REPORT.write_text(json.dumps(report,indent=2)); print(json.dumps(report,indent=2)); return code
if __name__=='__main__': raise SystemExit(main())
