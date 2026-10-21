import pytest

from tinyparse.header import read_header


@pytest.mark.parametrize(("text", "expected"), [
    ("a : 1", {"a": "1"}),
])
def test_read_header(text, expected):
    assert read_header(text) == expected
