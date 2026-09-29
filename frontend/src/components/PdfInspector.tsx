import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { openPdf, renderPage } from '../pdf';
import type { SeatElementDetail } from '../types';

export function PdfInspector({url,detail,onClose}:{url:string;detail:SeatElementDetail;onClose:()=>void}){
 const canvasRef=useRef<HTMLCanvasElement>(null); const stageRef=useRef<HTMLDivElement>(null); const headingRef=useRef<HTMLHeadingElement>(null);
 const [doc,setDoc]=useState<PDFDocumentProxy|null>(null); const [size,setSize]=useState({width:1,height:1}); const [renderError,setRenderError]=useState('');
 const taskRef=useRef<RenderTask|null>(null);
 useEffect(()=>{ headingRef.current?.focus(); },[]);
 useEffect(()=>{ let dead=false; let d:PDFDocumentProxy|null=null; setRenderError(''); openPdf(url).then(x=>{if(dead){void x.destroy();return;} d=x;setDoc(x);}).catch(()=>{if(!dead)setRenderError('Could not open this slide for visual inspection.');}); return()=>{dead=true;taskRef.current?.cancel();if(d)void d.destroy();};},[url]);
 useEffect(()=>{
   if(!doc||!canvasRef.current||!stageRef.current)return; let cancelled=false;
   const run=async()=>{
     taskRef.current?.cancel(); setRenderError('');
     const available=stageRef.current!.clientWidth; const width=Math.max(240,Math.min(900,available));
     try{
       const r=await renderPage(doc,detail.page_number,canvasRef.current!,width); taskRef.current=r.task;
       await r.task.promise;if(!cancelled)setSize({width:r.width,height:r.height});
     }catch(e:any){if(e?.name!=='RenderingCancelledException'&&!cancelled)setRenderError('Could not render this slide. Try another element or reopen the inspector.');}
   };
   void run(); const ro=new ResizeObserver(()=>void run());ro.observe(stageRef.current);return()=>{cancelled=true;ro.disconnect();taskRef.current?.cancel();};
 },[doc,detail.page_number]);
 const b=detail.logical_bbox;
 return <section className="inspector" aria-labelledby="slide-title">
  <div className="panel-head"><div><div className="eyebrow">SLIDE INSPECTOR</div><h3 ref={headingRef} tabIndex={-1} id="slide-title">Slide {detail.page_number}</h3></div><button className="ghost" onClick={onClose}>Close</button></div>
  {renderError&&<p className="error" role="alert">{renderError}</p>}
  <div ref={stageRef} className="pdf-stage">
    <div className="pdf-wrap" style={{width:size.width,maxWidth:'100%'}}>
      <canvas ref={canvasRef}/>
      {!renderError&&b&&<div className="highlight" aria-hidden="true" style={{left:`${b.x0*100}%`,top:`${b.y0*100}%`,width:`${(b.x1-b.x0)*100}%`,height:`${(b.y1-b.y0)*100}%`}}/>}
    </div>
  </div>
  <div className="metric-grid">
    <div><span>Element</span><strong>{detail.text_preview || 'Supported text element'}</strong></div>
    <div><span>Measured height</span><strong>{detail.element_height_pct==null?'—':`${detail.element_height_pct.toFixed(3)}%`}</strong></div>
    <div><span>Reference target</span><strong>{detail.target_height_pct==null?'—':`${detail.target_height_pct.toFixed(3)}%`}</strong></div>
    <div><span>Margin</span><strong>{detail.margin==null?'—':`${detail.margin.toFixed(2)}×`}</strong></div>
  </div>
  <p className="disclaimer">FarSeat evaluates structural presentation geometry against the selected public BDM reference profile. It does not determine whether a particular person can read the content.</p>
 </section>;
}
