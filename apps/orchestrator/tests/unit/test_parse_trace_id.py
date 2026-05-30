import pytest
from runnerforge.main import parse_trace_id


@pytest.mark.parametrize(
    "header,expected",
    [
        (
            "105445aa7843bc8bf206b12000100000/1243;o=1",
            "105445aa7843bc8bf206b12000100000",
        ),
        (None, None),
        ("", None),
        ("105445aa7843bc8bf206b12000100000", "105445aa7843bc8bf206b12000100000"),
        ("/1243;o=1", None),
    ],
)
def test_parse_trace_id(header, expected):
    assert parse_trace_id(header) == expected
