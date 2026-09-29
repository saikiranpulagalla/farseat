export type TextCoverage = 'COMPLETE' | 'PARTIAL' | 'NONE';
export type ResultState = 'MEETS_TARGET' | 'REVIEW_RECOMMENDED' | 'NO_ANALYZABLE_RESULTS';
export type CoverageState = 'COMPLETE' | 'PARTIAL' | 'NONE';
export type CellState = 'PASS_TARGET' | 'BELOW_TARGET' | 'NOT_ANALYZED' | 'OUTSIDE_REFERENCE_RANGE' | 'INVALID_GEOMETRY';

export interface BBox { x0:number; y0:number; x1:number; y1:number }
export interface PageModel {
  page_number:number;
  geometry:{ page_number:number; page_rotation_deg:0|90|180|270; display_aspect_ratio:number; display_width_units:number; display_height_units:number };
  text_coverage:TextCoverage;
  non_text_content:'ABSENT'|'PRESENT';
  reason_codes:string[];
  elements:Array<{ element_id:string; slide_id:string; text_preview:string|null; logical_bbox:BBox; support_state:string; reason_codes:string[] }>;
}
export interface PresentationResponse {
  schema_version:'1.2'; presentation_id:string; capability_token:string|null; filename:string; page_count:number;
  analyzable_element_count:number; unsupported_element_count:number; unanalyzable_page_count:number; pages:PageModel[];
}
export interface SeatSummary {
  seat_id:string; row:number; number:number; x_m:number; y_m:number; geometry_state:'VALID'|'INVALID';
  result_state:ResultState; coverage_state:CoverageState; has_outside_reference_results:boolean;
  meets_target_count:number; below_target_count:number; outside_range_count:number; not_analyzed_count:number;
}
export interface SlideSummary { slide_id:string; page_number:number; text_coverage:TextCoverage; non_text_content:string; unique_below_target_elements:number; below_target_comparisons:number }
export interface AnalysisResponse {
  schema_version:'1.2'; request_id:string; analysis_id:string; presentation_id:string;
  configured_seat_count:number; valid_seat_count:number; invalid_seat_count:number; affected_valid_seat_count:number;
  seats:SeatSummary[]; slides:SlideSummary[];
}
export interface SeatElementDetail {
  slide_id:string; page_number:number; element_id:string; text_preview:string|null; logical_bbox:BBox|null; state:CellState;
  element_height_pct:number|null; target_height_pct:number|null; margin:number|null; reason_codes:string[];
}
export interface SeatDetailResponse { schema_version:'1.2'; analysis_id:string; seat:{seat_id:string;row:number;number:number;x_m:number;y_m:number}; details:SeatElementDetail[] }
export interface RendererPageManifest { page_number:number; rotation_deg:0|90|180|270; viewport_width:number; viewport_height:number; view_x0:number; view_y0:number; view_x1:number; view_y1:number }
