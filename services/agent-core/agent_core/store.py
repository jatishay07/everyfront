"""Firestore-backed case store, contract §3.1.

Collections exactly as specified:

    cases/{case_id}
    cases/{case_id}/documents/{doc_id}
    cases/{case_id}/events/{event_id}          -- the audit log / UI activity feed
    hospitals/{ein}
    filings/{filing_id}
    _processed_messages/{message_id}           -- NOT a contract collection; this
        service's own bookkeeping for §2.3 idempotent Pub/Sub redelivery.

Identifier keys in every returned dict match web/lib/types.ts exactly
(`case_id`, `doc_id`, `event_id`, `filing_id` -- never a bare `id`), since
CANVAS's frontend types are the other half of this contract and a mismatched
key name is a silent `undefined` in the UI, not a loud error.

Falls back to an in-memory store when `google-cloud-firestore` cannot reach a
real project (no credentials, offline unit tests, `FIRESTORE_EMULATOR_HOST`
unset and no ADC). This is a deliberate seam, not a toy: PROOF's `make
demo-reset` and this repo's own CI both need to exercise the pipeline without
a live GCP project, and a store an agent cannot construct is a store nobody
can test against. Production (Cloud Run, service account `ef-agent`) always
gets the real Firestore client -- see `_try_firestore_client`.
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
        client = firestore.Client(project=config.PROJECT_ID or None)
        # Cheap round-trip-free sanity check; firestore.Client is lazy so this
        # does not by itself prove connectivity, but it does prove the SDK and
        # ADC are present enough to construct a client.
        return client
    except Exception:  # noqa: BLE001 -- any construction failure means "no Firestore"
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CaseStore:
    """Everything the agent hierarchy needs to read or write case state.

    Every method is safe to call from either the Firestore-backed or
    in-memory mode with identical semantics -- callers never branch on which
    backend is live.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client if client is not None else _try_firestore_client()
        self._lock = threading.Lock()
        # In-memory fallback state, only touched when self._client is None.
        self._cases: dict[str, dict] = {}
        self._documents: dict[str, dict[str, dict]] = {}
        self._events: dict[str, list[dict]] = {}
        self._hospitals: dict[str, dict] = {}
        self._filings: dict[str, dict] = {}
        self._processed: set[str] = set()

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
            # Non-nullable in web/lib/types.ts's CaseSummary; Lookup fills in
            # the real values once the hospital is resolved (agent_core.pipeline).
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
        """Insert or replace one `fronts[]` entry, keyed by `front["front"]`.

        Firestore has no atomic array-upsert-by-key, so this is read-modify-
        write. Acceptable here: a single case's fronts are only ever touched by
        this case's own pipeline run, never concurrently from two agents.
        """
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

    def update_document(self, case_id: str, doc_id: str, patch: dict) -> None:
        if self._client is not None:
            self._client.collection("cases").document(case_id).collection("documents").document(
                doc_id
            ).set(patch, merge=True)
        else:
            with self._lock:
                self._documents.setdefault(case_id, {}).setdefault(doc_id, {}).update(patch)

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
        """Append one entry to `cases/{id}/events`. This is the audit log.

        Every agent action must call this -- it is the UI activity feed and,
        per persona 5's own identity, "an unobservable agent is a broken
        agent." `event_id` is accepted (not generated) so pipeline callers can
        pass a deterministic id derived from the triggering Pub/Sub message
        for redelivery-safe dedupe at the individual-event level, in addition
        to the coarser `has_processed_message` guard below.

        Stores `case_id` on the event itself (not just implied by the
        subcollection path) so the global `GET /events` stream (services/api)
        can flatten across cases and still let the UI link back to one.
        """
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

        Firestore: a `collection_group` query over every `events` subcollection.
        In-memory: flatten `self._events` across all cases. Both return newest
        first, matching the "live activity feed" reading order.
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
    def create_filing(self, filing: dict, filing_id: str | None = None) -> str:
        filing_id = filing_id or str(uuid.uuid4())
        payload = {"sent_at": _now_iso(), **filing}
        if self._client is not None:
            self._client.collection("filings").document(filing_id).set(payload)
        else:
            with self._lock:
                self._filings[filing_id] = copy.deepcopy(payload)
        return filing_id

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

    # ------------------------------------------------- message idempotency
    def has_processed_message(self, message_id: str) -> bool:
        """§2.3: every Pub/Sub handler must tolerate redelivery.

        Returns True (and does NOT record it) if already seen, so the caller
        can short-circuit before doing any work; the caller must then call
        `mark_message_processed` only once handling actually completes, so a
        crash mid-handler is retried rather than silently dropped.
        """
        if self._client is not None:
            return self._client.collection("_processed_messages").document(message_id).get().exists
        with self._lock:
            return message_id in self._processed

    def mark_message_processed(self, message_id: str) -> None:
        if self._client is not None:
            self._client.collection("_processed_messages").document(message_id).set(
                {"processed_at": _now_iso()}
            )
        else:
            with self._lock:
                self._processed.add(message_id)


# Module-level singleton -- one store per process, matching how a Cloud Run
# instance holds one Firestore client for its lifetime.
store = CaseStore()
