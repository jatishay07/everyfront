"""Demo rehearsal harness tests -- PROOF (persona 7), work order 4.

The real `demo-reset` / `demo-run` targets need a live GCP project and a
deployed services/api -- neither exists in this sandbox (see
tests/test_e2e_happy_path.py's docstring for why). What CAN be tested without
any of that is the harness scripts' own logic: `--dry-run` must need no
credentials, must not import a GCP SDK at all, must describe the same fixed
plan every time (idempotent, matching WO4's "safe to run twice"), and must
reference the actual generated fixture bundle rather than something made up.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "fixtures" / "generated"
PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
    )


def _hospitals() -> dict:
    return json.loads((GENERATED / "hospitals.json").read_text())


class TestDemoResetDryRun:
    def test_dry_run_exits_zero_without_gcp_credentials(self, monkeypatch):
        result = _run("fixtures/demo_reset.py", "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "dry-run" in result.stdout

    def test_dry_run_mentions_every_reset_collection(self):
        from fixtures.demo_reset import RESET_COLLECTIONS, plan

        lines = "\n".join(plan(_hospitals()))
        for coll in RESET_COLLECTIONS:
            assert f"{coll}/" in lines

    def test_plan_verifies_the_real_hospital_count_without_writing(self):
        """AMENDED 2026-08-25: this used to re-seed (write) hospitals/{ein};
        now it only verifies they exist, since LEDGER's real 200-hospital
        Schedule H seed owns that collection and a write here would clobber
        it with this corpus's 4-hospital placeholder record. See
        fixtures/demo_reset.py's module docstring."""
        from fixtures.cases_data import HOSPITALS
        from fixtures.demo_reset import plan

        hospitals = _hospitals()
        assert len(hospitals) == len(HOSPITALS)
        lines = "\n".join(plan(hospitals))
        assert f"verify (read-only, never write) that {len(HOSPITALS)} hospitals" in lines
        assert "re-seed" not in lines

    def test_plan_is_deterministic(self):
        from fixtures.demo_reset import plan

        hospitals = _hospitals()
        assert plan(hospitals) == plan(hospitals)

    def test_real_reset_without_project_env_is_blocked_not_crashed(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        result = _run("fixtures/demo_reset.py")
        assert result.returncode == 1
        assert "BLOCKED" in result.stdout + result.stderr


class TestDemoRunDryRun:
    def test_dry_run_exits_zero_without_a_live_api(self):
        result = _run("fixtures/demo_run.py", "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "case_01_uninsured_gfe_ca" in result.stdout

    def test_dry_run_reports_every_front_from_the_fixture(self):
        case = json.loads(
            (
                REPO_ROOT
                / "fixtures"
                / "generated"
                / "cases"
                / "case_01_uninsured_gfe_ca"
                / "case.json"
            ).read_text()
        )
        result = _run("fixtures/demo_run.py", "--dry-run")
        for front in case["expected"]["fronts_reference_model"]:
            assert front["front"] in result.stdout

    def test_real_run_without_api_url_is_blocked_not_crashed(self, monkeypatch):
        monkeypatch.delenv("EVERYFRONT_API_URL", raising=False)
        result = _run("fixtures/demo_run.py")
        assert result.returncode == 1
        assert "BLOCKED" in result.stdout + result.stderr


class TestMakefileWiring:
    def test_fixtures_makefile_declares_every_required_target(self):
        text = (REPO_ROOT / "fixtures" / "Makefile").read_text()
        for target in ("demo-reset", "demo-run", "demo-cycle"):
            assert f"{target}:" in text

    def test_demo_cycle_runs_reset_run_reset_run(self):
        """WO4 acceptance: the happy path twice in a row.

        This asserts what make DOES, not what the Makefile SAYS. The previous
        version of this test matched the literal prerequisite list
        `demo-cycle: demo-reset demo-run demo-reset demo-run` -- which passed
        happily while make, which deduplicates a target's prerequisites, ran
        each phony target exactly ONCE and exited 0. Half the acceptance test,
        green the whole time. Expanding the recipe with `make -n` is the only
        form of this check that can fail when the behaviour regresses.
        """
        result = subprocess.run(
            ["make", "-n", "-f", "fixtures/Makefile", "demo-cycle"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        phases = [
            "reset" if "demo_reset.py" in ln else "run"
            for ln in result.stdout.splitlines()
            if "demo_reset.py" in ln or "demo_run.py" in ln
        ]
        assert phases == ["reset", "run", "reset", "run"], result.stdout

    def test_demo_reset_target_passes_reseed(self):
        """WO6 task 1: `make demo-reset` must genuinely purge AND reseed, not
        just purge -- see fixtures/demo_reset.py's module docstring."""
        lines = (REPO_ROOT / "fixtures" / "Makefile").read_text().splitlines()
        header = next(i for i, ln in enumerate(lines) if ln.startswith("demo-reset:"))
        recipe = lines[header + 1]
        assert "--reseed" in recipe


class TestHumanPlausibleCaseIds:
    """WO6 task 1: 'reseed your 8 cases with human-plausible case
    identifiers, not demo-<fixture>-<uuid>.'"""

    def test_every_corpus_case_has_a_human_id(self):
        from fixtures.cases_data import CASES
        from fixtures.demo_reset import HUMAN_CASE_IDS

        assert set(HUMAN_CASE_IDS) == {c.case_id for c in CASES}

    def test_human_ids_are_unique_and_dont_look_like_fixture_scratch_data(self):
        from fixtures.demo_reset import HUMAN_CASE_IDS

        ids = list(HUMAN_CASE_IDS.values())
        assert len(ids) == len(set(ids))
        for case_id in ids:
            assert not case_id.startswith("demo-"), case_id
            assert "uuid" not in case_id.lower()

    def test_live_demo_fixture_is_excluded_from_the_quiet_reseed(self):
        """case_01 is injected LIVE by demo_run.py, on camera -- reseeding it
        too would leave 9 cases in Firestore instead of the §7 target of 8."""
        from fixtures.demo_reset import HUMAN_CASE_IDS, LIVE_DEMO_FIXTURE, RESEED_FIXTURES

        assert LIVE_DEMO_FIXTURE not in RESEED_FIXTURES
        assert set(RESEED_FIXTURES) | {LIVE_DEMO_FIXTURE} == set(HUMAN_CASE_IDS)
        assert len(RESEED_FIXTURES) == 7

    def test_demo_reset_dry_run_plan_names_every_reseed_target(self):
        result = _run("fixtures/demo_reset.py", "--dry-run")
        assert result.returncode == 0, result.stderr
        from fixtures.demo_reset import HUMAN_CASE_IDS, RESEED_FIXTURES

        for fixture_name in RESEED_FIXTURES:
            assert HUMAN_CASE_IDS[fixture_name] in result.stdout


class _FakeSnapshot:
    def __init__(self, doc_id: str, data: dict | None, reference: _FakeDocRef | None = None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = reference

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocRef:
    """Just enough of the google-cloud-firestore DocumentReference surface
    for fixtures.demo_reset.rename_case / _delete_collection to run against,
    entirely offline -- no GCP project or credentials needed."""

    def __init__(self, docs: dict, path: str):
        self._docs = docs
        self.path = path

    def get(self):
        return _FakeSnapshot(self.path.rsplit("/", 1)[-1], self._docs.get(self.path), self)

    def set(self, data: dict) -> None:
        self._docs[self.path] = dict(data)

    def update(self, patch: dict) -> None:
        self._docs.setdefault(self.path, {}).update(patch)

    def delete(self) -> None:
        self._docs.pop(self.path, None)

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._docs, f"{self.path}/{name}")

    @property
    def reference(self) -> _FakeDocRef:
        return self


class _FakeCollectionRef:
    def __init__(self, docs: dict, path: str, where: tuple | None = None):
        self._docs = docs
        self.path = path
        self._where = where

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._docs, f"{self.path}/{doc_id}")

    def where(self, field: str, op: str, value) -> _FakeCollectionRef:
        assert op == "=="
        return _FakeCollectionRef(self._docs, self.path, where=(field, value))

    def limit(self, _n: int) -> _FakeCollectionRef:
        return self

    def stream(self):
        prefix = self.path + "/"
        out = []
        for path, data in list(self._docs.items()):
            if not path.startswith(prefix) or "/" in path[len(prefix) :]:
                continue  # only immediate children, matching a real subcollection query
            if self._where is not None:
                field, value = self._where
                if data.get(field) != value:
                    continue
            out.append(_FakeSnapshot(path[len(prefix) :], data, _FakeDocRef(self._docs, path)))
        return out


class _FakeFirestoreClient:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self.docs, name)


class TestRenameCase:
    """WO6 task 1's actual data-safety mechanism, tested against a fake
    Firestore client (no live GCP project needed): renaming a case must
    preserve its documents/events subcollections, repoint any filings that
    reference it, rewrite each event's own embedded case_id, and leave no
    trace of the old id behind."""

    def _client_with_one_case(self) -> _FakeFirestoreClient:
        from fixtures.demo_reset import rename_case  # noqa: F401 -- import check

        client = _FakeFirestoreClient()
        old = "demo-case_03_in_collections_ca-abcd1234"
        client.docs[f"cases/{old}"] = {"status": "filing", "patient": {"name": "Denise Okafor"}}
        client.docs[f"cases/{old}/documents/doc1"] = {"type": "collection_notice"}
        client.docs[f"cases/{old}/events/ev1"] = {
            "case_id": old,
            "agent": "reader",
            "action": "classified",
        }
        client.docs["filings/f1"] = {"case_id": old, "front": "debt_validation"}
        client.docs["filings/f2"] = {"case_id": "some-other-case", "front": "ppdr"}
        return client, old

    def test_rename_moves_the_case_doc_and_deletes_the_old_one(self):
        from fixtures.demo_reset import rename_case

        client, old = self._client_with_one_case()
        rename_case(client, old, "ef-2026-0003")

        assert client.docs["cases/ef-2026-0003"] == {
            "status": "filing",
            "patient": {"name": "Denise Okafor"},
        }
        assert f"cases/{old}" not in client.docs

    def test_rename_carries_documents_subcollection_verbatim(self):
        from fixtures.demo_reset import rename_case

        client, old = self._client_with_one_case()
        rename_case(client, old, "ef-2026-0003")

        assert client.docs["cases/ef-2026-0003/documents/doc1"] == {"type": "collection_notice"}
        assert f"cases/{old}/documents/doc1" not in client.docs

    def test_rename_rewrites_each_event_s_own_case_id_field(self):
        from fixtures.demo_reset import rename_case

        client, old = self._client_with_one_case()
        rename_case(client, old, "ef-2026-0003")

        event = client.docs["cases/ef-2026-0003/events/ev1"]
        assert event["case_id"] == "ef-2026-0003"
        assert event["agent"] == "reader"  # everything else about the event is untouched

    def test_rename_repoints_only_the_matching_filing(self):
        from fixtures.demo_reset import rename_case

        client, old = self._client_with_one_case()
        rename_case(client, old, "ef-2026-0003")

        assert client.docs["filings/f1"]["case_id"] == "ef-2026-0003"
        assert client.docs["filings/f2"]["case_id"] == "some-other-case"  # untouched

    def test_rename_raises_on_a_case_that_does_not_exist(self):
        from fixtures.demo_reset import rename_case

        client = _FakeFirestoreClient()
        with pytest.raises(RuntimeError):
            rename_case(client, "no-such-case", "ef-2026-0003")
