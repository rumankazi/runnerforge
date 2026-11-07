"""The reconciler's serial logging backstop (ADR-020): running VMs whose
serial logging the in_progress webhook didn't turn off."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

import pytest

from runnerforge import reconcile
from runnerforge.models import Runner, RunnerForgeVmLabels, VmInfo

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
IID = 7


def _vm(
    name: str = "runnerforge-1-1-1",
    age: timedelta = timedelta(minutes=5),
    status: str = "RUNNING",
    serial: bool | None = True,
    iid: str = str(IID),
    repo: str | None = None,
) -> VmInfo:
    return VmInfo(
        name=name,
        creation_timestamp=NOW - age,
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="1",
            run_id="1",
            run_attempt="1",
            installation_id=iid,
        ),
        repo_full_name=repo,
        status=status,
        serial_logging=serial,
    )


def _busy(runner: str, since: timedelta | None, iid: int = IID) -> tuple:
    fact = {"runner_name": runner}
    if since is not None:
        fact["at"] = NOW - since
    return f"1-{runner}-1", {"installation_id": iid, "in_progress": fact}


async def _pass(vms, records=(), errors=None, tokens=None):
    errors = [] if errors is None else errors
    tokens = {} if tokens is None else tokens
    await reconcile._serial_logging_pass(NOW, vms, list(records), tokens, errors)
    return errors


def _offs(serial_off) -> list[tuple[str, str]]:
    return [(c.args[0], c.args[2]) for c in serial_off.await_args_list]


async def test_a_busy_vm_the_webhook_missed_is_turned_off(serial_off):
    errors = await _pass([_vm()], [_busy("runnerforge-1-1-1", timedelta(minutes=3))])
    assert serial_off.await_args_list == [
        call("runnerforge-1-1-1", "reconcile", "busy", installation_id=IID)
    ]
    assert errors == []


async def test_a_job_that_just_started_is_left_to_its_webhook(serial_off):
    await _pass(
        [_vm(age=timedelta(minutes=30))],
        [_busy("runnerforge-1-1-1", timedelta(seconds=30))],
    )
    serial_off.assert_not_awaited()


async def test_a_busy_fact_without_a_time_counts_as_old(serial_off):
    await _pass([_vm()], [_busy("runnerforge-1-1-1", None)])
    assert _offs(serial_off) == [("runnerforge-1-1-1", "busy")]


async def test_another_installations_record_doesnt_make_a_vm_busy(serial_off):
    """It can't steer the pass at a VM that isn't its own."""
    await _pass([_vm()], [_busy("runnerforge-1-1-1", timedelta(minutes=3), iid=8)])
    serial_off.assert_not_awaited()


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(minutes=5), []),
        (timedelta(minutes=11), [("runnerforge-1-1-1", "aged")]),
    ],
    ids=["booting", "past-boot"],
)
async def test_an_idle_vm_is_turned_off_once_past_boot(serial_off, age, expected):
    await _pass([_vm(age=age)])
    assert _offs(serial_off) == expected


@pytest.mark.parametrize(
    "vm",
    [
        _vm(age=timedelta(hours=1), status="STOPPING"),
        _vm(age=timedelta(hours=1), serial=False),
    ],
    ids=["not-running", "already-off"],
)
async def test_vms_not_logging_are_skipped(serial_off, vm):
    await _pass([vm])
    serial_off.assert_not_awaited()


async def test_an_unknown_state_is_turned_off(serial_off):
    await _pass([_vm(age=timedelta(hours=1), serial=None)])
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]


async def test_a_vm_without_an_installation_is_still_turned_off(serial_off):
    await _pass([_vm(age=timedelta(hours=1), iid="")])
    assert serial_off.await_args.kwargs == {"installation_id": None}


async def test_the_pass_stops_at_its_cap(serial_off, monkeypatch, caplog):
    monkeypatch.setattr(reconcile, "MAX_SERIAL_OFFS", 2)
    vms = [_vm(name=f"runnerforge-{i}-1-1", age=timedelta(hours=1)) for i in range(3)]
    with caplog.at_level(logging.WARNING):
        await _pass(vms)
    assert serial_off.await_count == 2
    [incomplete] = [
        r for r in caplog.records if r.message == "Serial logging pass incomplete"
    ]
    assert incomplete.unchecked == 1


async def test_the_pass_stops_at_its_budget(serial_off, monkeypatch):
    monkeypatch.setattr(reconcile, "SERIAL_BUDGET_SECONDS", -1.0)
    await _pass([_vm(age=timedelta(hours=1))])
    serial_off.assert_not_awaited()


async def test_failures_are_reported_and_the_pass_goes_on(serial_off, caplog):
    serial_off.side_effect = [RuntimeError("boom"), "failed", "conflict", "vm_gone"]
    vms = [_vm(name=f"runnerforge-{i}-1-1", age=timedelta(hours=1)) for i in range(4)]
    with caplog.at_level(logging.ERROR):
        errors = await _pass(vms)
    assert errors == [
        "runnerforge-0-1-1: serial logging RuntimeError",
        "runnerforge-1-1-1: serial logging failed",
        "runnerforge-2-1-1: serial logging conflict",
    ]
    [failed] = [r for r in caplog.records if r.message == "Serial logging off failed"]
    assert failed.error_class == "RuntimeError"
    assert failed.exc_info is None


async def test_the_run_turns_off_the_vms_it_kept(
    monkeypatch, fake_job_store, serial_off
):
    """Wired into run_reconcile, after the orphan cleanup."""
    vms = [_vm(age=timedelta(hours=1))]
    monkeypatch.setattr(
        reconcile, "list_runnerforge_vms", AsyncMock(side_effect=lambda: vms)
    )
    monkeypatch.setattr(reconcile, "_confirm_cutoffs", AsyncMock(return_value={}))
    monkeypatch.setattr(reconcile, "_delete_orphans", AsyncMock(return_value=set()))
    monkeypatch.setattr(reconcile, "_backfill", AsyncMock(return_value=0))

    class Registry:
        async def list_all(self):
            return {}

    monkeypatch.setattr(reconcile, "get_registry", lambda: Registry())
    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    result = await reconcile.run_reconcile(now=NOW)
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]
    assert result.errors == []


# --- GitHub's busy flag, for VMs no open record shows on a job ---

REPO = "octo/repo"


@pytest.fixture
def github(monkeypatch):
    """get_runner answers from `runners` (name -> Runner, None, or an
    exception); every runner and token request is counted."""
    runners: dict = {}
    asked: list[str] = []
    token = AsyncMock(return_value="tok")

    async def get_runner(tok, repo, name):
        assert (tok, repo) == ("tok", REPO)
        asked.append(name)
        answer = runners.get(name)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(reconcile, "get_installation_token", token)
    monkeypatch.setattr(reconcile, "get_runner", get_runner)
    return runners, token, asked


def _runner(name: str, busy: bool) -> Runner:
    return Runner(id=1, name=name, status="online", busy=busy)


def _aged(name: str = "runnerforge-1-1-1", **kwargs) -> VmInfo:
    return _vm(name=name, age=timedelta(minutes=30), repo=REPO, **kwargs)


async def test_an_aged_vm_github_calls_busy_is_runner_busy(serial_off, github):
    """Its in_progress webhook was lost before a record check wrote the
    fact, or is still on its way: its own reason, so the alert can tell."""
    runners, _, _ = github
    runners["runnerforge-1-1-1"] = _runner("runnerforge-1-1-1", busy=True)
    await _pass([_aged()])
    assert serial_off.await_args_list == [
        call("runnerforge-1-1-1", "reconcile", "runner_busy", installation_id=IID)
    ]


@pytest.mark.parametrize(
    "answer",
    [_runner("runnerforge-1-1-1", busy=False), None],
    ids=["idle", "not-registered"],
)
async def test_an_aged_vm_github_doesnt_call_busy_stays_aged(
    serial_off, github, answer
):
    runners, _, _ = github
    runners["runnerforge-1-1-1"] = answer
    await _pass([_aged()])
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]


async def test_a_failed_check_still_turns_it_off(serial_off, github, caplog):
    runners, _, _ = github
    runners["runnerforge-1-1-1"] = RuntimeError("github down")
    runners["runnerforge-2-1-1"] = _runner("runnerforge-2-1-1", busy=True)
    with caplog.at_level(logging.WARNING):
        await _pass([_aged("runnerforge-1-1-1"), _aged("runnerforge-2-1-1")])
    # One VM's failure doesn't cost the installation's other answers.
    assert _offs(serial_off) == [
        ("runnerforge-1-1-1", "aged"),
        ("runnerforge-2-1-1", "runner_busy"),
    ]
    [line] = [
        r for r in caplog.records if r.message == "Serial logging runner check failed"
    ]
    assert (line.vm_name, line.error_class) == ("runnerforge-1-1-1", "RuntimeError")


async def test_a_failed_token_fails_its_installations_checks(
    serial_off, github, caplog
):
    _, token, asked = github
    token.side_effect = RuntimeError("no token")
    with caplog.at_level(logging.WARNING):
        await _pass([_aged("runnerforge-1-1-1"), _aged("runnerforge-2-1-1")])
    assert asked == []
    assert _offs(serial_off) == [
        ("runnerforge-1-1-1", "aged"),
        ("runnerforge-2-1-1", "aged"),
    ]
    lines = [
        (r.vm_name, r.error_class)
        for r in caplog.records
        if r.message == "Serial logging runner check failed"
    ]
    assert lines == [
        ("runnerforge-1-1-1", "RuntimeError"),
        ("runnerforge-2-1-1", "RuntimeError"),
    ]


async def test_a_slow_github_times_out_and_every_vm_is_still_turned_off(
    serial_off, github, monkeypatch, caplog
):
    """The checks share one timeout, so a slow GitHub costs one timeout,
    not one per VM, and can't run the pass out of budget."""
    monkeypatch.setattr(reconcile, "SERIAL_RUNNER_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(reconcile, "SERIAL_BUDGET_SECONDS", 0.5)

    async def slow(token, repo, name):
        await asyncio.sleep(0.2)
        return _runner(name, busy=True)  # too late to count

    monkeypatch.setattr(reconcile, "get_runner", slow)
    vms = [_aged(f"runnerforge-{i}-1-1") for i in range(5)]
    with caplog.at_level(logging.WARNING):
        await _pass(vms)
    assert _offs(serial_off) == [(vm.name, "aged") for vm in vms]
    errors = {
        r.error_class
        for r in caplog.records
        if r.message == "Serial logging runner check failed"
    }
    assert errors == {"TimeoutError"}
    assert not any(
        r.message == "Serial logging pass incomplete" for r in caplog.records
    )


@pytest.mark.parametrize(
    "vm",
    [_vm(age=timedelta(minutes=30)), _aged(iid="")],
    ids=["no-repo", "no-installation"],
)
async def test_without_a_repo_or_installation_github_isnt_asked(serial_off, github, vm):
    _, token, _ = github
    await _pass([vm])
    token.assert_not_awaited()
    assert _offs(serial_off) == [("runnerforge-1-1-1", "aged")]


async def test_vms_on_a_known_job_dont_ask_github(serial_off, github):
    _, token, _ = github
    await _pass([_aged()], [_busy("runnerforge-1-1-1", timedelta(minutes=3))])
    token.assert_not_awaited()


async def test_one_token_per_installation(serial_off, github):
    _, token, asked = github
    tokens: dict = {}
    await _pass([_aged(f"runnerforge-{i}-1-1") for i in range(3)], tokens=tokens)
    assert token.await_count == 1
    assert sorted(asked) == [f"runnerforge-{i}-1-1" for i in range(3)]
    assert tokens == {IID: "tok"}


async def test_the_runs_token_is_reused(serial_off, github):
    """The rest of the run already fetched it."""
    _, token, asked = github
    await _pass([_aged()], tokens={IID: "tok"})
    token.assert_not_awaited()
    assert asked == ["runnerforge-1-1-1"]


async def test_only_vms_the_cap_reaches_are_checked(serial_off, github, monkeypatch):
    """Known jobs go first, and aged VMs past the cap cost no call."""
    monkeypatch.setattr(reconcile, "MAX_SERIAL_OFFS", 2)
    _, _, asked = github
    vms = [_aged(f"runnerforge-{i}-1-1") for i in range(3)]
    await _pass(vms, [_busy("runnerforge-2-1-1", timedelta(minutes=3))])
    assert _offs(serial_off) == [
        ("runnerforge-2-1-1", "busy"),
        ("runnerforge-0-1-1", "aged"),
    ]
    assert asked == ["runnerforge-0-1-1"]


async def test_the_run_shares_its_tokens_with_the_pass(
    monkeypatch, fake_job_store, serial_off, github
):
    """run_reconcile hands the pass the token cache the orphan cleanup
    filled, so the pass fetches none of its own."""
    _, token, asked = github
    vms = [_aged()]
    monkeypatch.setattr(
        reconcile, "list_runnerforge_vms", AsyncMock(side_effect=lambda: vms)
    )
    monkeypatch.setattr(reconcile, "_confirm_cutoffs", AsyncMock(return_value={}))
    monkeypatch.setattr(reconcile, "_backfill", AsyncMock(return_value=0))

    async def orphans(now, vms, listed, busy, tokens, *args):
        tokens[IID] = "tok"
        return set()

    monkeypatch.setattr(reconcile, "_delete_orphans", orphans)

    class Registry:
        async def list_all(self):
            return {}

    monkeypatch.setattr(reconcile, "get_registry", lambda: Registry())
    monkeypatch.setattr(reconcile, "ADMISSION", "allowlist")
    await reconcile.run_reconcile(now=NOW)
    token.assert_not_awaited()
    assert asked == ["runnerforge-1-1-1"]
