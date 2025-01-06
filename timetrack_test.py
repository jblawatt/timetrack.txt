from timetrack import (
    TTrackItem,
    TTrackItemMeta,
    TTrackTimeItem,
    parse_line,
    RE_PROJECT,
    RE_CONTEXT,
    parser_date,
)
from pathlib import Path
import pytest
from datetime import date, timedelta


@pytest.mark.parametrize(
    "input,expected",
    (
        (
            "$ 2023-10-10 .. done some work for +project",
            {
                "billable": True,
                "done": False,
                "date": date(2023, 10, 10),
                "time": timedelta(minutes=30),
                "text": "done some work for +project",
            },
        ),
        (
            "x 2023-10-10 1h15m done some work for +project",
            {
                "billable": False,
                "done": True,
                "date": date(2023, 10, 10),
                "time": timedelta(minutes=75),
                "text": "done some work for +project",
            },
        ),
        (
            "2023-10-10 13:00-13:20 done some work for +project",
            {
                "billable": False,
                "done": False,
                "date": date(2023, 10, 10),
                "time": timedelta(minutes=20),
                "text": "done some work for +project",
            },
        ),
        (
            "x $ 2023-10-10 done some work for +project",
            {
                "billable": True,
                "done": True,
                "date": date(2023, 10, 10),
                "time": timedelta(minutes=0),
                "text": "done some work for +project",
            },
        ),
    ),
)
def test_parse_line(input: str, expected: dict):
    line = parse_line(input)
    assert line == expected


def test_get_project():
    assert RE_PROJECT.search("hello +project world") is not None
    assert RE_PROJECT.search("hello world") is None
    assert RE_PROJECT.search("+project hello world") is not None
    assert RE_PROJECT.search("hello world +project") is not None
    assert RE_PROJECT.search("hello abc+abc foo") is None


def test_get_context():
    assert RE_CONTEXT.search("hello @context world") is not None
    assert RE_CONTEXT.search("hello world") is None
    assert RE_CONTEXT.search("@context hello world") is not None
    assert RE_CONTEXT.search("hello world @context") is not None
    assert RE_CONTEXT.search("hello hello@hello.de foo") is None


@pytest.fixture(name="test_item")
def create_ttrack_item():
    return TTrackItem(
        meta=TTrackItemMeta(
            file=Path("not-exists.txt"),
            line=10,
        ),
        done="x",
        billable="$",
        date=date.today(),
        time=TTrackTimeItem(raw="1h", time=timedelta(minutes=60)),
        text="hello world +ttrack @foss",
    )


@pytest.mark.parametrize(
    "text,expected",
    (("hello +project", True), ("hello world", False)),
)
def test_has_project(text: str, expected: bool, test_item: TTrackItem):
    test_item.text = text
    assert test_item.has_project() == expected


@pytest.mark.parametrize(
    "text,expected",
    (("hello @foss", True), ("hello world", False)),
)
def test_has_context(text: str, expected: bool, test_item: TTrackItem):
    test_item.text = text
    assert test_item.has_context() == expected


def test_parser_date():
    key, date, str_ = parser_date("2023-10-10")
    print(key, date, str_)


def test_parser_date_fail():
    key, date, str_ = parser_date("not-a-date")
    print(key, date, str_)
