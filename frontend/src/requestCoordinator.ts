export class LatestRequestGate {
  private sequence = 0;
  private controller: AbortController | null = null;

  begin(): { sequence: number; signal: AbortSignal } {
    this.sequence += 1;
    this.controller?.abort();
    this.controller = new AbortController();
    return { sequence: this.sequence, signal: this.controller.signal };
  }

  isCurrent(sequence: number): boolean {
    return sequence === this.sequence;
  }

  cancel(): void {
    this.sequence += 1;
    this.controller?.abort();
    this.controller = null;
  }
}
