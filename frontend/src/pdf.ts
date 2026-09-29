import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { RendererPageManifest } from './types';
GlobalWorkerOptions.workerSrc = workerSrc;

function abortError():DOMException{return new DOMException('Aborted','AbortError');}

export async function rendererManifest(url:string, signal?:AbortSignal):Promise<RendererPageManifest[]>{
 const task=getDocument({url,stopAtErrors:true}); let aborted=false;
 const onAbort=()=>{aborted=true;void task.destroy();};
 if(signal?.aborted){onAbort();throw abortError();}
 signal?.addEventListener('abort',onAbort,{once:true});
 try{
   const doc=await task.promise; const out:RendererPageManifest[]=[];
   try{
     for(let i=1;i<=doc.numPages;i++){
       if(signal?.aborted) throw abortError();
       const page=await doc.getPage(i); const vp=page.getViewport({scale:1}); const rot=((page.rotate%360)+360)%360 as 0|90|180|270; const [x0,y0,x1,y1]=page.view;
       out.push({page_number:i,rotation_deg:rot,viewport_width:vp.width,viewport_height:vp.height,view_x0:x0,view_y0:y0,view_x1:x1,view_y1:y1});
     }
   } finally { await doc.destroy(); }
   return out;
 }catch(e){ if(aborted||signal?.aborted) throw abortError(); throw e; }
 finally{ signal?.removeEventListener('abort',onAbort); }
}

export async function openPdf(url:string):Promise<PDFDocumentProxy>{ return getDocument({url,stopAtErrors:true}).promise; }

export async function renderPage(doc:PDFDocumentProxy,pageNo:number,canvas:HTMLCanvasElement,cssWidth:number):Promise<{width:number;height:number;task:RenderTask}>{
 const page=await doc.getPage(pageNo); const base=page.getViewport({scale:1}); const scale=cssWidth/base.width; const vp=page.getViewport({scale}); const ratio=Math.max(1,window.devicePixelRatio||1);
 canvas.width=Math.max(1,Math.floor(vp.width*ratio)); canvas.height=Math.max(1,Math.floor(vp.height*ratio)); canvas.style.width=`${vp.width}px`; canvas.style.height=`${vp.height}px`;
 const ctx=canvas.getContext('2d'); if(!ctx) throw new Error('CANVAS_UNAVAILABLE');
 // PDF.js' supported HiDPI pattern is an explicit render transform. Do not rely on a
 // pre-set canvas context transform that the renderer may save/reset internally.
 const transform=ratio===1?undefined:[ratio,0,0,ratio,0,0] as [number,number,number,number,number,number];
 const task=page.render({canvas,canvasContext:ctx,viewport:vp,transform}); return {width:vp.width,height:vp.height,task};
}
