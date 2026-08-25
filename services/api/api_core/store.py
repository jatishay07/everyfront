"""Firestore-backed case store, contract §3.1.

This is intentionally the same implementation as
`services/agent-core/agent_core/store.py` (plus `list_all_events`, needed
only here for `GET /events`, and minus `create_filing`/message-idempotency,
needed only there). The two services are built and deployed independently
(`infra/deploy.sh` uses `--source=services/api` / `--source=services/agent-core`
as separate Docker build contexts -- see infra/deploy.sh's `src_for`), so
services/api cannot import agent-core's package at runtime even though both
are owned by SWARM. Both read and write the exact same Firestore collections
(§3.1), so keeping the shared subset byte-for-byte identical matters more
than avoiding the duplication; if you change one, change the other the same
way.

Identifier keys in every returned dict match web/lib/types.ts exactly
(`case_id`, `doc_id`, `event_id`, `filing_id` -- never a bare `id`).

Collections exactly as specified:

    cases/{case_id}
    cases/{case_id}/documents/{doc_id}
    cases/{case_id}/events/{event_id}          -- the audit log / UI activity feed
    hospitals/{ein}
    filings/{filing_id}

Falls back to an in-memory store when `google-cloud-firestore` cannot reach a
real project (no credentials, offline unit tests). Production (Cloud Run,
service account `ef-api`) always gets the real Firestore client.
"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from . import config


def _try_firestore_client():
    try:
        from google.cloud import firestore
    except ImportError:
        return None
    try:
        return firestore.Client(project=config.PROJECT_ID or None)
    except Exception:  # noqa: BLE001 -- any construction failure means "no Firestore"
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CaseStore:
    """Read/write access to case state for the REST API (contract §3.3)."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else _try_firestore_client()
        self._lock = threading.Lock()
        self._cases: dict[str, dict] = {}
        self._documents: dict[str, dict[str, dict]] = {}
        self._events: dict[str, list[dict]] = {}
        self._hospitals: dict[str, dict] = {}
        self._filings: dict[str, dict] = {}

    @property
    def backend(self) -> str:
        return "firestore" if self._client is not None else "memory"

    # ---------------------------------------------------------------- cases
    def create_case(self, case_id: str, data: dict) -> dict:
        payload = {
            "status": "intake",
            "fronts": [],
            "savings_found_cents": 0,
            "audit_findings_cents": 0,
            "denial_flag": None,
            # Non-nullable in web/lib/types.ts's CaseSummary; agent-core's
            # Lookup fills in the real values once the hospital is resolved.
            "hospital_name": "",
            "hospital_nonprofit": True,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            **data,
        }
        if self._client is not None:
            self._client.collection("cases").document(case_id).set(payload)
        else:
            with self._lock:
                self._cases[case_id] = copy.deepcopy(payload)
                self._documents.setdefault(case_id, {})
                self._events.setdefault(case_id, [])
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> dict | None:
        if self._client is not None:
            snap = self._client.collection("cases").document(case_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            data["case_id"] = case_id
            return data
        with self._lock:
            case = self._cases.get(case_id)
            if case is None:
                return None
            out = copy.deepcopy(case)
            out["case_id"] = case_id
            return out

    def list_cases(self) -> list[dict]:
        if self._client is not None:
            out = []
            for snap in self._client.collection("cases").stream():
                d = snap.to_dict()
                d["case_id"] = snap.id
                out.append(d)
            return out
        with self._lock:
            return [dict(v, case_id=k) for k, v in self._cases.items()]

    def update_case(self, case_id: str, patch: dict) -> dict:
        patch = {**patch, "updated_at": _now_iso()}
        if self._client is not None:
            self._client.collection("cases").document(case_id).set(patch, merge=True)
        else:
            with self._lock:
                self._cases.setdefault(case_id, {}).update(patch)
        return self.get_case(case_id)

    def upsert_front(self, case_id: str, front: dict) -> dict:
        case = self.get_case(case_id) or {}
        fronts = list(case.get("fronts") or [])
        for i, existing in enumerate(fronts):
            if existing.get("front") == front.get("front"):
                fronts[i] = front
                break
        else:
            fronts.append(front)
        return self.update_case(case_id, {"fronts": fronts})

    # ----------------------------------------------------------- documents
    def add_document(self, case_id: str, doc: dict, doc_id: str | None = None) -> str:
        doc_id = doc_id or str(uuid.uuid4())
        payload = {"verified": None, "verification_notes": "", "uploaded_at": _now_iso(), **doc}
        if self._client is not None:
            self._client.collection("cases").document(case_id).collection("documents").document(
                doc_id
            ).set(payload)
        else:
            with self._lock:
                self._documents.setdefault(case_id, {})[doc_id] = copy.deepcopy(payload)
        return doc_id

    def get_document(self, case_id: str, doc_id: str) -> dict | None:
        if self._client is not None:
            snap = (
                self._client.collection("cases")
                .document(case_id)
                .collection("documents")
                .document(doc_id)
                .get()
            )
            if not snap.exists:
                return None
            d = snap.to_dict()
            d["doc_id"] = doc_id
            return d
        with self._lock:
            d = self._documents.get(case_id, {}).get(doc_id)
            return dict(d, doc_id=doc_id) if d is not None else None

    def list_documents(self, case_id: str) -> list[dict]:
        if self._client is not None:
            out = []
            for snap in (
                self._client.collection("cases").document(case_id).collection("documents").stream()
            ):
                d = snap.to_dict()
                d["doc_id"] = snap.id
                out.append(d)
            return out
        with self._lock:
            return [dict(v, doc_id=k) for k, v in self._documents.get(case_id, {}).items()]

    # --------------------------------------------------------------- events
    def append_event(
        self,
        case_id: str,
        agent: str,
        action: str,
        detail: str,
        citations: list[str] | None = None,
        event_id: str | None = None,
    ) -> dict:
        event_id = event_id or str(uuid.uuid4())
        payload = {
            "ts": _now_iso(),
            "case_id": case_id,
            "agent": agent,
            "action": action,
            "detail": detail,
            "citations": citations or [],
        }
        if self._client is not None:
            ref = (
                self._client.collection("cases")
                .document(case_id)
                .collection("events")
                .document(event_id)
            )
            if ref.get().exists:
                return {**payload, "event_id": event_id}
            ref.set(payload)
        else:
            with self._lock:
                bucket = self._events.setdefault(case_id, [])
                if any(e.get("event_id") == event_id for e in bucket):
                    return {**payload, "event_id": event_id}
                bucket.append({**payload, "event_id": event_id})
        return {**payload, "event_id": event_id}

    def list_events(self, case_id: str) -> list[dict]:
        if self._client is not None:
            out = []
            for snap in (
                self._client.collection("cases")
                .document(case_id)
                .collection("events")
                .order_by("ts")
                .stream()
            ):
                d = snap.to_dict()
                d["event_id"] = snap.id
                out.append(d)
            return out
        with self._lock:
            return sorted((dict(e) for e in self._events.get(case_id, [])), key=lambda e: e["ts"])

    def list_all_events(self, *, limit: int | None = None, agent: str | None = None) -> list[dict]:
        """Global, cross-case event stream -- contract §3.3 `GET /events`
        (added 2026-08-25, the live activity feed's actual backing endpoint).
        Newest first.
        """
        events: list[dict] = []
        if self._client is not None:
            query = self._client.collection_group("events").order_by("ts", direction="DESCENDING")
            if agent is not None:
                query = query.where("agent", "==", agent)
            if limit is not None:
                query = query.limit(limit)
            for snap in query.stream():
                d = snap.to_dict()
                d["event_id"] = snap.id
                events.append(d)
            return events
        with self._lock:
            for bucket in self._events.values():
                events.extend(dict(e) for e in bucket)
        if agent is not None:
            events = [e for e in events if e.get("agent") == agent]
        events.sort(key=lambda e: e["ts"], reverse=True)
        return events[:limit] if limit is not None else events

    # ------------------------------------------------------------ hospitals
    def get_hospital(self, ein: str) -> dict | None:
        if self._client is not None:
            snap = self._client.collection("hospitals").document(ein).get()
            if not snap.exists:
                return None
            d = snap.to_dict()
            d["ein"] = ein
            return d
        with self._lock:
            d = self._hospitals.get(ein)
            return dict(d, ein=ein) if d is not None else None

    def put_hospital(self, ein: str, data: dict) -> None:
        if self._client is not None:
            self._client.collection("hospitals").document(ein).set(data, merge=True)
        else:
            with self._lock:
                self._hospitals[ein] = copy.deepcopy(data)

    # -------------------------------------------------------------- filings
    def list_filings(self, case_id: str | None = None) -> list[dict]:
        if self._client is not None:
            q = self._client.collection("filings")
            if case_id is not None:
                q = q.where("case_id", "==", case_id)
            out = []
            for snap in q.stream():
                d = snap.to_dict()
                d["filing_id"] = snap.id
                out.append(d)
            return out
        with self._lock:
            vals = [dict(v, filing_id=k) for k, v in self._filings.items()]
            return [v for v in vals if case_id is None or v.get("case_id") == case_id]


# Module-level singleton -- one store per process.
store = CaseStore()
