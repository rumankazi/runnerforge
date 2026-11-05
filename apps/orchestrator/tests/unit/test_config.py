"""Where runner VMs go (ADR-020): the VM project, subnetwork and account."""

import logging

import pytest

from runnerforge.config import validate_runner_vm_config

CONTROL = "runnerforge"
POOL = "runnerforge-pool"
ZONE = "europe-west4-a"
SUBNET = "projects/runnerforge-pool/regions/europe-west4/subnetworks/pool"


@pytest.mark.parametrize(
    "sa_email",
    [
        "",
        "test-runner-vm@example.iam.gserviceaccount.com",
        # A self-host install's default compute account.
        "123-compute@developer.gserviceaccount.com",
    ],
)
def test_vms_in_the_control_project_take_any_account(sa_email):
    assert validate_runner_vm_config(CONTROL, CONTROL, ZONE, "", sa_email) == []


def test_vms_in_another_project_get_no_account():
    with pytest.raises(ValueError, match="RUNNER_VM_SA_EMAIL must be empty"):
        validate_runner_vm_config(
            CONTROL, POOL, ZONE, SUBNET, "runner@runnerforge.iam.gserviceaccount.com"
        )


@pytest.mark.parametrize(
    "subnetwork",
    [
        SUBNET,
        "regions/europe-west4/subnetworks/pool",
    ],
    ids=["with-project", "partial"],
)
def test_a_subnetwork_in_the_vm_projects_region_is_accepted(subnetwork, caplog):
    with caplog.at_level(logging.WARNING):
        assert validate_runner_vm_config(CONTROL, POOL, ZONE, subnetwork, "") == []
    assert not caplog.records


@pytest.mark.parametrize(
    ("subnetwork", "error"),
    [
        (
            "projects/runnerforge/regions/europe-west4/subnetworks/pool",
            "not the VM project runnerforge-pool",
        ),
        (
            "projects/runnerforge-pool/regions/us-central1/subnetworks/pool",
            "not europe-west4",
        ),
        ("pool", "must be"),
        ("global/networks/default", "must be"),
        ("regions/europe-west4/subnetworks/pool/extra", "must be"),
    ],
    ids=["other-project", "other-region", "bare-name", "network", "trailing"],
)
def test_a_subnetwork_elsewhere_is_refused(subnetwork, error):
    with pytest.raises(ValueError, match=error):
        validate_runner_vm_config(CONTROL, POOL, ZONE, subnetwork, "")


def test_another_project_without_a_subnetwork_warns(caplog):
    """Valid where the project has a default network; the insert fails
    loudly where it hasn't."""
    with caplog.at_level(logging.WARNING):
        warnings = validate_runner_vm_config(CONTROL, POOL, ZONE, "", "")
    [warning] = warnings
    assert warning == (
        "RUNNER_VM_SUBNETWORK is empty, so VMs in runnerforge-pool use its "
        "default network"
    )
    assert [r.message for r in caplog.records] == warnings
