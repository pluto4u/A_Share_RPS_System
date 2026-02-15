from cloud_runner import run_once


def test_cloud_runner_returns_zero_on_success():
    assert run_once(dry_run=True) == 0


def test_cloud_runner_skips_when_session_only_and_non_trading(monkeypatch):
    import cloud_runner

    # patch market_gambler_watch imports used in run_once path
    class _FakeWatch:
        @staticmethod
        def is_trading_session(_now=None):
            return False

        @staticmethod
        def run_once(full=False, feishu=False):
            raise AssertionError("run_once should not be called in non-trading session")

    monkeypatch.setitem(__import__("sys").modules, "market_gambler_watch", _FakeWatch)
    assert cloud_runner.run_once(dry_run=False, full_mode=False, session_only=True) == 0
