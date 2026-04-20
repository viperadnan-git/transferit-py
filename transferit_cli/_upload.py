"""`transferit upload` subcommand."""

from __future__ import annotations

import datetime as _dt
import json as _json
import time
from pathlib import Path

import click

from transferit import Transferit
from transferit._transfer import (
    MAX_EXPIRY_SECONDS,
    MIN_EXPIRY_SECONDS,
    humanise_duration,
)

from ._common import (
    ExpiryDuration,
    bytes_progress,
    humanise_bytes,
    kv_grid,
    parse_schedule,
    render_transferit_panel,
)


@click.command(
    "upload",
    short_help="Upload a file or folder and print the share link.",
    help=(
        "Upload a file or folder to transfer.it and print the share link.\n\n"
        "PATH can be a single file or a directory — directories are uploaded "
        "recursively with folder structure preserved.\n\n"
        "Examples:\n\n"
        "\b\n"
        "  transferit upload report.pdf\n"
        "  transferit upload ./project/ -e 7d --sender me@example.com\n"
        "  transferit upload big.mp4 -r alice@x.com -r bob@x.com \\\n"
        "                   --sender me@x.com --expiry 30d"
    ),
)
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-n",
    "--name",
    "--title",
    "title",
    help="Title shown on the transfer page.  Defaults to the file or folder name.",
)
@click.option(
    "-c",
    "--concurrency",
    type=click.IntRange(1, 32),
    default=8,
    show_default=True,
    help="Parallel upload connections per file.  Raise on fast links for more throughput.",
)
@click.option(
    "-m",
    "--message",
    help="Short note displayed next to the files on the transfer page.",
)
@click.option(
    "-p",
    "--password",
    help="Require this password to open the transfer (hashed locally before it leaves your machine).",
)
@click.option(
    "-s",
    "--sender",
    "--from",
    "sender",
    metavar="EMAIL",
    help=(
        "Your email address — shown as the sender.  "
        "Required whenever --message, --password, --expiry, or --recipient is also set."
    ),
)
@click.option(
    "-e",
    "--expiry",
    type=ExpiryDuration(),
    metavar="DURATION",
    help=(
        "How long the transfer stays accessible.  This is a duration, not a date — "
        "pass values like 30m, 2h, 7d, 1w, 1y, or combine them (e.g. 1y6m3d).  "
        f"Allowed: {MIN_EXPIRY_SECONDS}s to {humanise_duration(MAX_EXPIRY_SECONDS)}.  "
        "Omit to keep the transfer until you delete it."
    ),
)
@click.option(
    "--notify-expiry/--no-notify-expiry",
    default=False,
    help="Email the sender a reminder before the transfer expires.  Requires --expiry and --sender.",
)
@click.option(
    "--max-downloads",
    type=click.IntRange(1, None),
    metavar="N",
    help="Stop allowing downloads after N successful fetches.",
)
@click.option(
    "-r",
    "--recipient",
    "recipients",
    metavar="EMAIL",
    multiple=True,
    help="Email this recipient the link.  Repeat for multiple.  Requires --sender.",
)
@click.option(
    "--schedule",
    metavar="TIME",
    help=(
        "Delay sending the invitation email until this time — ISO 8601 "
        "(e.g. 2026-04-25T09:00) or a unix timestamp.  Requires --recipient."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print machine-readable JSON instead of the formatted summary.",
)
def cmd_upload(
    path: Path,
    title: str | None,
    concurrency: int,
    message: str | None,
    password: str | None,
    sender: str | None,
    expiry: int | None,
    notify_expiry: bool,
    max_downloads: int | None,
    recipients: tuple[str, ...],
    schedule: str | None,
    as_json: bool,
) -> None:
    """Upload PATH to transfer.it."""
    started = time.monotonic()
    schedule_ts = parse_schedule(schedule)
    if schedule_ts is not None and not recipients:
        raise click.UsageError("--schedule only makes sense with --recipient")

    size = (
        sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        if path.is_dir()
        else path.stat().st_size
    )
    file_label = path.name + ("/" if path.is_dir() else "")

    if as_json:
        with Transferit() as tx:
            result = tx.upload(
                path,
                title=title,
                message=message,
                password=password,
                sender=sender,
                expiry=expiry,
                notify_expiry=notify_expiry,
                max_downloads=max_downloads,
                recipients=list(recipients) if recipients else None,
                schedule=schedule_ts,
                concurrency=concurrency,
            )
        click.echo(_json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False))
        return

    is_folder = path.is_dir()

    with bytes_progress() as progress:
        overall_label = (
            f"[bold]uploading[/bold] · {file_label}" if is_folder else file_label
        )
        overall = progress.add_task(overall_label, total=size or 1)

        current_task: dict[str, object] = {"task": None, "start_bytes": 0}

        def on_file_start(idx: int, file_path: Path, fsize: int) -> None:
            if not is_folder:
                return
            try:
                rel = file_path.relative_to(path).as_posix()
            except ValueError:
                rel = file_path.name
            current_task["task"] = progress.add_task(rel, total=fsize or 1)

        def on_file_done(idx: int, file_path: Path, fsize: int) -> None:
            if not is_folder:
                return
            tid = current_task.get("task")
            if tid is not None:
                progress.update(tid, completed=fsize or 1)
                progress.remove_task(tid)
            current_task["task"] = None
            current_task["start_bytes"] = (current_task["start_bytes"] or 0) + fsize

        def on_progress(sent: int, total: int) -> None:
            progress.update(overall, completed=sent, total=total or 1)
            tid = current_task.get("task")
            if tid is not None:
                per_file = sent - (current_task.get("start_bytes") or 0)
                progress.update(tid, completed=max(0, per_file))

        with Transferit() as tx:
            result = tx.upload(
                path,
                title=title,
                message=message,
                password=password,
                sender=sender,
                expiry=expiry,
                notify_expiry=notify_expiry,
                max_downloads=max_downloads,
                recipients=list(recipients) if recipients else None,
                schedule=schedule_ts,
                concurrency=concurrency,
                on_progress=on_progress,
                on_file_start=on_file_start if is_folder else None,
                on_file_done=on_file_done if is_folder else None,
            )

    elapsed = time.monotonic() - started
    rate = (size / elapsed / 1e6) if elapsed else 0

    body = kv_grid()
    body.add_row("title", f"[bold]{result.title}[/bold]")
    body.add_row("source", file_label)
    body.add_row(
        "content",
        f"{result.file_count} file{'s' if result.file_count != 1 else ''}"
        + (f", {result.folder_count} folder(s)" if result.folder_count else ""),
    )
    body.add_row("size", f"{humanise_bytes(size)}  [dim]({size:,} bytes)[/dim]")
    body.add_row("elapsed", f"{elapsed:.1f}s  [dim]({rate:.2f} MB/s)[/dim]")
    if sender:
        body.add_row("sender", sender)
    if expiry:
        body.add_row(
            "expiry",
            f"{humanise_duration(expiry)} [dim]({expiry}s)[/dim]"
            + (" + notify" if notify_expiry else ""),
        )
    if password:
        body.add_row("password", "[green]set[/green]")
    if message:
        body.add_row("message", message if len(message) < 60 else message[:57] + "…")
    if max_downloads:
        body.add_row("max downloads", str(max_downloads))
    if recipients:
        body.add_row("recipients", ", ".join(recipients))
        if schedule_ts is not None:
            body.add_row(
                "scheduled", _dt.datetime.fromtimestamp(schedule_ts).isoformat()
            )
    body.add_row("share", f"[link={result.url}]{result.url}[/link]")
    render_transferit_panel(body)
