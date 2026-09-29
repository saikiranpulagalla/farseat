# Verification Status — FarSeat v1.0.3 RC

Contract: EC-1.2

## Verified on the exact v1.0.3 source

- Backend complete suite: **83/83 PASS**.
- Production API/subprocess parser path: PASS.
- Numeric boundary hardening: PASS, including decimal-derived BDM boundaries, ULP-bounded equality, and exact room-boundary seat generation.
- Parser visibility hardening: PASS, including CropBox clipping, graphics clipping, obsolete `F` path termination, alpha/transparency, optional content, direction/shaping, Form XObject parent-state inheritance, and Form `/BBox` clipping.
- Mixed text + graphics preserves explicit unknown coverage through seat summary and detail: PASS.
- Factorized analysis/store behavior: PASS.
- Token, expiry, delete/analyze atomicity, and capacity non-eviction regressions: PASS.
- Upload admission is installed as ASGI middleware before route-level multipart handling; nginx request size, connection, and rate limits are included.
- Frontend exact pinned dependency set: `npm ci` succeeded on a network-enabled GitHub runner.
- Frontend unit tests on the edited v1.0.3 source: **6/6 PASS**.
- TypeScript compilation and Vite production build on the edited v1.0.3 source: PASS.
- Sample classroom reset/configuration, stale-generation guards, orphan-upload cleanup, detail loading semantics, inspector sizing/focus/error handling, PDF.js DPR render transform, semantic unit selection, and mobile 44 px seat targets are implemented.
- Defect-ledger validation now rejects a CLOSED P0/P1 whose referenced regression evidence no longer exists.

## Browser release gate

`scripts/browser_e2e.py` is an executable Playwright Chromium gate covering the frozen sample flow, 30-seat result map, keyboard navigation, delayed detail integrity, inspector focus/overlay/resize, DPR=2 backing-store scaling, sample reset determinism, and mobile hit targets. `.github/workflows/ci.yml` installs Playwright Chromium and requires this gate before packaging.

The browser gate is now **PASS in this container** using the exported Playwright unmanaged browser bundle. The administrator-managed system Chromium still rejects navigation with `ERR_BLOCKED_BY_ADMINISTRATOR`, so the gate was executed with `FARSEAT_CHROMIUM_EXECUTABLE` pointing at Playwright's standalone headless shell. Desktop lifecycle/overlay, HiDPI rendering, and mobile hit-target checks all passed. Browser evidence is now source-commit-bound, so a stale PASS report from another commit is rejected. CI remains the canonical release environment and uses Playwright-managed Chromium by default.

## Release status

- Known reproduced P0/P1 implementation defects from the latest audit: **closed in source with regression evidence**.
- Local release status: **all executable gates PASS; publication/source-identity provenance remains the final release gate**.
- Final V1 promotion requires the normal GitHub `FarSeat CI` workflow to run the exact published source commit and return PASS for backend, frontend, browser E2E, release validator, and source identity.
