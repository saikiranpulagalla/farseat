# FarSeat

**FarSeat shows which structurally analyzed presentation text elements fall below a selected visual-demand reference target from different modeled seats.**

Upload a machine-generated PDF, describe the usable display area and seating layout, and FarSeat combines presentation geometry with room geometry to trace a review result back to a modeled seat, slide, and text element.

FarSeat deliberately does **not** claim individual readability, WCAG compliance, AVIXA certification, medical/vision assessment, or complete analysis of arbitrary PDF content.

## Why this exists

Presentation editors understand the slide. AV tools understand the room. FarSeat connects the two. A slide that appears comfortable on a laptop can impose much greater visual demand when the active image is physically small or the viewer is farther away.

## What V1 does

- accepts PDF uploads up to 20 MiB and 75 pages; PDFs that exceed structural or safe-processing limits may be rejected;
- extracts supported horizontal visible text without OCR;
- models the **active displayed image**, including per-slide aspect-ratio letterboxing;
- models up to 200 seats using perpendicular distance to the display plane;
- compares measured structural text height against `PUBLIC_AVIXA_BDM_REFERENCE_V1`;
- reports `PASS_TARGET`, `BELOW_TARGET`, `NOT_ANALYZED`, `OUTSIDE_REFERENCE_RANGE`, or `INVALID_GEOMETRY` at the comparison level;
- keeps **result**, **coverage**, and **outside-reference** conditions separate in aggregate UI state;
- renders the original PDF locally with PDF.js and overlays the selected normalized text region;
- uses short-lived capability tokens and in-memory server state; no accounts or database.

## EC-1.2 safety rules

The central rule is: **unknown never becomes pass**.

FarSeat separates five questions: page geometry trust, content analyzability, measurement confidence, seat/reference validity, and coverage. Unsupported or uncertain content never receives a fabricated element-height measurement. Geometry disagreements between backend parsing and PDF.js rendering become `NOT_ANALYZED`, not a plausible green result.

The PDF parser captures PDF text rendering mode, alpha/soft-mask state, and proven rectangular clipping state so invisible or unsupported transparent/clipped text cannot masquerade as visible content. Complex clipping paths, optional-content/layered PDFs, RTL text, and unvalidated non-Latin shaping fail closed to `NOT_ANALYZED` in V1. Normal page rotations are normalized separately from arbitrary text rotation. Non-text graphics are tracked independently so a decorative logo does not poison otherwise complete text coverage.

## Public BDM reference profile

`PUBLIC_AVIXA_BDM_REFERENCE_V1` encodes the public Basic Decision Making viewing-ratio / element-height table. FarSeat applies a conservative higher-target policy at shared interval boundaries. The profile is a **reference profile only**; FarSeat does not claim implementation or certification of the current ANSI/AVIXA standard.

The exact encoded table is in `validation/reference/PUBLIC_AVIXA_BDM_REFERENCE_V1.json` and `backend/app/reference.py`.

## Architecture

```text
Browser
  local File/Blob ────────> PDF.js renderer + geometry manifest
       │
       └── PDF upload ────> FastAPI
                              │
                              ├─ bounded upload read
                              ├─ isolated parser subprocess
                              ├─ canonical page/text model
                              └─ temporary capability-protected store

Room + display setup ─────> active-image geometry per slide
Modeled seats ────────────> perpendicular viewing ratio
Reference profile ────────> target element height
Text measurements ────────> element × seat comparison
                              │
                              ├─ compact seat/slide summaries
                              └─ on-demand seat detail
```

The matrix is factorized; FarSeat does not persist millions of element×seat objects.

## Supported and deliberately not analyzed

V1 is designed for horizontally rendered extractable text whose geometry fits the validated left-to-right pipeline, plus standard PDF page rotations of 0/90/180/270 degrees. Image-only/scanned slides, OCR-dependent text, handwriting, arbitrary rotated text, unsupported writing directions/shaping, ambient lighting, projector/image contrast, and individual vision are outside V1's analysis scope and are surfaced as not analyzed/coverage limitations rather than silently passed.

## Run locally

Backend:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload --workers 1 --port 8000
```

Frontend in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. The frontend uses same-origin `/api` by default; the Vite development server proxies `/api` and `/health` to `http://localhost:8000`. Set `VITE_API_BASE` only when intentionally using a separate API origin.

## Container deployment

The repository includes a single-worker FastAPI container plus an nginx/Vite frontend deployment path:

```bash
docker compose up --build
```

Then open `http://localhost:8080`. nginx serves the built frontend and proxies same-origin `/api` and `/health` requests to the single backend worker. `FARSEAT_ALLOWED_ORIGINS` and `FARSEAT_MAX_CONCURRENT_UPLOADS` are configurable through environment variables; `.env.example` documents the defaults.

## Tests and validation

```bash
PYTHONPATH=backend pytest -q backend/tests
cd frontend && npm test && npm run build
cd .. && python scripts/validate_release.py
```

The release validator treats absent required suites/files as failure and missing local frontend dependencies as `UNVERIFIED`, never PASS. Frontend qualification has now been executed on a network-enabled GitHub Actions runner: `npm ci`, all 6 frontend tests, TypeScript compilation, Vite production build, and production-output verification passed. The normal full-repository release workflow remains the final provenance gate once the complete source tree is published to GitHub.

## Demo fixture

`frontend/public/farseat-demo.pdf` is a six-slide, project-owned fixture covering ordinary text, deliberately small text, mixed text sizes, an ultra-wide slide, and an image-only slide. Structural expectations are hand-authored in `sample/farseat-demo.expected.json`; expected results are not generated from production code.

## Privacy and security

FarSeat processes uploaded PDFs to create a temporary analysis model. The original uploaded file is not intentionally retained after parsing, although the web framework may spool upload bytes to temporary storage while receiving them. Presentation and analysis objects are held in process memory with TTL expiry. A random capability token is returned once to the browser and is required for later access; tokens are stored only as SHA-256 digests and are never placed in URLs.

For a public deployment, place an HTTP request-body limit in front of FastAPI as defense in depth. The included nginx configuration uses a 21 MB body limit, and the application additionally caps concurrent upload ingestion and performs a single bounded read of at most 20 MiB + 1 byte before parsing. Parser work runs in a spawned subprocess with a wall deadline and memory ceiling so pathological inputs cannot consume the API process indefinitely. Parent/child transport uses a concurrently drained one-way pipe so large valid parsed models cannot deadlock behind an IPC buffer. The upload ceiling and safe-processing protections are independent: a PDF within 20 MiB can still be rejected when it exceeds safe structural or processing limits.

## Tech stack

- React + TypeScript + Vite + Tailwind CSS
- PDF.js for local rendering / renderer geometry
- FastAPI + Pydantic
- pdfplumber/pdfminer.six for structural PDF parsing
- pytest; GitHub Actions release gate

## Project status

The artifact contains the hardened v1.0.3 release candidate and automated regression tests for the audit P0s. Backend validation is 83/83 green, and the exact v1.0.3 frontend source passes 6/6 frontend tests plus the TypeScript/Vite production build. The v1.0.3 browser gate is implemented in `scripts/browser_e2e.py` and runs in CI with Playwright-managed Chromium. In the local qualification container, the administrator-managed system Chromium blocks navigation, so the same gate was executed with the explicit `FARSEAT_CHROMIUM_EXECUTABLE` override against an exported Playwright headless-shell bundle and passed all browser checks. The browser evidence records the exact Git source commit and release validation rejects stale evidence from any other commit. A repository checkout should only be called **release-verified** after the normal full-source GitHub Actions workflow passes for that exact published commit. Generated CI results belong to workflow/release artifacts rather than being committed back into the source tree.

## License

MIT.
