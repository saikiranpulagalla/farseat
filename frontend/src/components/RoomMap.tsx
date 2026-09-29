import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { SeatSummary } from '../types';
import { seatAriaLabel, seatVisualGlyph, seatVisualKind } from '../logic';

export function RoomMap({seats,roomWidth,roomDepth,displayWidth,displayHeight,selected,onSelect}:{seats:SeatSummary[];roomWidth:number;roomDepth:number;displayWidth:number;displayHeight:number;selected?:string;onSelect:(id:string)=>void}){
  const left=(displayWidth/roomWidth)*100;
  const [focused,setFocused]=useState(selected || seats[0]?.seat_id || '');
  const refs=useRef(new Map<string,HTMLButtonElement>());
  useEffect(()=>{ if(selected) setFocused(selected); else if(!focused && seats[0]) setFocused(seats[0].seat_id); },[selected,seats,focused]);

  const move=(seat:SeatSummary,key:string)=>{
    let candidates:SeatSummary[]=[];
    if(key==='ArrowLeft'||key==='ArrowRight') candidates=seats.filter(s=>s.row===seat.row && (key==='ArrowLeft'?s.number<seat.number:s.number>seat.number));
    else candidates=seats.filter(s=>s.number===seat.number && (key==='ArrowUp'?s.row<seat.row:s.row>seat.row));
    if(!candidates.length) return;
    candidates.sort((a,b)=>{
      if(key==='ArrowLeft') return b.number-a.number;
      if(key==='ArrowRight') return a.number-b.number;
      if(key==='ArrowUp') return b.row-a.row;
      return a.row-b.row;
    });
    const next=candidates[0];setFocused(next.seat_id);refs.current.get(next.seat_id)?.focus();
  };
  const keyDown=(e:KeyboardEvent<HTMLButtonElement>,seat:SeatSummary)=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
    e.preventDefault();move(seat,e.key);
  };

  return <div className="room-wrap">
    <div className="display-label">Usable display {displayWidth.toFixed(2)} m × {displayHeight.toFixed(2)} m</div>
    <div className="display-bar" style={{width:`${Math.min(90,Math.max(12,left))}%`}} aria-hidden="true">DISPLAY</div>
    <div className="room-map" role="group" aria-label="Modeled room seats">
      {seats.map(s=>{
        const x=Math.max(2,Math.min(98,50+(s.x_m/roomWidth)*100));
        const y=Math.max(3,Math.min(97,(s.y_m/roomDepth)*100));
        return <button key={s.seat_id} ref={node=>{if(node)refs.current.set(s.seat_id,node);else refs.current.delete(s.seat_id);}}
          className={`seat ${seatVisualKind(s)} ${selected===s.seat_id?'selected':''}`}
          style={{left:`${x}%`,top:`${y}%`}} aria-label={seatAriaLabel(s)} aria-pressed={selected===s.seat_id}
          tabIndex={focused===s.seat_id?0:-1} onFocus={()=>setFocused(s.seat_id)} onKeyDown={e=>keyDown(e,s)}
          onClick={()=>onSelect(s.seat_id)}><span>{s.row}.{s.number}</span><small className="seat-glyph" aria-hidden="true">{seatVisualGlyph(s)}</small></button>;
      })}
    </div>
    <details className="seat-list"><summary>Seat list</summary><div>{seats.map(s=><button className="ghost" key={s.seat_id} onClick={()=>onSelect(s.seat_id)}>{seatVisualGlyph(s)} Row {s.row} · Seat {s.number}</button>)}</div></details>
  </div>;
}
