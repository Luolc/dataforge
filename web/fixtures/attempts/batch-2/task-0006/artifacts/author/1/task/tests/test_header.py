from tinyparse import parse_header


def test_keeps_every_field():
    assert parse_header("a=1; b=2") == {"a": "1", "b": "2"}


def test_trailing_semicolon():
    assert parse_header("a=1;") == {"a": "1"}
