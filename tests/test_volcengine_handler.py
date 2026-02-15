import volcengine_handler as vh


def test_volcengine_handler_dry_run(monkeypatch):
    calls = {"dry_run": None, "full_mode": None, "session_only": None}

    def _fake_run_once(dry_run=False, full_mode=False, session_only=False):
        calls["dry_run"] = dry_run
        calls["full_mode"] = full_mode
        calls["session_only"] = session_only
        return 0

    monkeypatch.setattr(vh, "run_once", _fake_run_once)
    event = {"dry_run": True, "full_mode": False, "session_only": True}
    resp = vh.handler(event, context={})

    assert resp["statusCode"] == 200
    assert resp["body"]["ok"] is True
    assert calls["dry_run"] is True
    assert calls["full_mode"] is False
    assert calls["session_only"] is True
