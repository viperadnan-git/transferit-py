"""`transferit metadata` subcommand — transfer-level info (xi)."""

from __future__ import annotations

import json as _json

import click

from transferit import Transferit

from ._common import render_metadata_panel


@click.command(
    "metadata",
    short_help="Show a transfer's top-level metadata only.",
    help=(
        "Show just the transfer-level metadata — title, sender, message, "
        "password flag, file/byte counts, zip status.  Use "
        "`transferit info` if you also want the file listing.\n\n"
        "LINK can be either a full share URL or the 12-character handle."
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
    help="Print machine-readable JSON instead of the formatted panel.",
)
def cmd_metadata(url_or_xh: str, password: str | None, as_json: bool) -> None:
    """Fetch transfer-level metadata — title, sender, message, size, password flag."""
    try:
        with Transferit() as tx:
            meta = tx.metadata(url_or_xh, password=password)
    except ValueError as ex:
        raise click.BadParameter(str(ex))

    if as_json:
        click.echo(_json.dumps(meta.to_json_dict(), indent=2, ensure_ascii=False))
        return

    render_metadata_panel(meta)
