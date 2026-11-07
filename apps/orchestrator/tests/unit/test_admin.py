import os
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import FailedPrecondition, NotFound
from google.auth.exceptions import DefaultCredentialsError

from runnerforge import admin
from runnerforge.registry import BETA_DEFAULTS, INSTALLATIONS_COLLECTION

APPROVED_AT = datetime(2026, 10, 30, 17, 0, tzinfo=UTC)
LIMITED = {
    "max_concurrent_vms": 4,
    "max_size": "large",
    "spot_only": False,
    "max_runtime_minutes": 120,
    "literal_machine_types": True,
}


def _snap(doc_id: int, data: dict | None, exists: bool = True) -> MagicMock:
    snap = MagicMock()
    snap.id = str(doc_id)
    snap.exists = exists
    snap.to_dict.return_value = data
    snap.update_time = "read-time"
    return snap


def _row(**fields) -> dict:
    return {
        "installation_id": 7,
        "account_login": "octo",
        "account_type": "Organization",
        "state": "pending",
        "github_status": "active",
        **fields,
    }


@pytest.fixture
def client(monkeypatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr(admin, "make_client", lambda project, database: client)
    return client


@pytest.fixture
def answer(monkeypatch):
    """What the owner types at the confirmation question."""
    replies = {"value": "y"}

    def ask(prompt: str) -> str:
        if replies["value"] is EOFError:
            raise EOFError
        return replies["value"]

    monkeypatch.setattr("builtins.input", ask)
    return replies


def _serve(client: MagicMock, snap: MagicMock) -> MagicMock:
    client.collection.return_value.document.return_value.get.return_value = snap
    return snap


def _written(snap: MagicMock) -> dict:
    snap.reference.update.assert_called_once()
    return snap.reference.update.call_args.args[0]


# --- client and wiring ---


def test_make_client_bills_the_registry_project(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        admin.firestore, "Client", lambda **kwargs: seen.update(kwargs) or "client"
    )
    assert admin.make_client("proj", "db") == "client"
    assert seen == {
        "project": "proj",
        "database": "db",
        "client_options": {"quota_project_id": "proj"},
    }


def test_console_script_is_registered():
    scripts = entry_points(group="console_scripts", name="runnerforge-admin")
    assert [ep.value for ep in scripts] == ["runnerforge.admin:main"]


def test_import_does_not_load_service_config():
    # The service's config needs its secrets; the owner's machine has none.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GITHUB_", "GCP_", "RUNNER_", "EXPECTED_"))
    }
    src = Path(admin.__file__).parents[1]
    check = (
        "import sys, runnerforge.admin; "
        "assert 'runnerforge.config' not in sys.modules, 'config loaded'"
    )
    result = subprocess.run(
        [sys.executable, "-c", check],
        env={**env, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_prints_the_registry_it_uses(client, capsys):
    client.collection.return_value.stream.return_value = []
    assert admin.main(["--project", "p", "--database", "d", "list"]) == 0
    assert "Registry: projects/p/databases/d" in capsys.readouterr().err
    client.collection.assert_called_with(INSTALLATIONS_COLLECTION)


def test_reports_other_failures_without_traceback(monkeypatch, capsys):
    def no_credentials(project, database):
        raise DefaultCredentialsError("run gcloud auth application-default login")

    monkeypatch.setattr(admin, "make_client", no_credentials)
    assert admin.main(["list"]) == 1
    assert "error: DefaultCredentialsError: run gcloud" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["set-limits", "7"],
        ["set-limits", "7", "--unlimited", "--max-size", "small"],
        ["set-limits", "7", "--defaults", "--no-spot-only"],
        ["set-limits", "7", "--unlimited", "--defaults"],
        ["set-limits", "7", "--max-size", "huge"],
        ["approve", "not-a-number"],
        [],
    ],
)
def test_rejects_bad_arguments(client, argv):
    with pytest.raises(SystemExit) as exc:
        admin.main(argv)
    assert exc.value.code == 2
    client.collection.assert_not_called()


# --- list ---


def test_list_with_no_rows(client, capsys):
    client.collection.return_value.stream.return_value = []
    assert admin.main(["list"]) == 0
    assert "No installations." in capsys.readouterr().out


def test_list_with_no_rows_in_state(client, capsys):
    client.collection.return_value.stream.return_value = [_snap(7, _row())]
    assert admin.main(["list", "--state", "approved"]) == 0
    assert "No installations in state approved." in capsys.readouterr().out


def test_list_shows_rows_sorted_with_limits(client, capsys):
    client.collection.return_value.stream.return_value = [
        _snap(30, _row(state="approved", approved_at=APPROVED_AT, limits=LIMITED)),
        _snap(10, {}),
        _snap(20, _row(limits={"unlimited": True})),
        _snap(40, _row(limits=BETA_DEFAULTS.to_dict())),
        _snap(50, _row(limits={"max_size": "small"})),
    ]
    assert admin.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "INSTALLATION",
        "ACCOUNT",
        "TYPE",
        "STATE",
        "GITHUB",
        "APPROVED",
        "LIMITS",
    ]
    assert [line.split()[0] for line in lines[1:]] == ["10", "20", "30", "40", "50"]
    assert lines[1].split() == ["10", "?", "?", "pending", "active", "-", "none"]
    assert lines[2].endswith("unlimited")
    assert "2026-10-30" in lines[3]
    assert lines[3].endswith(
        "4 VMs, up to large, spot or standard, 120 min, literal types"
    )
    assert lines[4].endswith("2 VMs, up to medium, spot only, 60 min, no literal types")
    assert lines[5].endswith("invalid")


def test_list_filters_by_state(client, capsys):
    client.collection.return_value.stream.return_value = [
        _snap(1, _row(state="approved")),
        _snap(2, _row()),
    ]
    assert admin.main(["list", "--state", "pending"]) == 0
    rows = capsys.readouterr().out.splitlines()[1:]
    assert [row.split()[0] for row in rows] == ["2"]


# --- approve ---


def test_approve_without_a_row(client, capsys):
    _serve(client, _snap(7, None, exists=False))
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "has no registry row" in capsys.readouterr().err


def test_approve_refuses_an_uninstalled_installation(client, capsys):
    snap = _serve(client, _snap(7, _row(github_status="deleted")))
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "was uninstalled" in capsys.readouterr().err
    snap.reference.update.assert_not_called()


def test_approve_writes_beta_defaults_and_first_approval(client, answer, capsys):
    history = [{"action": "old"}]
    snap = _serve(client, _snap(7, _row(history=history)))
    client.write_option.return_value = "unchanged-since-read"

    assert admin.main(["approve", "7"]) == 0

    out = capsys.readouterr().out
    assert "Installation 7: octo (Organization), GitHub status active" in out
    assert "state   pending -> approved" in out
    assert "limits  none -> 2 VMs, up to medium" in out
    assert "Written." in out
    fields = _written(snap)
    assert fields["state"] == "approved"
    assert fields["limits"] == BETA_DEFAULTS.to_dict()
    assert fields["approved_at"] == fields["updated_at"]
    assert fields["history"][0] == {"action": "old"}
    assert fields["history"][1] == {
        "at": fields["updated_at"],
        "action": "approve",
        "state": "approved",
        "limits": BETA_DEFAULTS.to_dict(),
    }
    client.write_option.assert_called_once_with(last_update_time="read-time")
    assert snap.reference.update.call_args.kwargs["option"] == "unchanged-since-read"
    assert history == [{"action": "old"}]  # the read row isn't mutated


def test_approve_unlimited(client, capsys):
    snap = _serve(client, _snap(7, _row(limits=LIMITED)))
    assert admin.main(["approve", "7", "--unlimited", "--yes"]) == 0
    assert _written(snap)["limits"] == {"unlimited": True}


def test_reapprove_keeps_limits_and_first_approval(client, capsys):
    snap = _serve(
        client,
        _snap(7, _row(state="suspended", approved_at=APPROVED_AT, limits=LIMITED)),
    )
    assert admin.main(["approve", "7", "--yes"]) == 0
    fields = _written(snap)
    assert fields["limits"] == LIMITED
    assert "approved_at" not in fields


def test_approve_warns_when_github_suspended_it(client, capsys):
    snap = _serve(client, _snap(7, _row(github_status="suspended")))
    assert admin.main(["approve", "7", "--yes"]) == 0
    assert "GitHub has suspended this installation" in capsys.readouterr().err
    assert _written(snap)["state"] == "approved"


def test_approve_refuses_invalid_limits(client, capsys):
    snap = _serve(client, _snap(7, _row(limits={"max_size": "small"})))
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "limits are invalid" in capsys.readouterr().err
    snap.reference.update.assert_not_called()


def test_approve_when_nothing_changes(client, capsys):
    snap = _serve(
        client,
        _snap(7, _row(state="approved", approved_at=APPROVED_AT, limits=LIMITED)),
    )
    assert admin.main(["approve", "7", "--yes"]) == 0
    assert "Nothing to change." in capsys.readouterr().out
    snap.reference.update.assert_not_called()


@pytest.mark.parametrize("reply", ["n", "", "maybe", EOFError])
def test_approve_aborts_unless_confirmed(client, answer, capsys, reply):
    answer["value"] = reply
    snap = _serve(client, _snap(7, _row()))
    assert admin.main(["approve", "7"]) == 1
    assert "Aborted; nothing written." in capsys.readouterr().out
    snap.reference.update.assert_not_called()


@pytest.mark.parametrize("reply", ["y", "YES", " yes "])
def test_approve_accepts_yes(client, answer, capsys, reply):
    answer["value"] = reply
    snap = _serve(client, _snap(7, _row()))
    assert admin.main(["approve", "7"]) == 0
    snap.reference.update.assert_called_once()


def test_write_fails_when_the_row_changed(client, capsys):
    snap = _serve(client, _snap(7, _row()))
    snap.reference.update.side_effect = FailedPrecondition("stale")
    # A webhook wrote GitHub's status in between; the decision didn't land.
    snap.reference.get.return_value = _snap(7, _row(github_status="suspended"))
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "The row changed after it was read" in capsys.readouterr().err


def test_write_fails_when_only_the_state_landed_elsewhere(client, capsys):
    snap = _serve(client, _snap(7, _row()))
    snap.reference.update.side_effect = FailedPrecondition("stale")
    snap.reference.get.return_value = _snap(7, _row(state="approved"))
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "nothing was written" in capsys.readouterr().err


def test_retried_write_that_landed_is_reported_written(client, capsys):
    # The first commit landed but its reply was lost; the client's retry then
    # fails the precondition against our own write.
    snap = _serve(client, _snap(7, _row()))
    snap.reference.update.side_effect = FailedPrecondition("stale")
    snap.reference.get.return_value = _snap(
        7, _row(state="approved", limits=BETA_DEFAULTS.to_dict())
    )
    assert admin.main(["approve", "7", "--yes"]) == 0
    assert "Written." in capsys.readouterr().out


def test_write_fails_when_the_row_was_deleted(client, capsys):
    snap = _serve(client, _snap(7, _row()))
    snap.reference.update.side_effect = NotFound("gone")
    assert admin.main(["approve", "7", "--yes"]) == 1
    assert "deleted after it was read" in capsys.readouterr().err


def test_history_keeps_the_newest_entries(client, capsys):
    history = [{"n": i} for i in range(admin.HISTORY_LIMIT)]
    snap = _serve(client, _snap(7, _row(history=history)))
    assert admin.main(["approve", "7", "--yes"]) == 0
    written = _written(snap)["history"]
    assert len(written) == admin.HISTORY_LIMIT
    assert written[0] == {"n": 1}
    assert written[-1]["action"] == "approve"


# --- suspend ---


def test_suspend_keeps_limits(client, capsys):
    snap = _serve(
        client,
        _snap(7, _row(state="approved", approved_at=APPROVED_AT, limits=LIMITED)),
    )
    assert admin.main(["suspend", "7", "--yes"]) == 0
    fields = _written(snap)
    assert fields["state"] == "suspended"
    assert fields["limits"] == LIMITED
    assert "approved_at" not in fields


def test_suspend_a_row_without_limits(client, capsys):
    snap = _serve(client, _snap(7, _row()))
    assert admin.main(["suspend", "7", "--yes"]) == 0
    fields = _written(snap)
    assert fields["state"] == "suspended"
    assert "limits" not in fields
    assert "approved_at" not in fields
    assert fields["history"][-1]["limits"] is None


def test_suspend_without_a_row(client, capsys):
    _serve(client, _snap(7, None, exists=False))
    assert admin.main(["suspend", "7", "--yes"]) == 1


# --- set-limits ---


def test_set_limits_merges_into_current(client, capsys):
    snap = _serve(client, _snap(7, _row(state="approved", limits=LIMITED)))
    assert (
        admin.main(
            ["set-limits", "7", "--max-concurrent-vms", "6", "--spot-only", "--yes"]
        )
        == 0
    )
    fields = _written(snap)
    assert fields["limits"] == {**LIMITED, "max_concurrent_vms": 6, "spot_only": True}
    assert fields["state"] == "approved"
    assert fields["history"][-1]["action"] == "set-limits"


@pytest.mark.parametrize("current", [None, {"unlimited": True}])
def test_set_limits_starts_from_beta_defaults(client, capsys, current):
    snap = _serve(client, _snap(7, _row(limits=current)))
    assert (
        admin.main(
            [
                "set-limits",
                "7",
                "--max-size",
                "small",
                "--max-runtime-minutes",
                "30",
                "--literal-machine-types",
                "--yes",
            ]
        )
        == 0
    )
    assert _written(snap)["limits"] == {
        **BETA_DEFAULTS.to_dict(),
        "max_size": "small",
        "max_runtime_minutes": 30,
        "literal_machine_types": True,
    }


def test_set_limits_replaces_invalid_limits(client, capsys):
    snap = _serve(client, _snap(7, _row(limits={"max_size": "small"})))
    assert admin.main(["set-limits", "7", "--no-spot-only", "--yes"]) == 0
    assert "starting from the beta defaults" in capsys.readouterr().err
    assert _written(snap)["limits"] == {**BETA_DEFAULTS.to_dict(), "spot_only": False}


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("--unlimited", {"unlimited": True}), ("--defaults", BETA_DEFAULTS.to_dict())],
)
def test_set_limits_replaces_all(client, capsys, flag, expected):
    snap = _serve(client, _snap(7, _row(limits=LIMITED)))
    assert admin.main(["set-limits", "7", flag, "--yes"]) == 0
    fields = _written(snap)
    assert fields["limits"] == expected
    # Limits never approve: a pending row stays pending.
    assert fields["state"] == "pending"
    assert "approved_at" not in fields


@pytest.mark.parametrize(
    "argv",
    [
        ["--max-concurrent-vms", "0"],
        ["--max-concurrent-vms", "31"],
        ["--max-runtime-minutes", "361"],
    ],
)
def test_set_limits_rejects_out_of_range(client, capsys, argv):
    snap = _serve(client, _snap(7, _row()))
    assert admin.main(["set-limits", "7", *argv, "--yes"]) == 1
    assert "must be 1 to" in capsys.readouterr().err
    snap.reference.update.assert_not_called()


def test_set_limits_refuses_an_uninstalled_installation(client, capsys):
    snap = _serve(client, _snap(7, _row(github_status="deleted")))
    assert admin.main(["set-limits", "7", "--defaults", "--yes"]) == 1
    snap.reference.update.assert_not_called()


def test_set_limits_without_a_row(client, capsys):
    _serve(client, _snap(7, None, exists=False))
    assert admin.main(["set-limits", "7", "--defaults", "--yes"]) == 1


# --- limits written by a newer version, and the pool cap ---

NEWER = {**LIMITED, "max_vcpus": 8}


def test_list_names_fields_it_does_not_know(client, capsys):
    client.collection.return_value.stream.return_value = [
        _snap(7, _row(limits=NEWER)),
        _snap(8, _row(limits={"unlimited": True, "max_vcpus": 8})),
    ]
    assert admin.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].endswith("literal types (and max_vcpus, unknown here)")
    assert lines[2].endswith("invalid (and max_vcpus, unknown here)")


@pytest.mark.parametrize(
    "argv", [["approve", "7"], ["set-limits", "7", "--max-size", "small"]]
)
def test_keeping_limits_refuses_fields_it_does_not_know(client, capsys, argv):
    snap = _serve(client, _snap(7, _row(limits=NEWER)))
    assert admin.main([*argv, "--yes"]) == 1
    err = capsys.readouterr().err
    assert "limit fields this version doesn't know: max_vcpus" in err
    assert "--defaults" in err
    snap.reference.update.assert_not_called()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["approve", "7", "--unlimited"], {"unlimited": True}),
        (["set-limits", "7", "--defaults"], BETA_DEFAULTS.to_dict()),
        (["set-limits", "7", "--unlimited"], {"unlimited": True}),
    ],
)
def test_replacing_every_limit_drops_fields_it_does_not_know(
    client, capsys, argv, expected
):
    snap = _serve(client, _snap(7, _row(limits=NEWER)))
    assert admin.main([*argv, "--yes"]) == 0
    assert "dropping limit fields max_vcpus" in capsys.readouterr().err
    assert _written(snap)["limits"] == expected


def test_suspend_keeps_fields_it_does_not_know(client, capsys):
    snap = _serve(client, _snap(7, _row(state="approved", limits=NEWER)))
    assert admin.main(["suspend", "7", "--yes"]) == 0
    assert _written(snap)["limits"] == NEWER  # written back as read


@pytest.mark.parametrize(("cap", "warns"), [([], True), (["--pool-cap", "12"], False)])
def test_set_limits_warns_above_the_pool_cap(client, capsys, cap, warns):
    snap = _serve(client, _snap(7, _row(limits=LIMITED)))
    argv = ["set-limits", "7", "--max-concurrent-vms", "12", *cap, "--yes"]
    assert admin.main(argv) == 0
    err = capsys.readouterr().err
    # 12 of 12 is the boundary: a limit equal to the cap doesn't warn.
    assert ("a limit of 12 VMs" in err) is warns
    assert ("more than the pool cap of 10 VMs" in err) is warns
    assert _written(snap)["limits"]["max_concurrent_vms"] == 12
    # A pending row's limit isn't added to the approved ones.
    client.collection.return_value.stream.assert_not_called()


@pytest.mark.parametrize(("cap", "warns"), [("10", False), ("9", True)])
def test_approve_warns_when_approved_limits_add_up_past_the_cap(
    client, capsys, cap, warns
):
    _serve(client, _snap(7, _row()))
    client.collection.return_value.stream.return_value = [
        _snap(7, _row(limits={**LIMITED, "max_concurrent_vms": 20})),  # this row
        _snap(8, _row(state="approved", limits=LIMITED)),  # 4
        _snap(9, _row(state="approved", limits={"unlimited": True})),  # not counted
        _snap(10, _row(limits=LIMITED)),  # pending
        _snap(11, _row(state="approved", github_status="deleted", limits=LIMITED)),
        _snap(12, _row(state="approved")),  # no limits: the beta defaults, 2
        _snap(13, _row(state="approved", limits={"max_size": "small"})),  # 2
    ]
    assert admin.main(["approve", "7", "--pool-cap", cap, "--yes"]) == 0
    err = capsys.readouterr().err
    # 2 (this row's beta defaults) + 4 + 2 + 2
    expected = "approved limits add up to 10 VMs, more than the pool cap of 9 VMs"
    assert (expected in err) is warns
    assert ("add up to" in err) is warns


def test_approve_unlimited_is_not_counted(client, capsys):
    _serve(client, _snap(7, _row()))
    client.collection.return_value.stream.return_value = [
        _snap(8, _row(state="approved", limits={**LIMITED, "max_concurrent_vms": 10}))
    ]
    assert admin.main(["approve", "7", "--unlimited", "--yes"]) == 0
    assert "warning" not in capsys.readouterr().err


def test_pool_cap_must_be_positive(client, capsys):
    with pytest.raises(SystemExit):
        admin.main(["approve", "7", "--pool-cap", "0"])
    assert "--pool-cap must be at least 1" in capsys.readouterr().err


@pytest.mark.parametrize(("vms", "warns"), [("6", False), ("7", True)])
def test_set_limits_counts_its_new_limit_in_the_approved_sum(
    client, capsys, vms, warns
):
    _serve(client, _snap(7, _row(state="approved", limits=LIMITED)))
    client.collection.return_value.stream.return_value = [
        _snap(7, _row(state="approved", limits=LIMITED)),  # its stored 4: not counted
        _snap(8, _row(state="approved", limits=LIMITED)),  # 4
    ]
    argv = ["set-limits", "7", "--max-concurrent-vms", vms, "--yes"]
    assert admin.main(argv) == 0
    assert ("approved limits add up to 11 VMs" in capsys.readouterr().err) is warns


def test_a_failed_listing_does_not_stop_the_write(client, capsys):
    snap = _serve(client, _snap(7, _row()))
    client.collection.return_value.stream.side_effect = TimeoutError("slow")
    assert admin.main(["approve", "7", "--yes"]) == 0
    assert (
        "couldn't add up the approved limits (TimeoutError)" in capsys.readouterr().err
    )
    assert _written(snap)["state"] == "approved"
