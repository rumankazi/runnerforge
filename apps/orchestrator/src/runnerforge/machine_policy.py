import re
from dataclasses import dataclass
from typing import Literal

SIZE_TABLE = {
    "small": "n2-standard-2",
    "medium": "n2-standard-4",
    "large": "n2-standard-8",
    "xlarge": "n2-standard-16",
}
DEFAULT_MACHINE_TYPE = "n2-standard-4"
LITERAL_RE = re.compile(r"^[a-z][a-z0-9]+-[a-z]+(?:-\d+)?$")


@dataclass(frozen=True, slots=True)
class MachinePolicy:
    machine_type: str
    spot: bool


@dataclass
class MachinePolicyError(Exception):
    reason: Literal["unknown_token", "conflicting_machines"]
    offending_tokens: list[str]

    def __post_init__(self):
        super().__init__(f"{self.reason}: {self.offending_tokens}")


def resolve_labels(labels: list[str]) -> MachinePolicy:
    """Processes the list of labels to allocate proper machine policy for VM instance creation"""
    chosen: str | None = None
    spot = False
    unknown: list[str] = []
    for t in labels:
        if t in ("runnerforge", "spot"):
            if t == "spot":
                spot = True
            continue

        if t in SIZE_TABLE:
            gce_type = SIZE_TABLE[t]
        elif LITERAL_RE.match(t):
            gce_type = t
        else:
            gce_type = None

        if gce_type is None:
            unknown.append(t)
            continue
        if chosen is not None:
            raise MachinePolicyError(
                "conflicting_machines",
                [
                    chosen,
                    gce_type,
                ],
            )
        chosen = gce_type
    if unknown:
        raise MachinePolicyError("unknown_token", unknown)
    return MachinePolicy(machine_type=chosen or DEFAULT_MACHINE_TYPE, spot=spot)
