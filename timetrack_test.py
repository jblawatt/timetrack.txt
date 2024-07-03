from timetrack import parse_line
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
