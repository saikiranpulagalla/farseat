import { describe, expect, it } from 'vitest';
import { LatestRequestGate } from './requestCoordinator';

describe('LatestRequestGate',()=>{
  it('invalidates and aborts an older seat-detail request',()=>{
    const gate=new LatestRequestGate();
    const a=gate.begin();
    expect(a.signal.aborted).toBe(false);
    const b=gate.begin();
    expect(a.signal.aborted).toBe(true);
    expect(gate.isCurrent(a.sequence)).toBe(false);
    expect(gate.isCurrent(b.sequence)).toBe(true);
  });
  it('cancel invalidates the active request',()=>{
    const gate=new LatestRequestGate();const r=gate.begin();gate.cancel();
    expect(r.signal.aborted).toBe(true);expect(gate.isCurrent(r.sequence)).toBe(false);
  });
});
