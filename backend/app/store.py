from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import threading
import time
from uuid import UUID

from .analysis import AnalysisSnapshot
from .models import PresentationModel

TTL_SECONDS = 30 * 60
TOMBSTONE_SECONDS = 5 * 60
MAX_PRESENTATIONS = 8
MAX_ANALYSES = 24
MAX_PRESENTATION_WEIGHT = 180_000


@dataclass(frozen=True)
class StoredPresentation:
    model: PresentationModel
    token_digest: bytes
    expires_at: float
    weight: int


@dataclass(frozen=True)
class StoredAnalysis:
    snapshot: AnalysisSnapshot
    token_digest: bytes
    expires_at: float


@dataclass(frozen=True)
class Tombstone:
    token_digest: bytes
    expires_at: float


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _presentation_weight(model: PresentationModel) -> int:
    # A deterministic normalized-model budget. It intentionally does not pretend to
    # equal Python RSS; its job is to bound retained parsed-model complexity.
    elements = sum(len(page.elements) for page in model.pages)
    preview_chars = sum(len(el.text_preview or "") for page in model.pages for el in page.elements)
    return model.page_count * 100 + elements * 6 + preview_chars // 32


class EphemeralStore:
    def __init__(self):
        self._presentations: dict[UUID, StoredPresentation] = {}
        self._analyses: dict[UUID, StoredAnalysis] = {}
        self._expired_presentations: dict[UUID, Tombstone] = {}
        self._expired_analyses: dict[UUID, Tombstone] = {}
        self._lock = threading.RLock()

    def _purge(self):
        now = time.monotonic()
        for pid, item in list(self._presentations.items()):
            if item.expires_at <= now:
                self._expired_presentations[pid] = Tombstone(item.token_digest, now + TOMBSTONE_SECONDS)
                self._presentations.pop(pid, None)
                for aid, analysis in list(self._analyses.items()):
                    if analysis.snapshot.presentation_id == pid:
                        self._expired_analyses[aid] = Tombstone(analysis.token_digest, now + TOMBSTONE_SECONDS)
                        self._analyses.pop(aid, None)
        for aid, item in list(self._analyses.items()):
            if item.expires_at <= now:
                self._expired_analyses[aid] = Tombstone(item.token_digest, now + TOMBSTONE_SECONDS)
                self._analyses.pop(aid, None)
        self._expired_presentations = {
            k: v for k, v in self._expired_presentations.items() if v.expires_at > now
        }
        self._expired_analyses = {k: v for k, v in self._expired_analyses.items() if v.expires_at > now}

    def _authorized(self, expected: bytes, token: str | None) -> bool:
        if not token:
            return False
        return secrets.compare_digest(expected, _digest(token))

    def _total_weight(self) -> int:
        return sum(item.weight for item in self._presentations.values())

    def put_presentation(self, model: PresentationModel) -> str:
        token = secrets.token_urlsafe(32)
        weight = _presentation_weight(model)
        if weight > MAX_PRESENTATION_WEIGHT:
            raise ValueError("STORE_CAPACITY_REACHED")
        with self._lock:
            self._purge()
            # Never evict another live capability to make room for a new upload. At
            # capacity FarSeat rejects the new operation, preserving existing sessions.
            if len(self._presentations) >= MAX_PRESENTATIONS or self._total_weight() + weight > MAX_PRESENTATION_WEIGHT:
                raise ValueError("STORE_CAPACITY_REACHED")
            self._presentations[model.presentation_id] = StoredPresentation(
                model=model,
                token_digest=_digest(token),
                expires_at=time.monotonic() + TTL_SECONDS,
                weight=weight,
            )
        return token

    def lookup_presentation(self, pid: UUID, token: str | None) -> tuple[str, PresentationModel | None]:
        with self._lock:
            self._purge()
            item = self._presentations.get(pid)
            if item is not None:
                if not self._authorized(item.token_digest, token):
                    return "NOT_FOUND", None
                return "OK", item.model
            tombstone = self._expired_presentations.get(pid)
            if tombstone is not None and self._authorized(tombstone.token_digest, token):
                return "EXPIRED", None
            return "NOT_FOUND", None

    def get_presentation(self, pid: UUID, token: str | None) -> PresentationModel | None:
        status, model = self.lookup_presentation(pid, token)
        return model if status == "OK" else None

    def put_analysis(self, snapshot: AnalysisSnapshot, token: str) -> str:
        """Atomically commit an analysis only while its parent capability is live.

        This closes the delete/eviction TOCTOU window: an analysis computed from a
        previously valid presentation cannot be resurrected after the presentation
        has been deleted, expired, or evicted while computation was in flight.
        """
        with self._lock:
            self._purge()
            parent = self._presentations.get(snapshot.presentation_id)
            if parent is None:
                tombstone = self._expired_presentations.get(snapshot.presentation_id)
                if tombstone is not None and self._authorized(tombstone.token_digest, token):
                    return "EXPIRED"
                return "NOT_FOUND"
            if not self._authorized(parent.token_digest, token):
                return "NOT_FOUND"
            if len(self._analyses) >= MAX_ANALYSES:
                return "CAPACITY"
            self._analyses[snapshot.analysis_id] = StoredAnalysis(
                snapshot=snapshot,
                token_digest=parent.token_digest,
                expires_at=time.monotonic() + TTL_SECONDS,
            )
            return "OK"

    def lookup_analysis(self, aid: UUID, token: str | None) -> tuple[str, StoredAnalysis | None]:
        with self._lock:
            self._purge()
            item = self._analyses.get(aid)
            if item is not None:
                if not self._authorized(item.token_digest, token):
                    return "NOT_FOUND", None
                return "OK", item
            tombstone = self._expired_analyses.get(aid)
            if tombstone is not None and self._authorized(tombstone.token_digest, token):
                return "EXPIRED", None
            return "NOT_FOUND", None

    def get_analysis(self, aid: UUID, token: str | None) -> StoredAnalysis | None:
        status, item = self.lookup_analysis(aid, token)
        return item if status == "OK" else None

    def delete_presentation(self, pid: UUID):
        with self._lock:
            self._presentations.pop(pid, None)
            self._expired_presentations.pop(pid, None)
            for aid in [a for a, item in self._analyses.items() if item.snapshot.presentation_id == pid]:
                self._analyses.pop(aid, None)
                self._expired_analyses.pop(aid, None)
