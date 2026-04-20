"""Shared formatting helpers used by every CLI subcommand."""

from __future__ import annotations

import datetime as _dt
import mimetypes

try:
    import click
    from rich import box as _rich_box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table
except ImportError as ex:  # pragma: no cover
    raise SystemExit(
        "The transferit CLI requires the [cli] extra.\n"
        "Install with:\n"
        "    pip install 'transferit-py[cli]'   # or\n"
        "    uv add 'transferit-py[cli]'\n"
    ) from ex

from transferit import TransferInfo
from transferit._transfer import (
    MAX_EXPIRY_SECONDS,
    MIN_EXPIRY_SECONDS,
    humanise_duration,
    parse_duration,
)

CONSOLE = Console(stderr=True, highlight=False)


# ---------- shared panel / grid helpers ----------


def kv_grid() -> Table:
    """Two-column key/value grid used in every panel body."""
    g = Table.grid(padding=(0, 2))
    g.add_column(style="dim", justify="right")
    g.add_column(overflow="fold")
    return g


def render_transferit_panel(body) -> None:
    """Rounded blue panel with the ``transfer.it`` brand in the top-right."""
    CONSOLE.print(
        Panel(
            body,
            title="[bold]transfer.it[/bold]",
            title_align="right",
            border_style="blue",
            box=_rich_box.ROUNDED,
        )
    )


def render_metadata_panel(meta: TransferInfo) -> None:
    """Render the rounded metadata panel (+ optional message panel)."""
    body = kv_grid()
    body.add_row("handle", f"[bold blue]{meta.xh}[/bold blue]")
    body.add_row("url", f"[link={meta.url}]{meta.url}[/link]")
    if meta.title:
        body.add_row("title", f"[bold]{meta.title}[/bold]")
    if meta.sender:
        body.add_row("sender", meta.sender)
    body.add_row(
        "password",
        "[yellow]required[/yellow]"
        if meta.password_protected
        else "[green]none[/green]",
    )
    if meta.zip_handle:
        zstate = "building…" if meta.zip_pending else "ready"
        body.add_row("zip", f"{meta.zip_handle}  [dim]({zstate})[/dim]")
    body.add_row(
        "totals",
        f"{humanise_bytes(meta.total_bytes)}  •  "
        f"[bold]{meta.file_count}[/bold] files, "
        f"[bold]{max(0, meta.folder_count - 1)}[/bold] subfolder(s)",
    )

    render_transferit_panel(body)
    if meta.message:
        CONSOLE.print(
            Panel(meta.message, title="message", border_style="dim", title_align="left")
        )


# ---------- formatting ----------


def humanise_bytes(n: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    f = float(n)
    for i, u in enumerate(units):
        if f < 1024 or i == len(units) - 1:
            return f"{f:,.1f} {u}" if i else f"{int(f):,} {u}"
        f /= 1024
    return f"{f:,.1f} PiB"


def humanise_time(ts: int | float | None) -> str:
    if not ts:
        return "—"
    return (
        _dt.datetime.fromtimestamp(int(ts))
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def guess_mime(name: str | None) -> str:
    if not name:
        return "application/octet-stream"
    return mimetypes.guess_type(name, strict=False)[0] or "application/octet-stream"


def bytes_progress() -> Progress:
    """A Rich Progress with bar + bytes-done + speed + ETA, all in blue."""
    return Progress(
        SpinnerColumn(style="blue"),
        TextColumn("[bold]{task.description}"),
        BarColumn(
            bar_width=None,
            complete_style="blue",
            finished_style="bold blue",
            pulse_style="blue",
        ),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(compact=True),
        console=CONSOLE,
        transient=False,
    )


def status(msg: str, *, icon: str = "•", style: str = "blue") -> None:
    CONSOLE.print(f"[{style}]{icon}[/{style}] {msg}")


# ---------- click param helpers ----------


def parse_schedule(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    s = value.strip()
    if s.isdigit():
        return int(s)
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError as ex:
        raise click.BadParameter(
            f"--schedule must be ISO 8601 (e.g. 2026-04-25T09:00) or a unix timestamp: {ex}"
        )
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp())


class ExpiryDuration(click.ParamType):
    """Click type for --expiry: accepts '30s' / '2h' / '7d' / '1y' / '1y6m' → seconds."""

    name = "duration"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if value is None:
            return None
        try:
            seconds = parse_duration(str(value))
        except ValueError as ex:
            self.fail(str(ex), param, ctx)
        if not (MIN_EXPIRY_SECONDS <= seconds <= MAX_EXPIRY_SECONDS):
            self.fail(
                f"{value} is out of range "
                f"[{MIN_EXPIRY_SECONDS}s .. {humanise_duration(MAX_EXPIRY_SECONDS)}]",
                param,
                ctx,
            )
        return seconds
