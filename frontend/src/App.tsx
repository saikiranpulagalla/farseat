import { useEffect, useRef, useState } from 'react';
import type { MouseEvent } from 'react';
import { analyzePresentation, deletePresentation, fetchSeatDetail, uploadPresentation } from './api';
import { rendererManifest } from './pdf';
import type { AnalysisResponse, PresentationResponse, RendererPageManifest, SeatDetailResponse, SeatElementDetail } from './types';
import { RoomMap } from './components/RoomMap';
import { PdfInspector } from './components/PdfInspector';
import { LatestRequestGate } from './requestCoordinator';
import { resultsHeadline } from './logic';

type Step='UPLOAD'|'PARSING'|'SETUP'|'ANALYZING'|'RESULTS'|'ERROR';
type Unit='m'|'ft';
type RoomConfig={displayW:number;displayH:number;roomW:number;roomD:number;rows:number;seats:number;first:number;rowGap:number;seatGap:number};
const FT=3.280839895013123;
const display=(m:number,u:Unit)=>u==='m'?m:m*FT;
const toM=(v:number,u:Unit)=>u==='m'?v:v/FT;
const DEFAULT_CFG:RoomConfig={displayW:3.2,displayH:1.8,roomW:9,roomD:12,rows:5,seats:6,first:2.2,rowGap:1.8,seatGap:1.1};
const SAMPLE_CFG:RoomConfig={...DEFAULT_CFG};

function configError(cfg:RoomConfig):string|null{
  const dims=[cfg.displayW,cfg.displayH,cfg.roomW,cfg.roomD,cfg.first,cfg.rowGap,cfg.seatGap];
  if(!dims.every(v=>Number.isFinite(v)&&v>0)) return 'All physical dimensions must be finite positive numbers.';
  if(!Number.isInteger(cfg.rows)||!Number.isInteger(cfg.seats)||cfg.rows<1||cfg.seats<1) return 'Rows and seats per row must be positive whole numbers.';
  if(cfg.displayW>cfg.roomW) return 'Usable display width must fit within the room width.';
  if(cfg.rows*cfg.seats>200) return 'Modeled seat count cannot exceed 200.';
  return null;
}

export default function App(){
 const [step,setStep]=useState<Step>('UPLOAD');
 const [error,setError]=useState('');
 const [seatError,setSeatError]=useState('');
 const [file,setFile]=useState<File|null>(null);
 const [url,setUrl]=useState<string|null>(null);
 const [presentation,setPresentation]=useState<PresentationResponse|null>(null);
 const [token,setToken]=useState('');
 const [manifest,setManifest]=useState<RendererPageManifest[]>([]);
 const [analysis,setAnalysis]=useState<AnalysisResponse|null>(null);
 const [detail,setDetail]=useState<SeatDetailResponse|null>(null);
 const [selected,setSelected]=useState('');
 const [inspect,setInspect]=useState<SeatElementDetail|null>(null);
 const [unit,setUnit]=useState<Unit>('m');
 const [cfg,setCfg]=useState<RoomConfig>({...DEFAULT_CFG});
 const generation=useRef(0);
 const abort=useRef<AbortController|null>(null);
 const seatGate=useRef(new LatestRequestGate());
 const inspectorOrigin=useRef<HTMLElement|null>(null);

 useEffect(()=>()=>{abort.current?.abort();seatGate.current.cancel();},[]);

 const reset=async()=>{
   generation.current++;
   abort.current?.abort(); abort.current=null;
   seatGate.current.cancel();
   setAnalysis(null);setDetail(null);setSelected('');setInspect(null);setStep('UPLOAD');setError('');setSeatError('');
   if(url)URL.revokeObjectURL(url);setUrl(null);setFile(null);setManifest([]);
   setCfg({...DEFAULT_CFG});setUnit('m');
   const p=presentation,t=token;setPresentation(null);setToken('');if(p&&t)void deletePresentation(p.presentation_id,t);
 };

 const loadSample=async()=>{
   try{
     const r=await fetch('/farseat-demo.pdf');
     if(!r.ok) throw new Error(`HTTP_${r.status}`);
     const b=await r.blob();
     setCfg({...SAMPLE_CFG});setUnit('m');
     const f=new File([b],'farseat-demo.pdf',{type:'application/pdf'});
     await choose(f);
   }catch(e:any){setError(e?.message||'Could not load the bundled sample.');setStep('ERROR');}
 };

 const choose=async(f:File)=>{
   const g=++generation.current;
   abort.current?.abort();seatGate.current.cancel();abort.current=new AbortController();
   const signal=abort.current.signal;
   if(url)URL.revokeObjectURL(url);
   const local=URL.createObjectURL(f);setUrl(local);setFile(f);setStep('PARSING');setError('');setSeatError('');setAnalysis(null);setDetail(null);setSelected('');setInspect(null);
   let uploaded:PresentationResponse|null=null;
   let cleaned=false;
   const cleanupUploaded=async()=>{
     if(cleaned||!uploaded?.capability_token)return;
     cleaned=true; await deletePresentation(uploaded.presentation_id,uploaded.capability_token);
   };
   try{
     const [uploadResult,manifestResult]=await Promise.allSettled([
       uploadPresentation(f,signal),
       rendererManifest(local,signal),
     ]);
     if(uploadResult.status==='fulfilled') uploaded=uploadResult.value;
     if(g!==generation.current){await cleanupUploaded();URL.revokeObjectURL(local);return;}
     if(uploadResult.status==='rejected') throw uploadResult.reason;
     if(manifestResult.status==='rejected'){await cleanupUploaded();throw manifestResult.reason;}
     const p=uploadResult.value,m=manifestResult.value;
     if(!p.capability_token){uploaded=p;await cleanupUploaded();throw new Error('MISSING_CAPABILITY_TOKEN');}
     setPresentation(p);setToken(p.capability_token);setManifest(m);setStep('SETUP');
   }catch(e:any){
     await cleanupUploaded();
     if(g!==generation.current){URL.revokeObjectURL(local);return;}
     if(e?.name==='AbortError')return;
     URL.revokeObjectURL(local);setUrl(null);setFile(null);setPresentation(null);setToken('');setManifest([]);
     setError(e?.message||'Upload failed');setStep('ERROR');
   }
 };

 const update=(k:keyof RoomConfig,v:number)=>setCfg(c=>({...c,[k]:v}));
 const run=async()=>{
   if(!presentation||!token)return;
   const invalid=configError(cfg);if(invalid){setError(invalid);setStep('ERROR');return;}
   const g=++generation.current;abort.current?.abort();seatGate.current.cancel();abort.current=new AbortController();setStep('ANALYZING');setError('');setSeatError('');setSelected('');setDetail(null);setInspect(null);
   try{
     const a=await analyzePresentation({token,presentationId:presentation.presentation_id,requestId:crypto.randomUUID(),display:{width_m:cfg.displayW,height_m:cfg.displayH},room:{width_m:cfg.roomW,depth_m:cfg.roomD},seatGrid:{rows:cfg.rows,seats_per_row:cfg.seats,first_row_distance_m:cfg.first,row_spacing_m:cfg.rowGap,seat_spacing_m:cfg.seatGap,center_x_m:0},rendererManifest:manifest},abort.current.signal);
     if(g!==generation.current)return;
     setAnalysis(a);setStep('RESULTS');setTimeout(()=>{if(g===generation.current)document.getElementById('results-heading')?.focus();},0);
   }catch(e:any){
     if(g!==generation.current)return;
     if(e?.name==='AbortError')return;
     setError(e?.message||'Analysis failed');setStep('ERROR');
   }
 };

 const seatClick=async(id:string)=>{
   if(!analysis||!token)return;
   const activeAnalysisId=analysis.analysis_id;
   const request=seatGate.current.begin();
   setSelected(id);setDetail(null);setInspect(null);setSeatError('');
   try{
     const d=await fetchSeatDetail(activeAnalysisId,id,token,request.signal);
     if(!seatGate.current.isCurrent(request.sequence))return;
     if(d.analysis_id!==activeAnalysisId || d.seat.seat_id!==id)return;
     setDetail(d);
   }catch(e:any){if(e?.name==='AbortError')return;if(seatGate.current.isCurrent(request.sequence))setSeatError(e?.message||'Seat detail failed');}
 };

 const openInspector=(d:SeatElementDetail,e:MouseEvent<HTMLElement>)=>{inspectorOrigin.current=e.currentTarget;setInspect(d);};
 const closeInspector=()=>{setInspect(null);setTimeout(()=>inspectorOrigin.current?.focus(),0);};
 const field=(label:string,key:keyof RoomConfig,meters=true,stepN=.1)=><label>{label}<input type="number" min={meters?0.001:1} step={stepN} value={meters?Number(display(cfg[key] as number,unit).toFixed(3)):cfg[key]} onChange={e=>update(key,meters?toM(Number(e.target.value),unit):Number(e.target.value))}/></label>;

 if(step==='UPLOAD') return <main className="shell hero"><div className="brand">FarSeat</div><h1>See how presentation text geometry changes across seats in a room.</h1><p>Upload a machine-generated PDF, describe the usable display area and seating layout, and FarSeat traces review results back to a modeled seat, slide, and analyzed text element.</p><div className="hero-actions"><label className="upload">Choose PDF<input hidden type="file" accept="application/pdf" onChange={e=>{const f=e.target.files?.[0];if(f)void choose(f);}}/></label><button className="ghost" onClick={()=>void loadSample()}>Try sample classroom</button></div><p className="fine">PDF only · up to 20 MB · temporary in-memory analysis model</p></main>;
 if(step==='PARSING') return <main className="shell"><div className="brand">FarSeat</div><div className="loading">Parsing presentation geometry…</div></main>;
 if(step==='ERROR') return <main className="shell"><div className="brand">FarSeat</div><section className="card"><h2>Couldn’t complete that step</h2><p className="error">{error}</p><button onClick={()=>setStep(presentation?'SETUP':'UPLOAD')}>Try again</button><button className="ghost" onClick={()=>void reset()}>New analysis</button></section></main>;
 if(step==='SETUP'&&presentation){
   const nonTextPages=presentation.pages.filter(p=>p.non_text_content==='PRESENT').length;
   return <main className="shell"><header><div className="brand">FarSeat</div><button className="ghost" onClick={()=>void reset()}>New analysis</button></header><section className="card"><div className="eyebrow">PRESENTATION READY</div><h2>{presentation.filename}</h2><p><strong>{presentation.page_count}</strong> slides · <strong>{presentation.analyzable_element_count}</strong> text elements analyzed · <strong>{presentation.unsupported_element_count}</strong> text elements not analyzed · <strong>{presentation.unanalyzable_page_count}</strong> slides with no analyzable text</p>{nonTextPages>0&&<p className="note">? {nonTextPages} slides contain graphical content that FarSeat does not analyze.</p>}<p className="note">FarSeat never treats content it could not analyze as passing.</p></section><section className="card"><div className="panel-head"><h2>Set up room</h2><div className="seg" role="group" aria-label="Measurement units"><button aria-pressed={unit==='m'} className={unit==='m'?'active':''} onClick={()=>setUnit('m')}>Metric</button><button aria-pressed={unit==='ft'} className={unit==='ft'?'active':''} onClick={()=>setUnit('ft')}>Imperial</button></div></div><div className="form-grid">{field(`Usable display width (${unit})`,'displayW')}{field(`Usable display height (${unit})`,'displayH')}{field(`Room width (${unit})`,'roomW')}{field(`Room depth (${unit})`,'roomD')}{field('Rows','rows',false,1)}{field('Seats per row','seats',false,1)}{field(`First-row distance (${unit})`,'first')}{field(`Row spacing (${unit})`,'rowGap')}{field(`Seat spacing (${unit})`,'seatGap')}</div><p className="fine">Use the rectangular area where presentation content can appear. Do not enter the display diagonal or bezel dimensions.</p><button onClick={()=>void run()}>Analyze modeled seats</button></section></main>;
 }
 if(step==='ANALYZING') return <main className="shell"><div className="brand">FarSeat</div><div className="loading">Analyzing presentation geometry and modeled seats…</div></main>;
 if(step==='RESULTS'&&analysis){
   const current=analysis.seats.find(s=>s.seat_id===selected);
   const safeDetail=detail?.analysis_id===analysis.analysis_id&&detail.seat.seat_id===selected?detail:null;
   const affected=safeDetail?.details.filter(d=>d.state==='BELOW_TARGET')??[];
   const unknown=safeDetail?.details.filter(d=>d.state==='NOT_ANALYZED')??[];
   const outside=safeDetail?.details.filter(d=>d.state==='OUTSIDE_REFERENCE_RANGE')??[];
   const invalid=safeDetail?.details.filter(d=>d.state==='INVALID_GEOMETRY')??[];
   const incompleteValid=analysis.seats.filter(s=>s.geometry_state==='VALID'&&s.coverage_state!=='COMPLETE').length;
   const headline=resultsHeadline(analysis.seats,analysis.affected_valid_seat_count,analysis.valid_seat_count);
   return <main className="shell wide"><header><div><div className="brand">FarSeat</div><div className="fine">{presentation?.filename}</div></div><button className="ghost" onClick={()=>void reset()}>New analysis</button></header>
     <h1 id="results-heading" tabIndex={-1}>{headline}</h1>
     <p className="sr-only" role="status" aria-live="polite">Analysis complete. {headline}{incompleteValid>0?` ${incompleteValid} valid seats have incomplete analysis coverage.`:''}</p>
     {incompleteValid>0&&<p className="note">? {incompleteValid} valid modeled seats have incomplete analyzed-text coverage.</p>}
     {analysis.slides.some(s=>s.non_text_content==='PRESENT')&&<p className="note">ⓘ Some slides also contain non-text content that FarSeat does not analyze.</p>}
     {analysis.invalid_seat_count>0&&<p className="note">{analysis.invalid_seat_count} configured seats have invalid geometry.</p>}
     <div className="results-grid"><section className="card"><div className="eyebrow">ROOM MAP</div><RoomMap seats={analysis.seats} roomWidth={cfg.roomW} roomDepth={cfg.roomD} displayWidth={cfg.displayW} displayHeight={cfg.displayH} selected={selected} onSelect={id=>void seatClick(id)}/><div className="legend"><span>✓ Meets target</span><span>! Review recommended</span><span>? Not analyzed</span><span>↗ Outside reference range</span></div></section>
     <aside className="card detail"><div className="eyebrow">DETAIL</div>{!current?<p>Select a seat to inspect its results.</p>:<><h2>Row {current.row} · Seat {current.number}</h2><p>{current.y_m.toFixed(2)} m perpendicular distance</p><div className="stats"><div><strong>{current.below_target_count}</strong><span>below target</span></div><div><strong>{current.meets_target_count}</strong><span>meet target</span></div><div><strong>{current.not_analyzed_count}</strong><span>not analyzed</span></div><div><strong>{current.outside_range_count}</strong><span>outside range</span></div></div>{current.coverage_state!=='COMPLETE'&&<p className="note">? Analysis coverage is {current.coverage_state.toLowerCase()}.</p>}{current.has_outside_reference_results&&<p className="note">↗ Some comparisons are outside the public reference range.</p>}
       {current.geometry_state==='INVALID'?<p className="error">No seat-level target conclusion is available because this seat has invalid geometry.</p>:
        !safeDetail?seatError?<><p className="error">Element details are temporarily unavailable: {seatError}</p>{current.below_target_count>0&&<p>{current.below_target_count} analyzed elements are still reported below target in the seat summary.</p>}</>:<p>Loading seat details…</p>:
        <><h3>Affected elements</h3>{affected.length===0?<p>No analyzed text elements below target for this seat.</p>:affected.map(d=><button className="element-row" key={d.element_id} onClick={e=>openInspector(d,e)}><span>Slide {d.page_number}</span><strong>{d.text_preview||d.element_id}</strong><small>{d.element_height_pct?.toFixed(2)}% measured · {d.target_height_pct?.toFixed(2)}% target</small></button>)}
          {invalid.length>0&&<><h3>Invalid geometry</h3>{invalid.map(d=><div className="element-row static" key={`i-${d.element_id}`}><span>Slide {d.page_number}</span><strong>{d.text_preview||'Geometry invalid'}</strong><small>{d.reason_codes.join(' · ')}</small></div>)}</>}
          {unknown.length>0&&<><h3>Not analyzed</h3>{unknown.map(d=><div className="element-row static" key={`u-${d.element_id}`}><span>Slide {d.page_number}</span><strong>{d.text_preview||'Content not analyzed'}</strong><small>{d.reason_codes.join(' · ')}</small></div>)}</>}
          {outside.length>0&&<><h3>Outside reference range</h3>{outside.map(d=><div className="element-row static" key={`o-${d.element_id}`}><span>Slide {d.page_number}</span><strong>{d.text_preview||d.element_id}</strong><small>{d.reason_codes.join(' · ')}</small></div>)}</>}
        </>}
     </>}</aside></div>
     {inspect&&url&&<PdfInspector url={url} detail={inspect} onClose={closeInspector}/>}<p className="disclaimer">FarSeat evaluates structural presentation geometry against PUBLIC_AVIXA_BDM_REFERENCE_V1. It does not determine individual readability, accessibility compliance, or AVIXA certification.</p></main>;
 }
 return null;
}
