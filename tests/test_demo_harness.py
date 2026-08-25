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

    def test_plan_reseeds_the_real_hospital_count(self):
        from fixtures.cases_data import HOSPITALS
        from fixtures.demo_reset import plan

        hospitals = _hospitals()
        assert len(hospitals) == len(HOSPITALS)
        lines = "\n".join(plan(hospitals))
        assert f"re-seed {len(HOSPITALS)} hospitals" in lines

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
        """WO4 acceptance: the happy path twice in a row."""
        text = (REPO_ROOT / "fixtures" / "Makefile").read_text()
        cycle_line = next(ln for ln in text.splitlines() if ln.startswith("demo-cycle:"))
        assert cycle_line.split(":", 1)[1].split() == [
            "demo-reset",
            "demo-run",
            "demo-reset",
            "demo-run",
        ]
