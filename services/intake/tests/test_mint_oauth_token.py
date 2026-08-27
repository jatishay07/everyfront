"""`scripts/mint_oauth_token.py` -- the one irreducibly-human step in the
go-live runbook (infra/OAUTH.md step 4).

Loaded by path rather than imported as a module: `scripts/` is not a package
and is deliberately not on `sys.path` (nothing in the service imports it, and
`google-auth-oauthlib` is only needed by the human running it). Everything
this file touches is faked -- no browser, no network, no Google.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mint_oauth_token.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("mint_oauth_token_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCreds:
    client_id = "fake-client-id.apps.googleusercontent.com"
    client_secret = "fake-client-secret"
    refresh_token = "fake-refresh-token"


class _FakeFlow:
    """Records how `run_local_server` was called so the test can assert on it."""

    calls: list[dict] = []

    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        cls.calls = []
        return cls()

    def run_local_server(self, **kwargs):
        type(self).calls.append(kwargs)
        return _FakeCreds()


@pytest.fixture
def script(monkeypatch):
    module = _load_script()
    # The script imports InstalledAppFlow lazily *inside* main(), so the fake
    # has to be reachable at `google_auth_oauthlib.flow.InstalledAppFlow`.
    flow_mod = sys.modules.setdefault("google_auth_oauthlib", type(sys)("google_auth_oauthlib"))
    submodule = type(sys)("google_auth_oauthlib.flow")
    submodule.InstalledAppFlow = _FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", flow_mod)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", submodule)
    monkeypatch.setattr(flow_mod, "flow", submodule, raising=False)
    return module


def _run(script, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mint_oauth_token.py",
            "--client-id",
            "cid.apps.googleusercontent.com",
            "--client-secret",
            "csecret",
        ],
    )
    script.main()
    return capsys.readouterr()


def test_requests_prompt_consent_so_a_re_mint_returns_a_refresh_token(script, monkeypatch, capsys):
    """REGRESSION: without `prompt="consent"`, Google issues a refresh token
    only on the FIRST grant for a given (client_id, account) pair and silently
    omits it from every subsequent exchange -- so re-running this script (after
    a typo, a rotation, a second demo account) completes the entire browser
    consent dance and then dies, sending a human off to revoke the grant by
    hand at myaccount.google.com/permissions in the middle of the runbook.

    `access_type=offline`, which google-auth-oauthlib sets by default, is NOT
    sufficient for this: it asks Google to issue a refresh token, it does not
    ask Google to re-issue one for an account that has already consented.
    """
    _run(script, monkeypatch, capsys)

    assert _FakeFlow.calls, "run_local_server was never called"
    kwargs = _FakeFlow.calls[0]
    assert kwargs.get("prompt") == "consent", (
        "mint_oauth_token.py must pass prompt='consent' to run_local_server, "
        f"otherwise a second mint silently returns no refresh_token; got {kwargs!r}"
    )


def test_prints_the_three_env_vars_go_live_needs(script, monkeypatch, capsys):
    """The output contract with `go_live.sh`: these three names, verbatim."""
    out = _run(script, monkeypatch, capsys).out
    assert "GOOGLE_OAUTH_CLIENT_ID" in out
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in out
    assert "GOOGLE_OAUTH_REFRESH_TOKEN" in out
    assert _FakeCreds.refresh_token in out


def test_scopes_are_exactly_the_three_oauth_md_documents(script):
    """infra/OAUTH.md's scope table names this list as its source of truth --
    a scope added here without updating that table, or vice versa, means a
    human consents to a set of scopes the doc does not describe. A refresh
    token is only ever valid for the scopes it was minted under, so a drift
    here fails at the FIRST Calendar/Drive call, long after the consent click.
    """
    assert script.SCOPES == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/drive.file",
    ]
