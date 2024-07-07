"""
$ 2023-10-10 1h14m
$ 2023-10-10 ....

x $ 2023-10-10 13:30-13:40 +project @context this is what i did #tag #tag #tag

- default context today
- default project
- default context

- edit "today"
- storage in txt file?
- storage backend txt|sqlite3


TODO:
- build in
    - strict mypy
    - strict ruff
    - no dependencies
    - 100%+ unittest
    - 100%+ functional test
- hooks
- build with go!?
"""

import time
import typing as t
import logging
from functools import partial, wraps
from rich.console import Console
from typing_extensions import Annotated


import re
from datetime import date, timedelta, datetime

import typer
from pathlib import Path
from pydantic import BaseModel
import pytimeparse
from configparser import ConfigParser
import subprocess


log = logging.getLogger(__name__)
console = Console()

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

RE_PROJECT = re.compile(r"\+\w+")
RE_CONTEXT = re.compile(r"\@\w+")

config = ConfigParser()


def timeit(func: t.Callable):
    @wraps(func)
    def timeit_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        print(f"Function {func.__name__}{args} {kwargs} Took {total_time:.4f} seconds")
        return result

    return timeit_wrapper


class TTrackItemMeta(BaseModel):
    file: Path
    line: int


DoneFlag: t.TypeAlias = t.Literal["x", "_"]
BillableFlag: t.TypeAlias = t.Literal["$", "€", "-"]


TTKey: t.TypeAlias = t.Literal["done", "billable", "date", "time", "raw"]
TTValue: t.TypeAlias = t.Union[date, str, DoneFlag, BillableFlag, "TTrackTimeItemRaw"]
OptionalTTValue: t.TypeAlias = TTValue | None


class TTrackTimeItem(BaseModel):
    raw: str
    time: timedelta


class TTrackTimeItemRaw(t.TypedDict):
    raw: str
    time: timedelta


class TTrackItem(BaseModel):
    meta: TTrackItemMeta
    done: None | DoneFlag
    billable: None | BillableFlag
    date: date
    time: TTrackTimeItem
    text: str

    def is_billable(self) -> bool:
        return self.billable in t.get_args(BillableFlag)

    def is_done(self) -> bool:
        return self.done in t.get_args(DoneFlag)

    def to_line(self, sep: str = "\t") -> str:
        parts = []
        if self.done is not None:
            parts.append(self.done)
        else:
            parts.append("_")
        if self.billable is not None:
            parts.append(self.billable)
        else:
            parts.append("_")
        parts.append(self.date.strftime(DATE_FORMAT))
        hours, rest = divmod(self.time.time.total_seconds(), 3600)
        minutes, _ = divmod(rest, 60)
        if hours > 0:
            if minutes > 0:
                parts.append("{:01}h{:01}m".format(int(hours), int(minutes)))
            else:
                parts.append("{:01}h".format(int(hours)))
        else:
            parts.append("{:01}m".format(int(minutes)))
        parts.append(self.text)
        return sep.join(parts)

    def has_project(self) -> bool:
        return RE_PROJECT.search(self.text) is not None

    def has_context(self) -> bool:
        return RE_CONTEXT.search(self.text) is not None

    @property
    def project(self) -> str | None:
        if self.has_project():
            return None
        if match := RE_PROJECT.search(self.text):
            return match.group()
        return None

    @property
    def context(self) -> str | None:
        if self.has_context():
            return None
        if match := RE_CONTEXT.search(self.text):
            return match.group()
        return None


class TTrackRawItem(t.TypedDict):
    done: None | DoneFlag


class ParserFunc(t.Protocol):
    def __call__(self, line: str) -> t.Tuple[TTKey, OptionalTTValue, str]: ...


def split_string(string: str, count: int) -> t.Tuple[str, str]:
    return string[0:count], string[count:]


def parser_done(line: str):
    key = "done"
    val, rest = split_string(line, 1)
    if val in t.get_args(DoneFlag):
        return key, val, rest.strip()
    return key, None, line


def parser_billable(line: str):
    key = "billable"
    val, rest = split_string(line, 1)
    if val in t.get_args(BillableFlag):
        return key, val, rest.strip()
    return key, None, line


def parser_date(line: str) -> t.Tuple[TTKey, date, str]:
    val, rest = line.split(" ", 1)
    return "date", datetime.strptime(val, DATE_FORMAT).date(), rest.strip()


def parse_line(line: str) -> dict:
    parsers: list[ParserFunc] = [
        parser_done,
        parser_billable,
        parser_date,
        parser_time,
    ]
    result: dict[TTKey, TTValue] = {}
    for p in parsers:
        key, value, line = p(line)
        result[key] = value

    result["text"] = line.strip(" ")
    return result


def parser_time(line: str) -> t.Tuple[TTKey, TTrackTimeItemRaw, str]:
    # improve, replace pytimeparse
    key = "time"
    val, rest = line.split(" ", 1)
    if value := pytimeparse.parse(val):
        return key, {"time": timedelta(seconds=value), "raw": val}, rest.strip(" ")
    if re.match(r"\.+", val):
        return (
            key,
            {"time": timedelta(minutes=15 * len(val)), "raw": val},
            rest.strip(" "),
        )
    try:
        start, end = val.split("-")
    except ValueError:
        return key, {"time": timedelta(seconds=0), "raw": "0m"}, line
    else:
        return (
            key,
            {
                "time": datetime.strptime(end, "%H:%M")
                - datetime.strptime(start, "%H:%M"),
                "raw": val,
            },
            rest.strip(" "),
        )


def parse_file(file: Path) -> list[TTrackItem]:
    result = []
    with file.open("r") as fhandle:
        line_no = 0
        while line := fhandle.readline():
            line_no += 1
            if not line.strip() or line.strip().startswith("//"):
                continue
            result.append(
                TTrackItem.parse_obj(
                    {
                        "meta": {
                            "file": file,
                            "line": line_no,
                        },
                        **parse_line(line),
                    }
                )
            )
    return result


# -------------------------------------------------

app = typer.Typer()


# TODO: no pydantic
class TTrackFilterOptions(BaseModel):
    daterange: t.Tuple[date, date] | None = None
    project: str | None = None
    context: str | None = None
    text: str | None = None


class TTrackRepository:
    def __init__(self, timefile: Path):
        self._data = parse_file(timefile)

    def list(
        self, filter_options: TTrackFilterOptions | None = None
    ) -> t.Iterable[TTrackItem]:
        for item in self._data:
            if filter_options is None:
                yield item
                continue
            if daterange := filter_options.daterange:
                start, end = daterange
                if not (start <= item.date <= end):
                    continue
            if project := filter_options.project:
                if item.project != project:
                    continue
            if context := filter_options.context:
                if item.context != context:
                    continue
            if text := filter_options.text:
                if text not in item.text.lower():
                    continue
            yield item


class TTrackContextObj:
    CONFIG_FILES: t.Final[list[str]] = ["timetrack.cfg"]

    config: ConfigParser
    repository: TTrackRepository

    def __init__(
        self,
    ):
        self.config = ConfigParser()
        self.config.read(self.CONFIG_FILES)
        self.repository = TTrackRepository(self.get_timefile())

    def _get_timefile_name_context(self):
        today = date.today()
        return {
            "year": today.strftime("%Y"),
            "month": today.strftime("%m"),
            "day": today.strftime("%d"),
        }

    def get_timefile(self) -> Path:
        timefile_name = self.config.get("timetrack", "timefile")
        timefile = Path(timefile_name.format(**self._get_timefile_name_context()))
        if not timefile.parent.exists():
            timefile.parent.mkdir(parents=True)
        if not timefile.exists():
            timefile.touch()
        return timefile

    def get_rich_line_style(self) -> str:
        return self.config.get("timetrack", "rich_line_style")

    def get_log_level(self) -> str:
        return self.config.get("timetrack", "log_level")

    def get_log_file(self) -> str | None:
        logfile = self.config.get("timetrack", "log_file")
        if logfile == "-":
            return None
        return logfile

    def get_log_format(self) -> str:
        logformat = self.config.get("timetrack", "log_format")
        if not logformat:
            return logging.BASIC_FORMAT
        return logformat


@app.callback()
def root_callback(ctx: typer.Context):
    ctx.obj = TTrackContextObj()
    logging.basicConfig(
        filename=ctx.obj.get_log_file(),
        level=getattr(logging, ctx.obj.get_log_level()),
        format=ctx.obj.get_log_format(),
    )


@app.command("add")
def cmd_add():
    pass


@app.command("ls")
def cmd_ls(ctx: typer.Context):
    ctx_obj: TTrackContextObj = ctx.obj
    for line in ctx_obj.repository.list():
        console.print(
            line.to_line(),
            style=ctx_obj.get_rich_line_style(),
        )


TIMESPAN_TODAY = "month"
TIMESPAN_MONTH = "month"
TIMESPAN_WEEK = "week"


def filter_none(item: TTrackItem) -> bool:
    return True


def filter_date_range(item: TTrackItem, tstart: date, tend: date) -> bool:
    if item.date >= tstart and item.date <= tend:
        return True
    return False


@app.command("summary")
def cmd_summary(
    ctx: typer.Context,
    timespan: Annotated[str, typer.Argument()] = TIMESPAN_TODAY,
):
    ctx_obj: TTrackContextObj = ctx.obj

    filter_options = TTrackFilterOptions()

    match timespan:
        case "all":
            pass
        case "today":
            tstart = date.today()
            tend = date.today()
            filter_options.daterange = (tstart, tend)
        case "week":
            tend = date.today()
            tstart = date.today() - timedelta(days=tend.weekday())
            filter_options.daterange = (tstart, tend)
        case "month":
            tend = date.today()
            tstart = date(tend.year, tend.month, 1)
            filter_options.daterange = (tstart, tend)
        case _:
            raise NotImplementedError("individual timespans are not yet supported.")
            # tstart, tend = parse_filter_timespan(timespan)

    billable = timedelta(seconds=0)
    overall = timedelta(seconds=0)

    for line in ctx_obj.repository.list(filter_options):
        console.print(line.to_line(), style=ctx_obj.get_rich_line_style())
        if line.is_billable():
            billable += line.time.time
        overall += line.time.time

    console.print(
        "[green]$ {billable}[/green] / {overall}".format(
            billable=billable, overall=overall
        )
    )


@app.command("edit")
def edit_cmd(
    ctx: typer.Context,
    editor: Annotated[str, typer.Option("-e", "--editor", envvar="EDITOR")] = "",
):
    ctx_obj: TTrackContextObj = ctx.obj
    if not editor:
        raise Exception("no editor")
    timefile = ctx_obj.get_timefile()
    subprocess.call([editor, str(timefile.absolute())])


if __name__ == "__main__":
    app()
