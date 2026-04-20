"""
transferit — pure-Python client for https://transfer.it.

Primary API (everything hangs off the :class:`Transferit` class):

    Transferit.upload(path, ...)            → share URL
    Transferit.download(url, dir, ...)      → list of written paths
    Transferit.info(url, ...)               → list[TransferNode]
    Transferit.metadata(url, ...)           → TransferInfo

Typed containers returned by the read-side methods:

    TransferInfo, TransferNode

Low-level escape hatches for advanced use:

    MegaAPI, MegaAPIError

See ``REVERSE_ENGINEERING.md`` for protocol notes.
"""

from __future__ import annotations

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0.0.0.dev0"

from ._api import MegaAPI, MegaAPIError
from ._client import Transferit
from ._models import TransferInfo, TransferNode

__all__ = [
    "__version__",
    "Transferit",
    "TransferInfo",
    "TransferNode",
    "MegaAPI",
    "MegaAPIError",
]
