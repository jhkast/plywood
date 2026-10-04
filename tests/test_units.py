import pytest

from plywood.core.units import Formatter, format_inches, parse_length

IN = 25.4


@pytest.mark.parametrize(
    ("text", "inches"),
    [
        ("23", 23),
        ("23.625", 23.625),
        ("23-5/8", 23.625),
        ("23 5/8", 23.625),
        ('23-5/8"', 23.625),
        ("5/8", 0.625),
        ("23/32", 23 / 32),
        ("8'", 96),
        ("8ft", 96),
        ("8' 6-1/2\"", 102.5),
        ("1in", 1),
    ],
)
def test_parse_inches(text, inches):
    assert parse_length(text, "in") == pytest.approx(inches * IN)


@pytest.mark.parametrize(("text", "mm"), [("600", 600), ("600mm", 600), ("60 cm", 600), ("1.2m", 1200), ("18.5", 18.5)])
def test_parse_mm(text, mm):
    assert parse_length(text, "mm") == pytest.approx(mm)


def test_explicit_unit_beats_default():
    assert parse_length("1in", "mm") == pytest.approx(IN)
    assert parse_length("25.4mm", "in") == pytest.approx(IN)


@pytest.mark.parametrize("bad", ["", "abc", "1/0", "5//8"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_length(bad)


@pytest.mark.parametrize(("inches", "text"), [(23.625, '23-5/8"'), (0.5, '1/2"'), (96, '96"'), (0.0, '0"'), (3.03, '3"')])
def test_format_inches(inches, text):
    assert format_inches(inches, 16) == text


def test_round_trip_sixteenths():
    for ticks in range(0, 16 * 50):
        mm = ticks / 16 * IN
        assert parse_length(format_inches(mm / IN, 16), "in") == pytest.approx(mm)


def test_thickness_keeps_32nds():
    fmt = Formatter("in", 16)
    assert fmt.length(23 / 32 * IN) == '3/4"'
    assert fmt.thickness(23 / 32 * IN) == '23/32"'
    assert Formatter("mm").thickness(18.256) == "18.26 mm"
