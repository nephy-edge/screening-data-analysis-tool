"""Usage tracker: tracked POSTs reach the sheet writer with the signed-in
user; untracked calls and a disabled config don't."""

import sys

from fastapi.testclient import TestClient

sys.path.insert(0, "scripts")
import web_api.main as m  # noqa: E402

usage_log = m.usage_log


def test_row_shape():
    row = usage_log._row("/api/analyse/rental", "a@lendable.io", 200, 1.234)
    assert row[1:] == ["a@lendable.io", "analyse", "rental", "/api/analyse/rental", 200, 1.23]


def test_middleware_logs_tracked_calls_only(monkeypatch):
    rows = []
    monkeypatch.setitem(usage_log.USAGE_LOG, "sheet_id", "sheet123")
    monkeypatch.setattr(usage_log, "_service_account_info", lambda: {"client_email": "x"})
    monkeypatch.setattr(usage_log, "_safe_append", rows.append)
    monkeypatch.setattr(usage_log.threading, "Thread", _InlineThread)

    client = TestClient(m.app, base_url="https://testserver")
    client.post("/api/auth/dummy", json={"email": "a@lendable.io"})
    # Turn "deployed" on only after signing in: under Cognito the dummy sign-in is refused.
    monkeypatch.setattr(usage_log.cognito, "is_configured", lambda: True)
    client.post("/api/analyse/revolving", json={})
    client.get("/api/auth/me")

    assert [(r[1], r[2], r[3]) for r in rows] == [("a@lendable.io", "analyse", "revolving")]


def test_disabled_without_sheet_id(monkeypatch):
    rows = []
    monkeypatch.setitem(usage_log.USAGE_LOG, "sheet_id", "")
    monkeypatch.setattr(usage_log, "_safe_append", rows.append)
    usage_log.log_call("/api/analyse/lending", "POST", "a@lendable.io", 200, 1.0)
    assert rows == []


class _InlineThread:
    def __init__(self, target, args, daemon):
        self._run = lambda: target(*args)

    def start(self):
        self._run()


def test_local_runs_not_logged(monkeypatch):
    rows = []
    monkeypatch.setitem(usage_log.USAGE_LOG, "sheet_id", "sheet123")
    monkeypatch.setattr(usage_log, "_service_account_info", lambda: {"client_email": "x"})
    monkeypatch.setattr(usage_log.cognito, "is_configured", lambda: False)
    monkeypatch.setattr(usage_log, "_safe_append", rows.append)
    usage_log.log_call("/api/analyse/lending", "POST", "a@lendable.io", 200, 1.0)
    assert rows == []
