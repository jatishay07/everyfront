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


def _merge_front(fronts: list[dict], front: dict) -> list[dict]:
    """Insert-or-replace `front` in `fronts`, keyed by its "front" name."""
    for i, existing in enumerate(fronts):
        if existing.get("front") == front.get("front"):
            fronts[i] = dict(front)
            break
    else:
        fronts.append(dict(front))
    return fronts


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

    def update_case(self, case_id: str, patch: dict) -> dict | None:
        """Merge `patch` into an EXISTING case. Returns None, writing nothing,
        if the case is gone.

        `.set(..., merge=True)` happily creates a document that does not
        exist, which meant a late write could resurrect a purged case as a
        half-empty zombie. Seen live 2026-08-26: `fixtures/demo_reset.py`
        renames each reseeded case (copy to `ef-2026-000N`, delete the
        original) as soon as its filings settle, but the Filer's own
        generated PDF lands as a case document and re-triggers analysis --
        whose trailing write recreated `demo-case_08_lawful_denial_ca-65c0a5df`
        13 seconds after the delete, with no `patient`, no `bill` and no
        `created_at`. A stray case in `GET /cases` is a judge-visible defect:
        it breaks PROOF's "corpus is exactly the 8 named cases" guard and
        would appear in the case list on camera.
        """
        patch = {**patch, "updated_at": _now_iso()}
        if self._client is not None:
            ref = self._client.collection("cases").document(case_id)
            if not ref.get().exists:
                return None
            ref.set(patch, merge=True)
        else:
            with self._lock:
                if case_id not in self._cases:
                    return None
                self._cases[case_id].update(patch)
        return self.get_case(case_id)

    def upsert_front(self, case_id: str, front: dict) -> dict | None:
        """Insert or replace one `fronts[]` entry, keyed by `front["front"]`.

        Atomic. Firestore has no array-upsert-by-key primitive, so this is
        still a read-modify-write -- but it runs inside a Firestore
        transaction (in-memory mode holds `self._lock` across the same span),
        so a concurrent writer either serializes behind it or is retried
        against the winner's state.

        The transaction is not belt-and-braces. This method's own docstring
        used to assert that "a single case's fronts are only ever touched by
        this case's own pipeline run, never concurrently from two agents" --
        which stopped being true at ca9fd40, when filing went asynchronous.
        Two fronts approved close together (what every multi-front fixture
        does, and what the recording's approval beat does on camera) run
        their Filers in separate `/pubsub/filing-requested` push handlers
        with no per-case serialization, and the loser's read-modify-write
        clobbered the sibling's already-"filed" status back down. PROOF
        reproduced the loss 3-for-3 live -- ef-2026-0001, -0003 and -0007
        each showed a front as open/filing while its own `filings/` record
        proved it had been sent.

        Prefer `set_front_status` when only the status changes: this method
        writes the caller's whole entry, which is stale by however long the
        caller held it.
        """
        return self._write_fronts(case_id, lambda fronts: _merge_front(fronts, front))

    #: `upsert_front_from_analysis` deliberately does NOT live here.
    #:
    #: REMOVED 2026-08-27 (FORGE). services/api never runs an analysis pass --
    #: `grep -rn upsert_front_from_analysis services/api` found only its own
    #: definition -- so this was dead code sitting in a file whose header
    #: promises it stays byte-for-byte identical to agent-core's. That promise
    #: is exactly what makes a dead copy dangerous: the next person to change
    #: the rule in agent-core reasonably updates this one too, and the two
    #: silently diverge in a method nothing calls until something does.
    #:
    #: `rules_bridge.py` was the same shape (526a8b9): a reimplementation
    #: nothing was supposed to reach, which then answered from a stale copy
    #: after the real bug was fixed upstream. agent-core's
    #: `write_analysis`/`upsert_front_from_analysis` are the only writers of
    #: analysis output, and agent-core is the only service that computes it.

    def set_front_status(self, case_id: str, front: str, status: str) -> dict | None:
        """Atomically set one front's `status`, touching nothing else.

        Reads the entry fresh inside the transaction rather than trusting the
        caller's copy, so a Filer that has been running for a minute cannot
        write back a minute-old view of its own front, nor of its siblings.
        A front that is not present is left alone -- this never invents one.
        """

        def _apply(fronts: list[dict]) -> list[dict]:
            for f in fronts:
                if f.get("front") == front:
                    f["status"] = status
            return fronts

        return self._write_fronts(case_id, _apply)

    def _write_fronts(self, case_id: str, mutate) -> dict | None:
        """Apply `mutate(fronts) -> fronts` to a fresh read of
        `cases/{case_id}.fronts` and persist the result atomically."""
        if self._client is not None:
            from google.cloud import firestore

            ref = self._client.collection("cases").document(case_id)

            @firestore.transactional
            def _txn(transaction) -> None:
                snap = ref.get(transaction=transaction)
                if not snap.exists:  # never resurrect a purged case -- see update_case
                    return
                data = snap.to_dict() or {}
                fronts = mutate(copy.deepcopy(data.get("fronts") or []))
                transaction.set(ref, {"fronts": fronts, "updated_at": _now_iso()}, merge=True)

            _txn(self._client.transaction())
        else:
            with self._lock:
                case = self._cases.get(case_id)
                if case is None:
                    return None
                case["fronts"] = mutate(copy.deepcopy(case.get("fronts") or []))
                case["updated_at"] = _now_iso()
        return self.get_case(case_id)

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
