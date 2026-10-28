import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from runnerforge.github_client import RepoNotFound
from runnerforge.models import Runner, RunnerForgeVmLabels, VmInfo
from runnerforge.sweep import run_sweep


def _vm(
    age_hours: int,
    installation_id: str = "100",
    name_suffix: str = "",
    repo_full_name: str | None = "foo/bar",
) -> VmInfo:
    """Helper to construct a VmInfo with a specific age + optional name suffix to disambiguate."""
    name = f"runnerforge-vm-{age_hours}h" + (f"-{name_suffix}" if name_suffix else "")
    return VmInfo(
        name=name,
        creation_timestamp=datetime.now(UTC) - timedelta(hours=age_hours),
        labels=RunnerForgeVmLabels(
            runner="runnerforge",
            job_id="12",
            run_id="23",
            run_attempt="33",
            repository_id="42",
            installation_id=installation_id,
        ),
        repo_full_name=repo_full_name,
    )


def _busy_runner() -> Runner:
    return Runner(id=7, name="runnerforge-vm-3h", status="online", busy=True)


def test_run_sweep_skips_young_vms(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=0), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    result = asyncio.run(run_sweep())

    assert result.deleted == 0
    assert result.skipped == 2
    assert result.checked == 2
    delete_mock.assert_not_awaited()
    runner_mock.assert_not_awaited()


def test_run_sweep_deletes_vm_when_runner_is_gone(monkeypatch):
    # Ephemeral runner de-registered after its one job → nothing left to run.
    list_mock = AsyncMock(return_value=[_vm(age_hours=3), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    runner_mock.return_value = None
    installation_token_mock.return_value = "installation_token"
    result = asyncio.run(run_sweep())
    assert result.deleted == 1
    assert result.skipped == 1
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-3h", reason="sweep"
    )
    runner_mock.assert_awaited_once()
    installation_token_mock.assert_awaited_once_with(100)  # int, not str


def test_run_sweep_keeps_vm_while_its_runner_is_busy(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=3), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    runner_mock.return_value = _busy_runner()
    installation_token_mock.return_value = "installation_token"
    result = asyncio.run(run_sweep())
    assert result.deleted == 0
    assert result.skipped == 2
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_not_awaited()
    runner_mock.assert_awaited_once()
    installation_token_mock.assert_awaited_once_with(100)  # int, not str


def test_run_sweep_queries_github_with_exact_repo_full_name(monkeypatch):
    """Regression: the sweep used to send the lossy `owner_repo` label to
    GitHub, got a 404, treated it as an orphan and deleted running VMs. The
    repo must reach GitHub exactly as GitHub knows it — mixed case and
    dots included (neither survives a GCE label)."""
    list_mock = AsyncMock(
        return_value=[_vm(age_hours=3, repo_full_name="RumanKazi/Foo.js")]
    )
    runner_mock = AsyncMock(return_value=_busy_runner())
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", AsyncMock())
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", AsyncMock(return_value="tok")
    )

    asyncio.run(run_sweep())

    # Asks about the runner on this VM (VM name == runner name), not the job
    # the VM was booted for.
    runner_mock.assert_awaited_once_with(
        installation_token="tok",
        repo_full_name="RumanKazi/Foo.js",
        runner_name="runnerforge-vm-3h",
    )


def test_run_sweep_skips_runner_check_when_repo_unknown(monkeypatch, caplog):
    """No exact repo → no GitHub call (a 404 would read as 'orphan'). The VM is
    left alone until the hard limit."""
    list_mock = AsyncMock(return_value=[_vm(age_hours=3, repo_full_name=None)])
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr("runnerforge.sweep.get_installation_token", token_mock)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(run_sweep())

    assert result.skipped == 1
    assert result.deleted == 0
    delete_mock.assert_not_awaited()
    runner_mock.assert_not_awaited()
    token_mock.assert_not_awaited()
    log = next(r for r in caplog.records if getattr(r, "reason", "") == "unknown_repo")
    assert log.levelno == logging.WARNING
    assert log.vm_name == "runnerforge-vm-3h"


def test_run_sweep_hard_limit_applies_even_when_repo_unknown(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=7, repo_full_name=None)])
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)

    result = asyncio.run(run_sweep())

    assert result.deleted == 1
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-7h", reason="sweep"
    )


def _patch_github(monkeypatch, runner_mock):
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", AsyncMock(return_value="tok")
    )


def test_run_sweep_keeps_swapped_vm_busy_with_another_job(monkeypatch, caplog):
    """Regression: the sweep used to check the job the VM was BOOTED for. After
    a job swap, VM-for-job-12 runs another job while job 12 finished elsewhere,
    so the VM was deleted mid-job. The runner's own busy flag is what counts,
    whichever job it is running."""
    monkeypatch.setattr(
        "runnerforge.sweep.list_runnerforge_vms", AsyncMock(return_value=[_vm(3)])
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    _patch_github(monkeypatch, AsyncMock(return_value=_busy_runner()))

    with caplog.at_level(logging.INFO):
        result = asyncio.run(run_sweep())

    assert result.skipped == 1
    delete_mock.assert_not_awaited()
    assert any(getattr(r, "reason", "") == "runner_busy" for r in caplog.records)


def test_run_sweep_deletes_idle_runner_vm(monkeypatch, caplog):
    """Registered but idle past MAX_LIFETIME: its job went to another runner
    and nothing is coming for it."""
    monkeypatch.setattr(
        "runnerforge.sweep.list_runnerforge_vms", AsyncMock(return_value=[_vm(3)])
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    idle = Runner(id=7, name="runnerforge-vm-3h", status="online", busy=False)
    _patch_github(monkeypatch, AsyncMock(return_value=idle))

    with caplog.at_level(logging.INFO):
        result = asyncio.run(run_sweep())

    assert result.deleted == 1
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-3h", reason="sweep"
    )
    assert any(getattr(r, "reason", "") == "runner_idle" for r in caplog.records)


def test_run_sweep_skips_vm_when_repo_not_found(monkeypatch, caplog):
    """A 404 (repo deleted or no longer visible to the App) is no evidence the
    runner is idle. Leave it to the hard limit / GCE max_run_duration."""
    monkeypatch.setattr(
        "runnerforge.sweep.list_runnerforge_vms", AsyncMock(return_value=[_vm(3)])
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    _patch_github(monkeypatch, AsyncMock(side_effect=RepoNotFound("foo/bar")))

    with caplog.at_level(logging.INFO):
        result = asyncio.run(run_sweep())

    assert result.skipped == 1
    assert result.errors == []
    delete_mock.assert_not_awaited()
    log = next(
        r for r in caplog.records if getattr(r, "reason", "") == "repo_not_found"
    )
    assert log.levelno == logging.WARNING


def test_run_sweep_deletes_vm_past_hard_limit(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=7), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)

    runner_mock.return_value = None
    result = asyncio.run(run_sweep())
    assert result.deleted == 1
    assert result.skipped == 1
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-7h", reason="sweep"
    )
    # Hard limit short-circuits the GitHub round-trip
    runner_mock.assert_not_awaited()


def test_run_sweep_caches_installation_token_per_installation(monkeypatch):
    # Two old VMs from same installation_id — token should be fetched ONCE, used for both
    list_mock = AsyncMock(
        return_value=[
            _vm(age_hours=3, installation_id="200", name_suffix="a"),
            _vm(age_hours=3, installation_id="200", name_suffix="b"),
        ]
    )
    delete_mock = AsyncMock()
    runner_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    runner_mock.return_value = None
    installation_token_mock.return_value = "installation_token"

    result = asyncio.run(run_sweep())
    assert result.deleted == 2
    assert result.errors == []
    # Cache works: one token fetch despite two VMs sharing installation_id
    installation_token_mock.assert_awaited_once_with(200)
    # But per-VM job status is still checked
    assert runner_mock.await_count == 2


def test_run_sweep_continues_when_delete_raises(monkeypatch):
    # First VM delete raises; second succeeds. Whole sweep continues, error recorded.
    list_mock = AsyncMock(
        return_value=[
            _vm(age_hours=3, name_suffix="a"),
            _vm(age_hours=3, name_suffix="b"),
        ]
    )
    delete_mock = AsyncMock(side_effect=[Exception("simulated GCE error"), None])
    runner_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_runner", runner_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    runner_mock.return_value = None
    installation_token_mock.return_value = "installation_token"

    result = asyncio.run(run_sweep())
    assert result.deleted == 1  # second VM succeeded
    assert result.checked == 2
    assert len(result.errors) == 1
    assert "runnerforge-vm-3h-a" in result.errors[0]
    assert delete_mock.await_count == 2  # both attempted (per-VM exception isolated)


def test_run_sweep_continues_when_installation_id_label_is_not_numeric(monkeypatch):
    """A VM with a non-numeric installation_id label used to raise outside the
    per-VM try and abort the whole sweep. It must be recorded as an error while
    the other VMs are still swept."""
    list_mock = AsyncMock(
        return_value=[
            _vm(age_hours=7, installation_id="abc", name_suffix="bad"),
            _vm(age_hours=7, name_suffix="good"),
        ]
    )
    delete_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)

    result = asyncio.run(run_sweep())

    assert result.checked == 2
    assert result.deleted == 1
    assert len(result.errors) == 1
    assert "runnerforge-vm-7h-bad" in result.errors[0]
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-7h-good", reason="sweep"
    )
