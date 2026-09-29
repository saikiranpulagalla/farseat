import { describe, expect, it } from 'vitest';
import { resultsHeadline, seatAriaLabel, seatVisualGlyph, seatVisualKind } from './logic';
import type { SeatSummary } from './types';

const base: SeatSummary = {
  seat_id: 'r1-s1',
  row: 1,
  number: 1,
  x_m: 0,
  y_m: 2,
  geometry_state: 'VALID',
  result_state: 'MEETS_TARGET',
  coverage_state: 'COMPLETE',
  has_outside_reference_results: false,
  meets_target_count: 4,
  below_target_count: 0,
  outside_range_count: 0,
  not_analyzed_count: 0,
};

describe('seat presentation', () => {
  it('does not hide unknown behind pass', () => {
    const s = { ...base, coverage_state: 'PARTIAL' as const, not_analyzed_count: 1 };
    expect(seatVisualKind(s)).toBe('unknown');
    expect(seatVisualGlyph(s)).toContain('?');
  });

  it('does not use a reassuring zero-below headline when all valid seats have no coverage', () => {
    const s = {
      ...base,
      coverage_state: 'NONE' as const,
      result_state: 'NO_ANALYZABLE_RESULTS' as const,
      meets_target_count: 0,
    };
    expect(resultsHeadline([s], 0, 1)).toContain('No seat-level target conclusion');
  });

  it('preserves outside as a supplementary glyph even when pass', () => {
    const s = { ...base, has_outside_reference_results: true, outside_range_count: 2 };
    expect(seatVisualKind(s)).toBe('pass');
    expect(seatVisualGlyph(s)).toContain('↗');
    expect(seatAriaLabel(s)).toContain('2 outside reference range');
  });

  it('shows review and unknown simultaneously', () => {
    const s = {
      ...base,
      result_state: 'REVIEW_RECOMMENDED' as const,
      coverage_state: 'PARTIAL' as const,
      below_target_count: 1,
      not_analyzed_count: 1,
    };
    expect(seatVisualGlyph(s)).toBe('!?');
  });
});
