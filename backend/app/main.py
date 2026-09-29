from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .analysis import analyze_presentation, derive_seat_detail
from .models import AnalyzeRequest, AnalysisResponse, PresentationResponse, SeatDetailResponse
from .parser import ParseFailure, SubprocessParserRunner
from .store import EphemeralStore

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
READ_CHUNK = 1024 * 1024

def _allowed_origins() -> list[str]:
    raw = os.getenv("FARSEAT_ALLOWED_ORIGINS", "http://localhost:5173")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["http://localhost:5173"]


class UploadAdmissionMiddleware:
    """Bound concurrent upload requests before multipart parsing/spooling begins."""

    def __init__(self, app, max_concurrent: int):
        self.app = app
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "POST" and scope.get("path") == "/api/presentations":
            async with self._semaphore:
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = FastAPI(title="FarSeat API", version="1.0.3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-FarSeat-Token"],
)
app.add_middleware(
    UploadAdmissionMiddleware,
    max_concurrent=max(1, int(os.getenv("FARSEAT_MAX_CONCURRENT_UPLOADS", "2"))),
)

@app.exception_handler(RequestValidationError)
async def request_validation_error(_request: Request, _exc: RequestValidationError):
    # Keep the API error contract stable; do not leak Pydantic's structurally different
    # list payload into a client that expects detail.code.
    return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_REQUEST"}})

store = EphemeralStore()
parser_runner = SubprocessParserRunner()
parse_semaphore = asyncio.Semaphore(1)
analysis_semaphore = asyncio.Semaphore(1)


@app.get("/health")
def health():
    return {"status": "ok", "schema_version": "1.2"}


async def _bounded_read(upload: UploadFile) -> bytes:
    try:
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await upload.close()
    if not data:
        raise HTTPException(status_code=400, detail={"code": "EMPTY_FILE"})
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "FILE_LIMIT_EXCEEDED"})
    return data


def _session_error(status: str) -> HTTPException:
    if status == "EXPIRED":
        return HTTPException(status_code=410, detail={"code": "SESSION_EXPIRED"})
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND"})


@app.post("/api/presentations", response_model=PresentationResponse)
async def upload_presentation(file: UploadFile = File(...)):
    filename = (file.filename or "presentation.pdf")[:200]
    if file.content_type not in ("application/pdf", "application/octet-stream", None):
        await file.close()
        raise HTTPException(status_code=415, detail={"code": "INVALID_PDF"})
    pid = uuid4()
    try:
        data = await _bounded_read(file)
        async with parse_semaphore:
            model = await asyncio.to_thread(parser_runner.parse, data, pid, filename)
    except ParseFailure as exc:
        status = 503 if exc.code in {"PARSER_RESOURCE_LIMIT"} else 422
        raise HTTPException(status_code=status, detail={"code": exc.code}) from exc
    try:
        token = store.put_presentation(model)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={"code": "STORE_CAPACITY_REACHED"}) from exc
    return PresentationResponse(
        presentation_id=model.presentation_id,
        capability_token=token,
        filename=model.filename,
        page_count=model.page_count,
        analyzable_element_count=model.analyzable_element_count,
        unsupported_element_count=model.unsupported_element_count,
        unanalyzable_page_count=model.unanalyzable_page_count,
        pages=model.pages,
    )


@app.get("/api/presentations/{presentation_id}", response_model=PresentationResponse)
def get_presentation(presentation_id: UUID, x_farseat_token: str | None = Header(default=None)):
    status, model = store.lookup_presentation(presentation_id, x_farseat_token)
    if model is None:
        raise _session_error(status)
    return PresentationResponse(
        presentation_id=model.presentation_id,
        capability_token=None,
        filename=model.filename,
        page_count=model.page_count,
        analyzable_element_count=model.analyzable_element_count,
        unsupported_element_count=model.unsupported_element_count,
        unanalyzable_page_count=model.unanalyzable_page_count,
        pages=model.pages,
    )


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(req: AnalyzeRequest, x_farseat_token: str | None = Header(default=None)):
    status, model = store.lookup_presentation(req.presentation_id, x_farseat_token)
    if model is None:
        raise _session_error(status)
    try:
        async with analysis_semaphore:
            snapshot = await asyncio.to_thread(analyze_presentation, model, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_GEOMETRY"}) from exc
    commit_status = store.put_analysis(snapshot, x_farseat_token or "")
    if commit_status == "CAPACITY":
        raise HTTPException(status_code=503, detail={"code": "ANALYSIS_CAPACITY_REACHED"})
    if commit_status != "OK":
        raise _session_error(commit_status)
    return snapshot.response


@app.get("/api/analyses/{analysis_id}/seats/{seat_id}", response_model=SeatDetailResponse)
def seat_detail(analysis_id: UUID, seat_id: str, x_farseat_token: str | None = Header(default=None)):
    status, stored = store.lookup_analysis(analysis_id, x_farseat_token)
    if stored is None:
        raise _session_error(status)
    p_status, model = store.lookup_presentation(stored.snapshot.presentation_id, x_farseat_token)
    if model is None:
        raise _session_error(p_status)
    detail = derive_seat_detail(model, stored.snapshot, seat_id)
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    return detail


@app.delete("/api/presentations/{presentation_id}", status_code=204)
def delete_presentation(presentation_id: UUID, x_farseat_token: str | None = Header(default=None)):
    status, model = store.lookup_presentation(presentation_id, x_farseat_token)
    if model is None:
        raise _session_error(status)
    store.delete_presentation(presentation_id)
    return None
