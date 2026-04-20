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
    CONSOLE,
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
        "  transferit upload ./src -x '.git' -x '__pycache__'\n"
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
    help="Parallel connections per file.  Raise on fast links for more throughput.",
)
@click.option(
    "-j",
    "--parallel",
    type=click.IntRange(1, 16),
    default=None,
    help=(
        "Files uploaded at the same time.  Auto-chosen by default (usually "
        "2–4).  Raise for transfers with lots of small files; leave alone "
        "for a single big file."
    ),
)
@click.option(
    "-m",
    "--message",
    help="Short note displayed next to the files on the transfer page.",
)
@click.option(
    "-p",
    "--password",
    help="Require this password to open the transfer.  Never sent in plain text.",
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
    "-x",
    "--exclude",
    "excludes",
    metavar="PATTERN",
    multiple=True,
    help=(
        "Skip files or folders matching this glob pattern.  Repeat for "
        "multiple, e.g. -x .git -x '*.pyc' -x node_modules."
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
    parallel: int | None,
    message: str | None,
    password: str | None,
    sender: str | None,
    expiry: int | None,
    notify_expiry: bool,
    max_downloads: int | None,
    recipients: tuple[str, ...],
    schedule: str | None,
    excludes: tuple[str, ...],
    as_json: bool,
) -> None:
    """Upload PATH to transfer.it."""
    started = time.monotonic()
    schedule_ts = parse_schedule(schedule)
    if schedule_ts is not None and not recipients:
        raise click.UsageError("--schedule only makes sense with --recipient")

    exclude_opt = list(excludes) if excludes else None
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
                parallel=parallel,
                exclude=exclude_opt,
            )
        click.echo(_json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False))
        return

    show_per_file = path.is_dir()

    def _rel_label(fp: Path) -> str:
        try:
            rel = fp.relative_to(path).as_posix()
        except ValueError:
            return fp.name
        return fp.name if rel == "." else rel

    def _fit_label(s: str) -> str:
        # Keep the tail (filename) and trim the parent path.  Budget is roughly
        # a third of the current terminal — matches how `tree` / `ls -F` wrap.
        budget = max(16, (CONSOLE.width or 80) // 3)
        if len(s) <= budget:
            return s
        return "…" + s[-(budget - 1) :]

    with bytes_progress() as progress:
        overall_label = (
            f"[bold]uploading[/bold] · {file_label}" if show_per_file else file_label
        )
        overall = progress.add_task(overall_label, total=1)

        def on_start(total_bytes: int, _file_count: int) -> None:
            progress.update(overall, total=total_bytes or 1)

        # fileno -> Rich task id, so per-file bars line up with whichever file
        # is currently streaming (several can be active at once when
        # --parallel > 1).
        active: dict[int, int] = {}

        def on_file_start(idx: int, file_path: Path, fsize: int) -> None:
            if not show_per_file:
                return
            active[idx] = progress.add_task(
                f"[dim]{_fit_label(_rel_label(file_path))}[/dim]", total=fsize or 1
            )

        def on_file_progress(idx: int, file_path: Path, sent: int, fsize: int) -> None:
            tid = active.get(idx)
            if tid is not None:
                progress.update(tid, completed=sent)

        def on_file_done(idx: int, file_path: Path, fsize: int) -> None:
            tid = active.pop(idx, None)
            if tid is not None:
                progress.update(tid, completed=fsize or 1)
                progress.remove_task(tid)

        def on_progress(sent: int, total: int) -> None:
            progress.update(overall, completed=sent, total=total or 1)

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
                parallel=parallel,
                exclude=exclude_opt,
                on_start=on_start,
                on_progress=on_progress,
                on_file_start=on_file_start if show_per_file else None,
                on_file_progress=on_file_progress if show_per_file else None,
                on_file_done=on_file_done if show_per_file else None,
            )

    elapsed = time.monotonic() - started
    size = result.total_bytes
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
