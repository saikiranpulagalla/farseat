# FarSeat release gate

A release is allowed only when every required suite is `PASS`, the working tree exactly matches the recorded Git commit, and no P0/P1 defect in `validation/defect-ledger.json` is open.

Required executable evidence:

- Python compile check
- complete backend test suite
- production API integration suite using `SubprocessParserRunner`
- parser adversarial/regression suite, including large IPC transport, clipping path terminators, Form XObject inheritance/BBox, transparency, direction/shaping, optional-content cases, and mixed graphics uncertainty
- numerical-boundary suite for BDM reference bands, element equality, and room boundaries
- factorization, capacity, token, and store-lifecycle suite
- frontend unit tests
- frontend TypeScript/Vite production build
- real Chromium browser gate (`scripts/browser_e2e.py`) covering sample determinism, delayed seat-detail integrity, keyboard navigation, inspector focus/resize/overlay, DPR=2 rendering, and mobile hit targets
- committed npm lockfile and `npm ci`
- clean exact source commit (`git status --porcelain` empty)

The defect gate verifies that every closed P0/P1 points to regression evidence that still exists. The validator deliberately returns non-zero for `UNVERIFIED`; missing browser/frontend evidence or missing Git identity is never treated as a pass.
