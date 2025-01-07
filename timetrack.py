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

import typing as t
import logging
import os
import re
import subprocess
import typing as t
from configparser import ConfigParser
from contextlib import contextmanager
from datetime import date, datetime, timedelta, time
from pathlib import Path

import pytimeparse
import typer
from pydantic import BaseModel, Field
from rich import box
from rich.console import Console
from rich.table import Table
from typing_extensions import Annotated

log = logging.getLogger(__name__)
console = Console()

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"

RE_PROJECT = re.compile(r"^\+\w+| \+\w+")
RE_CONTEXT = re.compile(r"^\@\w+| \@\w+")


class TTrackFileMeta(BaseModel):
    file: Path
    line: int


class TTrackItemMeta(TTrackFileMeta):
    pass


class TTrackWorkdayMeta(TTrackFileMeta):
    pass


DoneFlag: t.TypeAlias = t.Literal["x", "_"]
BillableFlag: t.TypeAlias = t.Literal["$", "€", "-"]


TTKey: t.TypeAlias = t.Literal["done", "billable", "date", "time", "raw", "text"]
TTValue: t.TypeAlias = t.Union[date, str, DoneFlag, BillableFlag, "TTrackTimeItemRaw"]
OptionalTTValue: t.TypeAlias = TTValue | None


def format_timedelta(td: timedelta) -> str:
    hours, rest = divmod(td.total_seconds(), 3600)
    minutes, _ = divmod(rest, 60)
    if hours > 0:
        if minutes > 0:
            return "{:01}h{:01}m".format(int(hours), int(minutes))
        else:
            return "{:01}h".format(int(hours))
    else:
        return "{:01}m".format(int(minutes))


class TTrackTimeItem(BaseModel):
    raw: str
    time: timedelta

    def format(self) -> str:
        return format_timedelta(self.time)


class TTrackTimeItemRaw(t.TypedDict):
    raw: str
    time: timedelta


class TTrackTime(BaseModel):
    time: time


class TTrackStartTime(TTrackTime):
    SYMBOL: t.Literal[">"] = ">"


class TTrackEndTime(TTrackTime):
    SYMBOL: t.Literal["<"] = "<"


class TTrackWorkday(BaseModel):
    meta: TTrackWorkdayMeta
    date: date
    times: list[TTrackStartTime | TTrackEndTime] = Field(default_factory=list)


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
        parts.append(self.time.format())
        parts.append(self.text)
        return sep.join(parts)

    def has_project(self) -> bool:
        return RE_PROJECT.search(self.text) is not None

    def has_context(self) -> bool:
        return RE_CONTEXT.search(self.text) is not None

    @property
    def project(self) -> str | None:
        if not self.has_project():
            return None
        if match := RE_PROJECT.search(self.text):
            return match.group().strip()
        return None

    @property
    def context(self) -> str | None:
        if not self.has_context():
            return None
        if match := RE_CONTEXT.search(self.text):
            return match.group().strip()
        return None


class TTrackRawItem(t.TypedDict):
    done: None | DoneFlag


class TTrackData(BaseModel):
    items: list[TTrackItem] = Field(default_factory=list)
    workdays: list[TTrackWorkday] = Field(default_factory=list)

    def get_or_create_workday(self, date_: date, meta: TTrackWorkdayMeta):
        for wd in self.workdays:
            if wd.date == date_:
                return wd
        wd = TTrackWorkday(meta=meta, date=date_)
        self.workdays.append(wd)
        return wd


class ParserFunc(t.Protocol):
    def __call__(self, line: str) -> t.Tuple[TTKey, OptionalTTValue, str]: ...


def parse_string(string: str, count: int) -> t.Tuple[str, str]:
    return string[0:count], string[count:]


def parser_done(line: str):
    key = "done"
    val, rest = parse_string(line, 1)
    if val in t.get_args(DoneFlag):
        return key, val, rest.strip()
    return key, None, line


def parser_billable(line: str):
    key = "billable"
    val, rest = parse_string(line, 1)
    if val in t.get_args(BillableFlag):
        return key, val, rest.strip()
    return key, None, line


def parser_date(line: str) -> t.Tuple[TTKey, date | None, str]:
    splitted = line.split(" ", 1)
    if len(splitted) == 1:
        splitted.append("")
    val, rest = splitted
    try:
        return "date", datetime.strptime(val, DATE_FORMAT).date(), rest.strip()
    except ValueError:
        return "date", None, line


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


def parse_line(line: str, context: dict[TTKey, OptionalTTValue] | None = None) -> dict:
    context = context or {}
    parsers: list[ParserFunc | None] = [
        parser_done,
        parser_billable,
        None if "date" in context else parser_date,
        parser_time,
    ]
    result: dict[TTKey, OptionalTTValue] = {**context}
    for p in parsers:
        if p is None:
            continue
        key, value, line = p(line)
        result[key] = value

    result["text"] = line.strip(" ")
    return result


def parse_workday_time(line: str) -> TTrackStartTime | TTrackEndTime:
    line = line.strip(" ")
    klass = TTrackStartTime if line[0] == ">" else TTrackEndTime
    line = line[1:]
    line = line.strip()
    return klass(time=datetime.strptime(line, TIME_FORMAT).time())


def parse_file(file: Path) -> TTrackData:
    result = TTrackData()
    with file.open("r") as fhandle:
        line_no = 0
        context: dict[TTKey, OptionalTTValue] = {}
        while line := fhandle.readline():
            line_no += 1
            if not line.strip() or line.strip().startswith("//"):
                continue
            match parser_date(line.strip()):
                case ("date", date() as ctx_date, ""):
                    context["date"] = ctx_date
                    continue
            if not line.startswith("  ") and "date" in context:
                del context["date"]
            elif line.startswith("  >") or line.startswith("  <"):
                try:
                    date_ = context["date"]
                except KeyError as error:
                    raise RuntimeError(
                        "you cannot add workday outside of date context."
                    ) from error
                workday = result.get_or_create_workday(
                    t.cast(date, date_),
                    meta=TTrackWorkdayMeta(
                        file=file,
                        line=line_no,
                    ),
                )
                workday.times.append(parse_workday_time(line))
                continue
            else:
                line = line.strip()
            item = TTrackItem.model_validate(
                {
                    "meta": {
                        "file": file,
                        "line": line_no,
                    },
                    **parse_line(line, context),
                },
            )
            result.items.append(item)
    return result


# -------------------------------------------------


@contextmanager
def measure_time():
    import time

    start = time.time()
    yield
    print("time", time.time() - start)


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
        self.timefile = timefile
        self.load()

    def load(self):
        self._data = parse_file(self.timefile)

    def add(self, line: list[str] | TTrackItem | TTrackRawItem):
        if isinstance(line, (dict, TTrackItem)):
            # TODO: implement
            raise NotImplementedError("not yet implemented")
        with self.timefile.open("a") as fhandle:
            fhandle.writelines([os.linesep, " ".join(line).strip()])

    def list(
        self, filter_options: TTrackFilterOptions | None = None
    ) -> t.Iterable[TTrackItem]:
        for item in self._data.items:
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

    def __init__(self, config_file: str | None = None):
        self.config_file = config_file
        self.config = ConfigParser(
            {
                **self._get_timefile_name_context(),
                **os.environ,
            }
        )
        if config_file:
            self.config.read([config_file])
        else:
            self.config.read(self.CONFIG_FILES)
        self.repository = TTrackRepository(self.get_timefile())

    def _get_timefile_name_context(self):
        today = date.today()
        return {
            "tt_year": today.strftime("%Y"),
            "tt_month": today.strftime("%m"),
            "tt_day": today.strftime("%d"),
        }

    def get_timefile(self) -> Path:
        timefile_name = self.config.get("timetrack", "timefile")
        timefile = Path(timefile_name)
        if not timefile.parent.exists():
            timefile.parent.mkdir(parents=True)
        if not timefile.exists():
            timefile.touch()
        return timefile

    def get_hookdir(self) -> Path:
        hookdir_name = self.config.get("timetrack", "hookdir")
        hookdir = Path(hookdir_name.format(**self._get_timefile_name_context()))
        if not hookdir.parent.exists():
            hookdir.parent.mkdir(parents=True)
        return hookdir

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

    def apply_hook(self, prefix: str, context: dict) -> dict:
        hooks_to_call = sorted(
            [hook for hook in self.config.options("hooks") if hook.startswith(prefix)]
        )
        for hook in hooks_to_call:
            command = self.config.get("hooks", hook)
            subprocess.call(command, cwd=str(self.get_hookdir().absolute()))


TIMESPAN_TODAY: t.Final[str] = "today"
TIMESPAN_MONTH: t.Final[str] = "month"
TIMESPAN_WEEK: t.Final[str] = "week"


def timespan_to_filter_options(timespan: str) -> TTrackFilterOptions:
    filter_options = TTrackFilterOptions()
    match timespan:
        case "all":
            pass
        case "today" | "t" | "to":
            tstart = tend = date.today()
            filter_options.daterange = (tstart, tend)
        case "yesterday" | "ye" | "yes" | "y":
            tstart = tend = date.today() - timedelta(days=1)
            filter_options.daterange = (tstart, tend)
        case "week" | "we" | "w":
            tend = date.today()
            tstart = date.today() - timedelta(days=tend.weekday())
            filter_options.daterange = (tstart, tend)
        case "month" | "mo" | "m":
            tend = date.today()
            tstart = date(tend.year, tend.month, 1)
            filter_options.daterange = (tstart, tend)
        case _:
            raise NotImplementedError("individual timespans are not yet supported.")
    return filter_options


@app.callback()
def root_callback(
    ctx: typer.Context,
    config_file: Annotated[
        str, typer.Option("-c", "--config", envvar="TT_CONFIG_FILE")
    ] = None,
):
    ctx.obj = TTrackContextObj(config_file)
    logging.basicConfig(
        filename=ctx.obj.get_log_file(),
        level=getattr(logging, ctx.obj.get_log_level()),
        format=ctx.obj.get_log_format(),
    )


@app.command("a")
@app.command("add")
def cmd_add(
    ctx: typer.Context,
    text: Annotated[list[str], typer.Argument()],
    time_: Annotated[str, typer.Option("--time", "-t")],
    is_done: Annotated[
        bool,
        typer.Option("--is-done/--is-not-done", "-d/-D", is_flag=True, flag_value=True),
    ] = False,
    is_billable: Annotated[
        bool,
        typer.Option(
            "--is-billalbe/--is-not-billable", "-b/-B", is_flag=True, flag_value=True
        ),
    ] = False,
):
    line = [
        "x" if is_done else "",
        "$" if is_billable else "",
        date.today().strftime(DATE_FORMAT),
        time_,
        *text,
    ]
    ctx_obj: TTrackContextObj = ctx.obj
    ctx_obj.repository.add(line)
    ctx_obj.apply_hook("post-add", {})


@app.command("ls")
@app.command("summary")
def cmd_summary(
    ctx: typer.Context,
    timespan: Annotated[str, typer.Argument()] = TIMESPAN_TODAY,
):
    ctx_obj: TTrackContextObj = ctx.obj

    filter_options = timespan_to_filter_options(timespan)

    billable = timedelta(seconds=0)
    overall = timedelta(seconds=0)

    table = Table(box=box.MINIMAL, padding=(0, 1))
    table.add_column("#", justify="right")
    table.add_column("x")
    table.add_column("$")
    table.add_column("date")
    table.add_column("time", justify="right")
    table.add_column("text")
    table.add_column("project", justify="right")
    table.add_column("context", justify="right")

    for index, line in enumerate(ctx_obj.repository.list(filter_options)):
        # console.print(line.to_line(), style=ctx_obj.get_rich_line_style())
        table.add_row(
            str(index),
            line.done or "-",
            line.billable or "_",
            line.date.strftime(DATE_FORMAT),
            line.time.format(),
            line.text,
            line.project,
            line.context,
        )
        if line.is_billable():
            billable += line.time.time
        overall += line.time.time

    table.add_row(
        "",
        "",
        format_timedelta(billable),
        "",
        format_timedelta(overall),
        "",
        "",
        "",
        end_section=True,
    )

    console.print(table)


@app.command("edit")
def edit_cmd(
    ctx: typer.Context,
    editor: Annotated[str, typer.Option("-e", "--editor", envvar="EDITOR")] = "",
):
    ctx_obj: TTrackContextObj = ctx.obj
    if not editor:
        raise Exception("no editor")
    timefile = ctx_obj.get_timefile()
    ctx_obj.apply_hook("pre-edit", {})
    subprocess.call([editor, str(timefile.absolute())])
    ctx_obj.apply_hook("post-edit", {})
    ctx_obj.repository.load()
    cmd_summary(ctx)


# @app.command("config")
# def config_cmd(ctx: typer.Context):
#     ctx_obj: TTrackContextObj = ctx.obj
#     sections = ctx_obj.config.sections()
#     for section in sections:
#         print(f"[{section}]")
#         options = ctx_obj.config.options(section)
#         for option in options:
#             value = dd
#             print(f"{option} = {value}")


@app.command("test")
def test_cmd(ctx: typer.Context):
    ctx_obj: TTrackContextObj = ctx.obj
    print(ctx_obj.config.options("hooks"))


@app.command("info")
def info_cmd(ctx: typer.Context):
    ctx_obj: TTrackContextObj = ctx.obj
    typer.echo(f"timefile: {ctx_obj.get_timefile()}")
    typer.echo(f"hookdir: {ctx_obj.get_hookdir()}")


if __name__ == "__main__":
    app()
