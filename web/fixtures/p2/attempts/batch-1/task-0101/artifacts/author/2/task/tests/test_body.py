from tinyparse.body import split


def test_split_keeps_the_body():
    assert split("a: 1\n\nhello") == ("a: 1", "hello")
