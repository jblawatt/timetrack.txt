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

import os
import typing as t
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


console = Console()

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

RE_PROJECT = re.compile(r"\+\w+")
RE_CONTEXT = re.compile(r"\@\w+")

config = ConfigParser()


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

TIMEFILE = Path("timetrack.txt")


# config = ConfigParser()
# config.read(["timetrack.cfg"])


class TTrackContextObj:
    CONFIG_FILES: t.Final[list[str]] = ["timetrack.cfg"]

    config: ConfigParser

    def __init__(self):
        self.config = ConfigParser()
        self.config.read(self.CONFIG_FILES)

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

    # def get_editor(self) -> str:
    #     return self.config.get("timetrack", "editor")


def parse_filter_timespan():
    pass


@app.callback()
def root_callback(ctx: typer.Context):
    ctx.obj = TTrackContextObj()


@app.command("add")
def cmd_add():
    pass


@app.command("ls")
def cmd_ls(ctx: typer.Context):
    ctx_obj: TTrackContextObj = ctx.obj
    for line in parse_file(ctx_obj.get_timefile()):
        console.print(line.to_line(), style="strike blink red")


@app.command("summary")
def cmd_summary(ctx: typer.Context, timespan=Annotated[str, typer.Argument()]):
    ctx_obj: TTrackContextObj = ctx.obj

    match timespan:
        case "today":
            tstart = date.today()
            tend = date.today()
        case "week":
            tstart = date.today()
            tend = date.today() + timespan(days=-7)
        case "month":
            pass
        case _:
            parse_filter_timespan(rest)

    billable = timedelta(seconds=0)
    overall = timedelta(seconds=0)

    for line in parse_file(ctx_obj.get_timefile()):
        console.print(line.to_line(), style="strike blink red")
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
