"""
Click-based CLI for transferit.

One file per subcommand — this package just assembles the group and
exposes :func:`main` for the ``transferit`` console-script entry point.

Requires the ``[cli]`` extra (``pip install transferit[cli]``).
"""

from __future__ import annotations

import sys

try:
    import click
except ImportError as ex:  # pragma: no cover
    raise SystemExit(
        "The transferit CLI requires the [cli] extra.\n"
        "Install with:\n"
        "    pip install 'transferit-py[cli]'   # or\n"
        "    uv add 'transferit-py[cli]'\n"
    ) from ex

from transferit import __version__
from transferit._api import MegaAPIError

from ._common import CONSOLE
from ._download import cmd_download
from ._info import cmd_info
from ._metadata import cmd_metadata
from ._upload import cmd_upload


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="transferit")
def cli() -> None:
    """
    transferit — upload and download files via transfer.it from the terminal.

    Run `transferit COMMAND --help` for the full option list of a command.
    """


cli.add_command(cmd_upload)
cli.add_command(cmd_download)
cli.add_command(cmd_info)
cli.add_command(cmd_metadata)


def main() -> int:
    """Console-script entry point (registered in pyproject.toml)."""
    try:
        cli(standalone_mode=False)
    except click.ClickException as ex:
        ex.show()
        return ex.exit_code
    except (click.Abort, KeyboardInterrupt):
        CONSOLE.print("[red]aborted[/red]")
        return 130
    except MegaAPIError as ex:
        CONSOLE.print(f"[red]error:[/red] {ex}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
