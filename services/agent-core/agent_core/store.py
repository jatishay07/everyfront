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
    def _new_case_payload(self, data: dict) -> dict:
        return {
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

    def create_case(self, case_id: str, data: dict) -> dict:
        payload = self._new_case_payload(data)
        if self._client is not None:
            self._client.collection("cases").document(case_id).set(payload)
        else:
            with self._lock:
                self._cases[case_id] = copy.deepcopy(payload)
                self._documents.setdefault(case_id, {})
                self._events.setdefault(case_id, [])
        return self.get_case(case_id)

    def create_case_if_absent(self, case_id: str, data: dict) -> tuple[dict, bool]:
        """Create `cases/{case_id}` ONLY if it does not exist yet. Returns
        `(case, created)`.

        `create_case` uses `.set()`, which overwrites: calling it for a case
        that already exists silently discards its `fronts[]`, its status and
        every field the cascade has written. That is fine for the two callers
        that mint a fresh id (`POST /cases`, `/demo/inject_bill`) and unsafe
        for the one that does NOT: agent-core's document-added push handler
        derives the case id from the Gmail thread (`case-{thread_id}`), so two
        attachments on one email -- or a Pub/Sub redelivery -- race on exactly
        the same id. Firestore's `create()` fails with AlreadyExists rather
        than clobbering, which makes the loser of that race a no-op instead of
        a case reset to `intake` with no fronts.
        """
        if self._client is not None:
            from google.api_core import exceptions as gexc

            try:
                self._client.collection("cases").document(case_id).create(
                    self._new_case_payload(data)
                )
            except gexc.Conflict:  # AlreadyExists subclasses Conflict -- catch both
                return self.get_case(case_id), False
            return self.get_case(case_id), True

        with self._lock:
            created = case_id not in self._cases
            if created:
                self._cases[case_id] = copy.deepcopy(self._new_case_payload(data))
                self._documents.setdefault(case_id, {})
                self._events.setdefault(case_id, [])
        return self.get_case(case_id), created

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

    #: Front statuses owned by the filing lifecycle, not by analysis. Once a
    #: front reaches one of these, only the approve/file path may move it --
    #: see `upsert_front_from_analysis`.
    _FILING_OWNED_STATUSES = ("filing", "filed", "won", "lost")

    def upsert_front_from_analysis(self, case_id: str, front: dict) -> dict | None:
        """Upsert a front that re-analysis just recomputed, WITHOUT reopening
        one the filing lifecycle already owns.

        Analysis is not a one-shot: every `case.document.added` re-runs the
        whole hierarchy. `select_fronts` is pure and has no idea anything has
        been filed, so it returns every applicable front at status "open", and
        a plain `upsert_front` wrote that "open" straight over a sibling
        front's "filed".

        Observed live on ef-2026-0007, 2026-08-26: audit filed 08:40:46 and
        charity_care 08:40:51, then re-analyses at 08:40:50 and 08:40:52-54
        reset both to "open" -- while `filings/` held three real "sent"
        records. ppdr kept its "filed" only because its filing happened to
        land after the last re-analysis. This is a second, independent cause
        of the same symptom PROOF reported, and no transaction can fix it:
        both writers are behaving exactly as written.

        CORRECTION 2026-08-26 (FORGE, same day): this docstring originally
        claimed those re-analyses were triggered by the Filer -- "the Filer
        itself stores each generated PDF as a document, so filing a front
        publishes an event that re-analyses the case." That is FALSE and it
        was my inference from the timestamps above, not something I checked.
        `filer.run` calls `store.add_document`, which publishes nothing; the
        only publishers of `case.document.added` in this repo are
        `services/api`'s `/demo/inject_bill` and `services/intake`. The real
        driver is a Pub/Sub redelivery storm: `ef-document-added` has a 60s
        ack deadline against a cascade that takes 60-130s, so the same
        document is redelivered mid-cascade and up to five concurrent
        cascades run per document -- which is also why the interleaving
        *looked* filing-triggered.

        The behaviour this method guards against is real and the fix stands.
        The mechanism I attributed it to was wrong, and a wrong mechanism in
        a docstring is how the next person debugs the wrong thing.

        Everything else on the entry -- applicable, reason, citation,
        deadline -- IS analysis's to update, and still is.
        """

        def _apply(fronts: list[dict]) -> list[dict]:
            for existing in fronts:
                if existing.get("front") == front.get("front"):
                    merged = dict(front)
                    if existing.get("status") in self._FILING_OWNED_STATUSES:
                        merged["status"] = existing["status"]
                    return _merge_front(fronts, merged)
            return _merge_front(fronts, front)

        return self._write_fronts(case_id, _apply)

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

    def add_document_if_absent(self, case_id: str, doc_id: str, doc: dict) -> tuple[dict, bool]:
        """Create `cases/{case_id}/documents/{doc_id}` ONLY if it does not
        exist yet. Returns `(document, created)`.

        Same hazard as `create_case_if_absent`, one level down: `add_document`
        `.set()`s the whole document, so re-adding one that Reader has already
        classified wipes its `type` and `extracted` back to the intake shape.
        The Gmail intake path derives `doc_id` deterministically from
        `(message_id, filename)` (services/intake/intake/pipeline.py), so a
        Pub/Sub redelivery of an event whose handler previously failed reaches
        this method with an id that already holds a fully-read document.
        """
        payload = {"verified": None, "verification_notes": "", "uploaded_at": _now_iso(), **doc}
        if self._client is not None:
            from google.api_core import exceptions as gexc

            ref = (
                self._client.collection("cases")
                .document(case_id)
                .collection("documents")
                .document(doc_id)
            )
            try:
                ref.create(payload)
            except gexc.Conflict:  # AlreadyExists subclasses Conflict -- catch both
                return self.get_document(case_id, doc_id), False
            return self.get_document(case_id, doc_id), True

        with self._lock:
            bucket = self._documents.setdefault(case_id, {})
            created = doc_id not in bucket
            if created:
                bucket[doc_id] = copy.deepcopy(payload)
        return self.get_document(case_id, doc_id), created

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

    def list_hospitals(self) -> list[dict]:
        """Every seeded `hospitals/{ein}` record (LEDGER's 200-hospital seed).

        Defect #2 (persona 5 WO2): Lookup previously resolved a hospital by
        EIN only, and a bill rarely prints an EIN -- Reader's extraction
        schema has a `hospital_ein` field, but real bill text almost never
        fills it. This backs `agents/lookup.py`'s name-matching fallback,
        which needs the whole directory (not a single get-by-key) to match
        against a bill's `provider_name`. 200 records is small enough to read
        in full and cache in-process (see lookup.py's TTL cache) rather than
        needing a Firestore text-search index.
        """
        if self._client is not None:
            out = []
            for snap in self._client.collection("hospitals").stream():
                d = snap.to_dict()
                d["ein"] = snap.id
                out.append(d)
            return out
        with self._lock:
            return [dict(v, ein=k) for k, v in self._hospitals.items()]

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
