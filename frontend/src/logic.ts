import type { SeatSummary } from './types';

export type SeatVisualKind='invalid'|'review'|'unknown'|'outside'|'pass';
export function seatVisualKind(s:SeatSummary):SeatVisualKind{
  if(s.geometry_state==='INVALID') return 'invalid';
  if(s.result_state==='REVIEW_RECOMMENDED') return 'review';
  if(s.coverage_state!=='COMPLETE') return 'unknown';
  if(s.has_outside_reference_results && s.result_state==='NO_ANALYZABLE_RESULTS') return 'outside';
  return 'pass';
}

export function seatVisualGlyph(s:SeatSummary):string{
  if(s.geometry_state==='INVALID') return '×';
  const parts:string[]=[];
  if(s.result_state==='REVIEW_RECOMMENDED') parts.push('!');
  else if(s.result_state==='MEETS_TARGET') parts.push('✓');
  if(s.coverage_state!=='COMPLETE') parts.push('?');
  if(s.has_outside_reference_results) parts.push('↗');
  if(parts.length===0) parts.push('—');
  return parts.join('');
}

export function seatAriaLabel(s:SeatSummary):string{
  const parts=[`Row ${s.row}, seat ${s.number}`];
  if(s.geometry_state==='INVALID') return `${parts[0]}, invalid geometry`;
  parts.push(`${s.below_target_count} below target`,`${s.meets_target_count} meet target`);
  if(s.not_analyzed_count) parts.push(`${s.not_analyzed_count} not analyzed`);
  if(s.outside_range_count) parts.push(`${s.outside_range_count} outside reference range`);
  return parts.join(', ');
}

export function resultsHeadline(seats:SeatSummary[],affectedValid:number,validCount:number):string{
  const valid=seats.filter(s=>s.geometry_state==='VALID');
  if(validCount===0) return 'No valid modeled seats are available for analysis.';
  if(valid.length>0 && valid.every(s=>s.coverage_state==='NONE')) return `No seat-level target conclusion is available because analysis coverage is unavailable for all ${validCount} valid modeled seats.`;
  if(affectedValid===0) return `No analyzed text elements fell below the selected target for ${validCount} valid modeled seats.`;
  return `${affectedValid} of ${validCount} valid modeled seats have at least one analyzed element below the selected target.`;
}
