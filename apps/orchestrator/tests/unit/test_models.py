import json
import logging

import pytest
from pydantic import ValidationError
from runnerforge.models import WorkflowJobEvent
from runnerforge.validation import parse_github_response


def test_workflow_job_event_parses_queued_payload(fixtures_dir):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    event = WorkflowJobEvent.model_validate(payload)

    assert event.action == "queued"
    assert event.workflow_job.id == 77678867086
    assert event.repository.full_name == "rumankazi/runnerforge"
    assert "self-hosted" in event.workflow_job.labels


def test_workflow_job_event_raises_error_on_missing_action(fixtures_dir):
    payload = json.loads((fixtures_dir / "queued_job_payload.json").read_text())
    payload.pop("action")
    with pytest.raises(ValidationError):
        WorkflowJobEvent.model_validate(payload)


def test_parse_github_response_logs_and_raises_on_missing_field(caplog):
    bad_payload = {
        # missing "action"
        "workflow_job": {
            "id": 1,
            "labels": [],
            "run_id": 1,
            "run_attempt": 1,
            "workflow_name": "x",
        },
        "repository": {
            "full_name": "x/y",
            "owner": {"id": 1, "login": "x", "type": "User"},
        },
        "installation": {"id": 1},
        "sender": {"id": 1, "login": "x", "type": "User"},
    }

    logger = logging.getLogger("test")

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as excinfo:
        parse_github_response(WorkflowJobEvent, bad_payload, "test hint", logger)

    # original ValidationError preserved via `raise ... from e`
    assert isinstance(excinfo.value.__cause__, ValidationError)

    # at least one logged ERROR mentions the missing field
    assert any(
        "- action: Field required" in record.message for record in caplog.records
    )
    assert all(record.levelno == logging.ERROR for record in caplog.records)
