from cloud_runner import run_once


def test_cloud_runner_returns_zero_on_success():
    assert run_once(dry_run=True) == 0
