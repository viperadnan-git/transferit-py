"""`transferit info` subcommand — metadata panel + aligned file/folder listing."""

from __future__ import annotations

import json as _json

import click
from rich.table import Table

from transferit import MegaAPIError, TransferInfo, Transferit, TransferNode

from ._common import (
    CONSOLE,
    guess_mime,
    humanise_bytes,
    humanise_time,
    render_metadata_panel,
)

# Box-drawing glyphs for the tree column.  Every segment is exactly 4 chars
# so nested rows align cleanly.
_BRANCH = "├── "
_LAST = "└── "
_PIPE = "│   "
_SPACE = "    "


@click.command(
    "info",
    short_help="Show a transfer's metadata and its file/folder listing.",
    help=(
        "Show everything about a transfer in one view: the metadata panel "
        "(title, sender, size, password status) plus an aligned file/folder "
        "tree.  LINK can be either a full share URL or the 12-character handle."
    ),
)
@click.argument("url_or_xh", metavar="LINK")
@click.option(
    "-p",
    "--password",
    help="Password required to open the transfer (if the sender set one).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print machine-readable JSON instead of the formatted tables.",
)
def cmd_info(url_or_xh: str, password: str | None, as_json: bool) -> None:
    """Show transfer-level metadata and a consolidated view of every node.

    Metadata is always available; the file listing is gated behind the
    transfer password.  If a listing can't be fetched, the metadata panel
    is still shown and the listing is marked as withheld.
    """
    listing_error: str | None = None
    nodes: list[TransferNode] | None = None
    try:
        with Transferit() as tx:
            meta = tx.metadata(url_or_xh, password=password)
            try:
                nodes = tx.info(url_or_xh, password=password)
            except MegaAPIError as ex:
                if ex.code == -14:
                    listing_error = str(ex)
                else:
                    raise
    except ValueError as ex:
        raise click.BadParameter(str(ex))

    if as_json:
        payload: dict = {
            "metadata": meta.to_json_dict(),
            "nodes": ([n.to_json_dict() for n in nodes] if nodes is not None else None),
        }
        if listing_error:
            payload["listing_error"] = listing_error
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    render_metadata_panel(meta)
    if nodes is None:
        CONSOLE.print(
            f"\n[yellow]file listing hidden[/yellow] · [dim]{listing_error}[/dim]"
        )
        return
    _render_listing(meta, nodes)


# ---------- consolidated listing ----------


def _render_listing(meta: TransferInfo, nodes: list[TransferNode]) -> None:
    """
    Print every folder and file in one table.  The ``name`` column carries
    box-drawing glyphs (``├── │   └──``) so hierarchy is visible, while the
    other columns stay right-aligned across every row.
    """
    if not nodes:
        CONSOLE.print("[yellow]transfer contains no files[/yellow]")
        return

    root = next((n for n in nodes if n.is_folder and not n.parent), None)
    if root is None:
        CONSOLE.print("[yellow]transfer has no root folder[/yellow]")
        return

    # Parent → sorted children (folders before files; alpha within each).
    children_by_parent: dict[str, list[TransferNode]] = {}
    for n in nodes:
        if n.handle == root.handle:
            continue
        children_by_parent.setdefault(n.parent, []).append(n)
    for children in children_by_parent.values():
        children.sort(key=lambda n: (n.is_file, (n.name or "").lower()))

    table = Table(
        show_header=True,
        show_edge=False,
        box=None,
        pad_edge=False,
        header_style="bold",
        expand=False,
    )
    table.add_column("name", no_wrap=True, overflow="fold")
    table.add_column("size", justify="right", no_wrap=True)
    table.add_column("mime", style="magenta", no_wrap=True)
    table.add_column("uploaded", no_wrap=True)
    table.add_column("handle", style="dim", no_wrap=True)

    def _name_cell(node: TransferNode, prefix: str) -> str:
        """Render the ``name`` cell: dim tree glyphs + styled leaf label."""
        name = node.name or node.handle
        styled = f"[bold blue]{name}/[/bold blue]" if node.is_folder else name
        if not prefix:
            return styled
        return f"[dim]{prefix}[/dim]{styled}"

    def _add_row(node: TransferNode, prefix: str) -> None:
        if node.is_folder:
            table.add_row(
                _name_cell(node, prefix),
                "",
                "",
                humanise_time(node.timestamp),
                node.handle,
            )
        else:
            table.add_row(
                _name_cell(node, prefix),
                humanise_bytes(node.size or 0),
                guess_mime(node.name),
                humanise_time(node.timestamp),
                node.handle,
            )

    # Root has no prefix — sits at the top of the tree.
    _add_row(root, "")

    def _walk(parent_handle: str, ancestor_prefix: str) -> None:
        children = children_by_parent.get(parent_handle, [])
        last_idx = len(children) - 1
        for i, child in enumerate(children):
            is_last = i == last_idx
            connector = _LAST if is_last else _BRANCH
            _add_row(child, ancestor_prefix + connector)
            if child.is_folder:
                # Pipe continues under children when this node has siblings below
                # it; otherwise pad with spaces so the tree closes cleanly.
                _walk(
                    child.handle,
                    ancestor_prefix + (_SPACE if is_last else _PIPE),
                )

    _walk(root.handle, "")

    CONSOLE.print()
    CONSOLE.print(table)
