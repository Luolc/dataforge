import pytest

from tinyparse.header import read_header


@pytest.mark.parametrize(("text", "expected"), [
    ("a: 1\n\nbody: x", {"a": "1"}),
    ("a: 1\nb: 2", {"a": "1", "b": "2"}),
    ("", {}),
])
def test_read_header(text, expected):
    assert read_header(text) == expected
