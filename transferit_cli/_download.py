"""`transferit download` subcommand."""

from __future__ import annotations

import json as _json
import time
from pathlib import Path

import click

from transferit import Transferit, TransferNode

from ._common import (
    bytes_progress,
    humanise_bytes,
    kv_grid,
    render_transferit_panel,
    status,
)


@click.command(
    "download",
    short_help="Download every file in a transfer.",
    help=(
        "Download every file in a transfer to a local directory.\n\n"
        "Folder structure from the transfer is recreated on disk.  "
        "Existing files are kept unless --force is given.\n\n"
        "LINK can be either a full share URL or the 12-character handle."
    ),
)
@click.argument("url_or_xh", metavar="LINK")
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Write files into this directory (created if missing).",
)
@click.option(
    "-p",
    "--password",
    help="Password required to open the transfer (if the sender set one).",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Overwrite files that already exist in the output directory.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print machine-readable JSON instead of the formatted summary.",
)
def cmd_download(
    url_or_xh: str,
    output_dir: Path,
    password: str | None,
    force: bool,
    as_json: bool,
) -> None:
    """Download every file in a transfer."""
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    if as_json:
        try:
            with Transferit() as tx:
                result = tx.download(
                    url_or_xh,
                    output_dir,
                    password=password,
                    force=force,
                )
        except ValueError as ex:
            raise click.BadParameter(str(ex))
        click.echo(_json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False))
        return

    result = None
    with bytes_progress() as progress:
        # For a single-file transfer the overall bar IS the file bar (no
        # sub-rows); for folders we keep an overall total + per-file sub-bar.
        state: dict[str, object] = {"overall": None, "current": None, "single": False}

        def on_start(files: list[TransferNode], total: int) -> None:
            status(
                f"downloading {len(files)} file(s), "
                f"{humanise_bytes(total)} [dim]({total:,} bytes)[/dim] "
                f"→  {output_dir}",
                style="green",
            )
            state["single"] = len(files) == 1
            label = (
                files[0].name or files[0].handle
                if state["single"]
                else "[bold]total[/bold]"
            )
            state["overall"] = progress.add_task(label, total=total if total > 0 else 1)

        def on_file_start(node: TransferNode, out_path: Path) -> None:
            if state["single"]:
                return
            state["current"] = progress.add_task(
                node.name or node.handle,
                total=node.size or 1,
            )

        def on_file_progress(node: TransferNode, done: int, total: int) -> None:
            if state["single"]:
                # overall tracks per-byte progress directly
                progress.update(state["overall"], completed=done)
                return
            tid = state.get("current")
            if tid is not None:
                progress.update(tid, completed=done)

        def on_file_done(node: TransferNode, out_path: Path) -> None:
            tid = state.get("current")
            if tid is not None:
                progress.update(tid, completed=node.size or 1)
                progress.remove_task(tid)
                state["current"] = None
            if state["single"]:
                progress.update(state["overall"], completed=node.size or 1)
            else:
                progress.advance(state["overall"], node.size or 0)

        def on_skip(node: TransferNode, out_path: Path) -> None:
            progress.console.print(
                f"[yellow]skip[/yellow] {out_path} (use --force to overwrite)"
            )
            if state["single"]:
                progress.update(state["overall"], completed=node.size or 0)
            else:
                progress.advance(state["overall"], node.size or 0)

        try:
            with Transferit() as tx:
                result = tx.download(
                    url_or_xh,
                    output_dir,
                    password=password,
                    force=force,
                    on_start=on_start,
                    on_file_start=on_file_start,
                    on_file_progress=on_file_progress,
                    on_file_done=on_file_done,
                    on_skip=on_skip,
                )
        except ValueError as ex:
            raise click.BadParameter(str(ex))

    elapsed = time.monotonic() - started
    rate = (result.total_bytes / elapsed / 1e6) if elapsed and result else 0
    written = len(result.paths) - len(result.skipped) if result else 0

    body = kv_grid()
    body.add_row("source", result.xh)
    body.add_row("destination", result.output_dir)
    body.add_row(
        "files",
        f"{written} written"
        + (
            f", [yellow]{len(result.skipped)} skipped[/yellow]"
            if result.skipped
            else ""
        ),
    )
    body.add_row(
        "size",
        f"{humanise_bytes(result.total_bytes)}  [dim]({result.total_bytes:,} bytes)[/dim]",
    )
    body.add_row("elapsed", f"{elapsed:.1f}s  [dim]({rate:.2f} MB/s)[/dim]")
    render_transferit_panel(body)
