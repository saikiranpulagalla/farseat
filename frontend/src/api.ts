import type { AnalysisResponse, PresentationResponse, RendererPageManifest, SeatDetailResponse } from './types';
const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

function fallbackCode(status:number):string{
  if(status===413) return 'FILE_LIMIT_EXCEEDED';
  if(status===429) return 'SERVICE_BUSY';
  if(status===503) return 'SERVICE_BUSY';
  return `HTTP_${status}`;
}

async function decode<T>(r:Response):Promise<T>{
  if(!r.ok){
    let detail:any={}; try{ detail=await r.json(); }catch{}
    const code=typeof detail?.detail?.code==='string'?detail.detail.code:fallbackCode(r.status);
    throw new Error(code);
  }
  if(r.status===204) return undefined as T;
  return r.json() as Promise<T>;
}
export async function uploadPresentation(file:File, signal?:AbortSignal){ const fd=new FormData();fd.append('file',file);return decode<PresentationResponse>(await fetch(`${BASE}/api/presentations`,{method:'POST',body:fd,signal})); }
export async function analyzePresentation(args:{token:string;presentationId:string;requestId:string;display:{width_m:number;height_m:number};room:{width_m:number;depth_m:number};seatGrid:any;rendererManifest:RendererPageManifest[]},signal?:AbortSignal){
 return decode<AnalysisResponse>(await fetch(`${BASE}/api/analyze`,{method:'POST',headers:{'Content-Type':'application/json','X-FarSeat-Token':args.token},signal,body:JSON.stringify({schema_version:'1.2',request_id:args.requestId,presentation_id:args.presentationId,display:{...args.display,center_x_m:0,bottom_z_m:0},room:args.room,seat_grid:args.seatGrid,profile:'PUBLIC_AVIXA_BDM_REFERENCE_V1',renderer_manifest:args.rendererManifest})}));
}
export async function fetchSeatDetail(analysisId:string,seatId:string,token:string,signal?:AbortSignal){ return decode<SeatDetailResponse>(await fetch(`${BASE}/api/analyses/${analysisId}/seats/${seatId}`,{headers:{'X-FarSeat-Token':token},signal})); }
export async function deletePresentation(id:string,token:string){ try{ await fetch(`${BASE}/api/presentations/${id}`,{method:'DELETE',headers:{'X-FarSeat-Token':token}});}catch{} }
