"""`runnerforge-admin`: the owner's command for the tenant registry (ADR-019).

    uv run runnerforge-admin list [--state pending]
    uv run runnerforge-admin approve <id> [--unlimited]
    uv run runnerforge-admin suspend <id>
    uv run runnerforge-admin set-limits <id> [--max-concurrent-vms N] ...

It runs on the owner's machine with their own gcloud credentials
(`gcloud auth application-default login`). It shares the registry's schema
(`registry.py`) but not its fail-open writes: every failure is reported and
exits non-zero. It never imports `runnerforge.config`, which needs the
service's secrets.

It writes `state`, `approved_at`, `limits`, `history` and `updated_at`. The
service creates rows as `pending` and then writes only GitHub's view of the
installation, plus `updated_at`. Each write is conditional on the row being
unchanged since it was read and shown, so a concurrent change fails the write
instead of being overwritten.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import FailedPrecondition, NotFound
from google.cloud import firestore

from runnerforge.machine_policy import SIZE_TABLE
from runnerforge.registry import (
    BETA_DEFAULTS,
    INSTALLATIONS_COLLECTION,
    UNLIMITED,
    Limits,
    entry_from_doc,
)

DEFAULT_PROJECT = "runnerforge"
DEFAULT_DATABASE = "runnerforge"
CALL_TIMEOUT_SECONDS = 10.0
# Decisions kept on each row, newest last. The array expires with the row; a
# subcollection wouldn't, because TTL doesn't delete documents under a row.
HISTORY_LIMIT = 20


class AdminError(Exception):
    """A refused or failed command. The message is shown and it exits 1."""


def make_client(project: str, database: str) -> firestore.Client:
    # User credentials need a quota project to call Firestore; the registry's
    # own project is the one to bill.
    return firestore.Client(
        project=project,
        database=database,
        client_options={"quota_project_id": project},
    )


def describe_limits(raw: Any) -> str:
    if raw is None:
        return "none"
    try:
        limits = Limits.from_dict(raw)
    except ValueError:
        return "invalid"
    if limits.unlimited:
        return "unlimited"
    return (
        f"{limits.max_concurrent_vms} VMs, up to {limits.max_size}, "
        f"{'spot only' if limits.spot_only else 'spot or standard'}, "
        f"{limits.max_runtime_minutes} min, "
        f"{'literal types' if limits.literal_machine_types else 'no literal types'}"
    )


def _read(client: firestore.Client, installation_id: int):
    snapshot = (
        client.collection(INSTALLATIONS_COLLECTION)
        .document(str(installation_id))
        .get(timeout=CALL_TIMEOUT_SECONDS)
    )
    if not snapshot.exists:
        raise AdminError(
            f"Installation {installation_id} has no registry row. Rows are "
            "created when the App is installed, or by the hourly backfill."
        )
    return snapshot


def _refuse_deleted(installation_id: int, github_status: str) -> None:
    if github_status == "deleted":
        raise AdminError(
            f"Installation {installation_id} was uninstalled. A reinstall gets "
            "a new installation ID, which registers as pending."
        )


def _confirm() -> bool:
    try:
        answer = input("Write this change? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _apply(
    client: firestore.Client,
    snapshot,
    action: str,
    *,
    state: str,
    limits: Limits | None,
    assume_yes: bool,
) -> int:
    """Write `state`, and `limits` unless None (keep them), if either
    changes. Shows the change and asks first unless `assume_yes`."""
    data = snapshot.to_dict() or {}
    entry = entry_from_doc(int(snapshot.id), data)
    old_limits = data.get("limits")
    new_limits = old_limits if limits is None else limits.to_dict()
    who = (
        f"Installation {entry.installation_id}: {entry.account_login or '?'} "
        f"({entry.account_type or '?'}), GitHub status {entry.github_status}"
    )
    if state == entry.state and new_limits == old_limits:
        print(who)
        print(
            f"Already {state} with limits {describe_limits(old_limits)}. "
            "Nothing to change."
        )
        return 0
    print(who)
    print(f"  state   {entry.state} -> {state}")
    print(f"  limits  {describe_limits(old_limits)} -> {describe_limits(new_limits)}")
    if not assume_yes and not _confirm():
        print("Aborted; nothing written.")
        return 1

    now = datetime.now(UTC)
    history = list(data.get("history") or [])[-(HISTORY_LIMIT - 1) :]
    history.append({"at": now, "action": action, "state": state, "limits": new_limits})
    fields: dict[str, Any] = {"state": state, "updated_at": now, "history": history}
    if new_limits is not None:
        fields["limits"] = new_limits
    if state == "approved" and entry.approved_at is None:
        # Set on first approval and never cleared: it gates job records.
        fields["approved_at"] = now
    try:
        snapshot.reference.update(
            fields,
            option=client.write_option(last_update_time=snapshot.update_time),
            timeout=CALL_TIMEOUT_SECONDS,
        )
    except NotFound as exc:
        raise AdminError(
            "The row was deleted after it was read; nothing was written."
        ) from exc
    except FailedPrecondition as exc:
        # The client retries a commit on UNAVAILABLE, and a retry of a commit
        # that did land fails its own precondition. Look before reporting.
        current = snapshot.reference.get(timeout=CALL_TIMEOUT_SECONDS).to_dict() or {}
        if entry_from_doc(entry.installation_id, current).state != state or (
            current.get("limits") != new_limits
        ):
            raise AdminError(
                "The row changed after it was read, so nothing was written. "
                "Run the command again."
            ) from exc
    print("Written. The orchestrator picks it up within a minute.")
    return 0


def list_installations(client: firestore.Client, state: str | None) -> int:
    snapshots = client.collection(INSTALLATIONS_COLLECTION).stream(
        timeout=CALL_TIMEOUT_SECONDS
    )
    rows = []
    for snapshot in snapshots:
        data = snapshot.to_dict() or {}
        entry = entry_from_doc(int(snapshot.id), data)
        if state is None or entry.state == state:
            rows.append((entry, data.get("limits")))
    if not rows:
        print(f"No installations{f' in state {state}' if state else ''}.")
        return 0
    line = "{:<13} {:<24} {:<13} {:<10} {:<10} {:<11} {}"
    print(
        line.format(
            "INSTALLATION", "ACCOUNT", "TYPE", "STATE", "GITHUB", "APPROVED", "LIMITS"
        )
    )
    for entry, limits in sorted(rows, key=lambda row: row[0].installation_id):
        print(
            line.format(
                entry.installation_id,
                entry.account_login or "?",
                entry.account_type or "?",
                entry.state,
                entry.github_status,
                entry.approved_at.date().isoformat() if entry.approved_at else "-",
                describe_limits(limits),
            )
        )
    return 0


def approve(
    client: firestore.Client, installation_id: int, *, unlimited: bool, assume_yes: bool
) -> int:
    snapshot = _read(client, installation_id)
    data = snapshot.to_dict() or {}
    entry = entry_from_doc(installation_id, data)
    _refuse_deleted(installation_id, entry.github_status)
    if entry.github_status == "suspended":
        print(
            "warning: GitHub has suspended this installation. It is admitted "
            "only once GitHub unsuspends it.",
            file=sys.stderr,
        )
    limits: Limits | None = None  # keep the row's limits
    if unlimited:
        limits = UNLIMITED
    elif data.get("limits") is None:
        limits = BETA_DEFAULTS
    else:
        try:
            Limits.from_dict(data["limits"])
        except ValueError as exc:
            raise AdminError(
                "This row's limits are invalid. Replace them first with "
                "`set-limits --defaults` or `--unlimited`."
            ) from exc
    return _apply(
        client,
        snapshot,
        "approve",
        state="approved",
        limits=limits,
        assume_yes=assume_yes,
    )


def suspend(client: firestore.Client, installation_id: int, *, assume_yes: bool) -> int:
    # Stops new VMs only; running ones end with their job or runtime cap.
    snapshot = _read(client, installation_id)
    return _apply(
        client,
        snapshot,
        "suspend",
        state="suspended",
        limits=None,
        assume_yes=assume_yes,
    )


def set_limits(
    client: firestore.Client,
    installation_id: int,
    *,
    changes: dict[str, Any],
    unlimited: bool,
    defaults: bool,
    assume_yes: bool,
) -> int:
    snapshot = _read(client, installation_id)
    data = snapshot.to_dict() or {}
    entry = entry_from_doc(installation_id, data)
    _refuse_deleted(installation_id, entry.github_status)
    if unlimited:
        limits = UNLIMITED
    elif defaults:
        limits = BETA_DEFAULTS
    else:
        # Limits not named stay as they are. An unlimited row, or one with
        # none, starts from the beta defaults.
        base = BETA_DEFAULTS
        if data.get("limits") is not None:
            try:
                current = Limits.from_dict(data["limits"])
            except ValueError:
                print(
                    "warning: this row's limits are invalid; starting from "
                    "the beta defaults.",
                    file=sys.stderr,
                )
            else:
                if not current.unlimited:
                    base = current
        try:
            limits = replace(base, **changes)
        except ValueError as exc:
            raise AdminError(str(exc)) from exc
    return _apply(
        client,
        snapshot,
        "set-limits",
        state=entry.state,
        limits=limits,
        assume_yes=assume_yes,
    )


_LIMIT_OPTIONS = (
    "max_concurrent_vms",
    "max_size",
    "spot_only",
    "max_runtime_minutes",
    "literal_machine_types",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runnerforge-admin",
        description="Approve, suspend and limit GitHub App installations "
        "in the RunnerForge tenant registry (ADR-019).",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="show installations")
    listing.add_argument("--state", choices=("pending", "approved", "suspended"))

    yes = argparse.ArgumentParser(add_help=False)
    yes.add_argument("--yes", action="store_true", help="don't ask before writing")

    approving = commands.add_parser(
        "approve",
        parents=[yes],
        help="approve an installation (beta defaults unless it has limits)",
    )
    approving.add_argument("installation_id", type=int)
    approving.add_argument("--unlimited", action="store_true")

    suspending = commands.add_parser(
        "suspend", parents=[yes], help="stop new VMs for an installation"
    )
    suspending.add_argument("installation_id", type=int)

    limiting = commands.add_parser(
        "set-limits",
        parents=[yes],
        help="change limits; ones not named stay as they are",
    )
    limiting.add_argument("installation_id", type=int)
    whole = limiting.add_mutually_exclusive_group()
    whole.add_argument("--unlimited", action="store_true")
    whole.add_argument("--defaults", action="store_true", help="the beta defaults")
    limiting.add_argument("--max-concurrent-vms", type=int)
    limiting.add_argument("--max-size", choices=tuple(SIZE_TABLE))
    limiting.add_argument("--spot-only", action=argparse.BooleanOptionalAction)
    limiting.add_argument("--max-runtime-minutes", type=int)
    limiting.add_argument(
        "--literal-machine-types", action=argparse.BooleanOptionalAction
    )
    return parser


def _run(client: firestore.Client, args: argparse.Namespace) -> int:
    if args.command == "list":
        return list_installations(client, args.state)
    if args.command == "approve":
        return approve(
            client,
            args.installation_id,
            unlimited=args.unlimited,
            assume_yes=args.yes,
        )
    if args.command == "suspend":
        return suspend(client, args.installation_id, assume_yes=args.yes)
    changes = {
        name: getattr(args, name)
        for name in _LIMIT_OPTIONS
        if getattr(args, name) is not None
    }
    return set_limits(
        client,
        args.installation_id,
        changes=changes,
        unlimited=args.unlimited,
        defaults=args.defaults,
        assume_yes=args.yes,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "set-limits":
        named = any(getattr(args, name) is not None for name in _LIMIT_OPTIONS)
        if named and (args.unlimited or args.defaults):
            parser.error("--unlimited and --defaults replace every limit")
        if not (named or args.unlimited or args.defaults):
            parser.error("name a limit, or give --unlimited or --defaults")
    print(
        f"Registry: projects/{args.project}/databases/{args.database}", file=sys.stderr
    )
    try:
        return _run(make_client(args.project, args.database), args)
    except AdminError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        # Credentials, permissions, network: report briefly, no traceback.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
