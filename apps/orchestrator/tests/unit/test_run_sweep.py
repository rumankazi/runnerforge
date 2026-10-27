import asyncio
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from runnerforge.models import JobStatusResponse, RunnerForgeVmLabels, VmInfo
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


def test_run_sweep_skips_young_vms(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=0), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    result = asyncio.run(run_sweep())

    assert result.deleted == 0
    assert result.skipped == 2
    assert result.checked == 2
    delete_mock.assert_not_awaited()
    job_status_mock.assert_not_awaited()


def test_run_sweep_deletes_vm_with_completed_job(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=3), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    job_status_mock.return_value = JobStatusResponse(
        status="completed", conclusion="success"
    )
    installation_token_mock.return_value = "installation_token"
    result = asyncio.run(run_sweep())
    assert result.deleted == 1
    assert result.skipped == 1
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-3h", reason="sweep"
    )
    job_status_mock.assert_awaited_once()
    installation_token_mock.assert_awaited_once_with(100)  # int, not str


def test_run_sweep_does_not_delete_vm_with_running_job(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=3), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    job_status_mock.return_value = JobStatusResponse(
        status="in_progress", conclusion=None
    )
    installation_token_mock.return_value = "installation_token"
    result = asyncio.run(run_sweep())
    assert result.deleted == 0
    assert result.skipped == 2
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_not_awaited()
    job_status_mock.assert_awaited_once()
    installation_token_mock.assert_awaited_once_with(100)  # int, not str


def test_run_sweep_queries_github_with_exact_repo_full_name(monkeypatch):
    """Regression: the sweep used to send the lossy `owner_repo` label to
    GitHub, got a 404, treated it as an orphan and deleted running VMs. The
    repo must reach the jobs API exactly as GitHub knows it — mixed case and
    dots included (neither survives a GCE label)."""
    list_mock = AsyncMock(
        return_value=[_vm(age_hours=3, repo_full_name="RumanKazi/Foo.js")]
    )
    job_status_mock = AsyncMock(
        return_value=JobStatusResponse(status="in_progress", conclusion=None)
    )
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", AsyncMock())
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", AsyncMock(return_value="tok")
    )

    asyncio.run(run_sweep())

    job_status_mock.assert_awaited_once_with(
        installation_token="tok", repo_full_name="RumanKazi/Foo.js", job_id="12"
    )


def test_run_sweep_skips_job_check_when_repo_unknown(monkeypatch, caplog):
    """No exact repo → no GitHub call (a 404 would read as 'orphan'). The VM is
    left alone until the hard limit."""
    list_mock = AsyncMock(return_value=[_vm(age_hours=3, repo_full_name=None)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr("runnerforge.sweep.get_installation_token", token_mock)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(run_sweep())

    assert result.skipped == 1
    assert result.deleted == 0
    delete_mock.assert_not_awaited()
    job_status_mock.assert_not_awaited()
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


def test_run_sweep_deletes_vm_with_job_not_found(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=3), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    # GitHub returned 404 → get_job_status returns None
    # Without this explicit assignment, AsyncMock returns a MagicMock instance,
    # which is NOT None — the `is None` branch wouldn't be taken.
    job_status_mock.return_value = None
    installation_token_mock.return_value = "installation_token"
    result = asyncio.run(run_sweep())
    assert result.deleted == 1
    assert result.skipped == 1
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-3h", reason="sweep"
    )
    job_status_mock.assert_awaited_once()
    installation_token_mock.assert_awaited_once_with(100)  # int, not str


def test_run_sweep_deletes_vm_past_hard_limit(monkeypatch):
    list_mock = AsyncMock(return_value=[_vm(age_hours=7), _vm(age_hours=0)])
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)

    job_status_mock.return_value = JobStatusResponse(
        status="completed", conclusion="success"
    )
    result = asyncio.run(run_sweep())
    assert result.deleted == 1
    assert result.skipped == 1
    assert result.checked == 2
    assert result.errors == []
    delete_mock.assert_awaited_once_with(
        instance_name="runnerforge-vm-7h", reason="sweep"
    )
    # Hard limit short-circuits the GitHub round-trip
    job_status_mock.assert_not_awaited()


def test_run_sweep_caches_installation_token_per_installation(monkeypatch):
    # Two old VMs from same installation_id — token should be fetched ONCE, used for both
    list_mock = AsyncMock(
        return_value=[
            _vm(age_hours=3, installation_id="200", name_suffix="a"),
            _vm(age_hours=3, installation_id="200", name_suffix="b"),
        ]
    )
    delete_mock = AsyncMock()
    job_status_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    job_status_mock.return_value = JobStatusResponse(
        status="completed", conclusion="success"
    )
    installation_token_mock.return_value = "installation_token"

    result = asyncio.run(run_sweep())
    assert result.deleted == 2
    assert result.errors == []
    # Cache works: one token fetch despite two VMs sharing installation_id
    installation_token_mock.assert_awaited_once_with(200)
    # But per-VM job status is still checked
    assert job_status_mock.await_count == 2


def test_run_sweep_continues_when_delete_raises(monkeypatch):
    # First VM delete raises; second succeeds. Whole sweep continues, error recorded.
    list_mock = AsyncMock(
        return_value=[
            _vm(age_hours=3, name_suffix="a"),
            _vm(age_hours=3, name_suffix="b"),
        ]
    )
    delete_mock = AsyncMock(side_effect=[Exception("simulated GCE error"), None])
    job_status_mock = AsyncMock()
    installation_token_mock = AsyncMock()
    monkeypatch.setattr("runnerforge.sweep.list_runnerforge_vms", list_mock)
    monkeypatch.setattr("runnerforge.sweep.delete_vm", delete_mock)
    monkeypatch.setattr("runnerforge.sweep.get_job_status", job_status_mock)
    monkeypatch.setattr(
        "runnerforge.sweep.get_installation_token", installation_token_mock
    )

    job_status_mock.return_value = JobStatusResponse(
        status="completed", conclusion="success"
    )
    installation_token_mock.return_value = "installation_token"

    result = asyncio.run(run_sweep())
    assert result.deleted == 1  # second VM succeeded
    assert result.checked == 2
    assert len(result.errors) == 1
    assert "runnerforge-vm-3h-a" in result.errors[0]
    assert delete_mock.await_count == 2  # both attempted (per-VM exception isolated)
