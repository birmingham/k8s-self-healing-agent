import pytest

from healer.quantity import format_mi, parse_bytes
from healer.redact import redact, truncate


@pytest.mark.parametrize("q,expected", [
    ("128Mi", 128 * 2**20), ("1Gi", 2**30), ("200M", 200 * 10**6), ("512Ki", 512 * 1024),
    ("1.5Gi", int(1.5 * 2**30)), ("1e9", 10**9), ("1048576", 2**20),
])
def test_parse_bytes(q, expected):
    assert parse_bytes(q) == expected


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_bytes("lots")


def test_format_rounds_up():
    assert format_mi(200 * 10**6) == "191Mi"
    assert format_mi(256 * 2**20) == "256Mi"


@pytest.mark.parametrize("raw", [
    "DATABASE_URL=postgres://app:hunter2@db:5432/app",
    "password=hunter2",
    "Authorization: Bearer abcdefghijklmnop",
    "key sk-ant-api03-abcdefghijklmnop",
    "AKIAABCDEFGHIJKLMNOP",
])
def test_redact_removes_secrets(raw):
    out = redact(raw)
    assert "hunter2" not in out and "abcdefghijklmnop" not in out and "AKIAABCDEFGHIJKLMNOP" not in out


def test_truncate_keeps_tail():
    assert truncate("a" * 10 + "CRASH", 5).endswith("CRASH")
