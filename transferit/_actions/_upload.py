"""
``Transferit.upload`` implementation.

Stateless — the caller (``Transferit``) owns the ``MegaAPI`` instance,
has already created an ephemeral session on it, and supplies a
``fileno_provider`` so file numbers stay monotonic across calls on the
same session.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from .._api import SHARE_BASE, MegaAPI, MegaAPIError
from .._crypto import rand_a32
from .._models import UploadResult
from .._transfer import cast_expiry_seconds, parse_duration
from .._upload import (
    DEFAULT_CONCURRENCY,
    _ws_upload_one,
    build_remote_tree,
    walk_folder,
)

ProgressCallback = Callable[[int, int], None]


def do_upload(
    api: MegaAPI,
    path: str | Path,
    *,
    fileno_provider: Callable[[], int],
    title: str | None = None,
    message: str | None = None,
    password: str | None = None,
    sender: str | None = None,
    expiry: int | str | None = None,
    notify_expiry: bool = False,
    max_downloads: int | None = None,
    recipients: list[str] | None = None,
    schedule: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: ProgressCallback | None = None,
    on_file_start: Callable[[int, Path, int], None] | None = None,
    on_file_done: Callable[[int, Path, int], None] | None = None,
) -> UploadResult:
    """
    Upload ``path`` (file or folder) into a fresh transfer and return an
    :class:`UploadResult`.  See :class:`Transferit.upload` for docstring.
    """
    expiry_seconds = parse_duration(expiry) if isinstance(expiry, str) else expiry
    expiry_seconds = cast_expiry_seconds(expiry_seconds)

    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(p)

    if p.is_dir():
        files, dir_rel_paths = walk_folder(p)
        local_root = p
        default_title = p.name
    elif p.is_file():
        files = [p]
        dir_rel_paths = []
        local_root = p.parent
        default_title = p.name
    else:
        raise FileNotFoundError(f"not a file or directory: {p}")

    if not files:
        raise FileNotFoundError(f"{p} contains no files to upload")

    if title is None:
        title = default_title

    if notify_expiry and (not expiry_seconds or not sender):
        raise MegaAPIError("notify_expiry requires both expiry>0 and sender")
    if (message or password or (expiry_seconds and expiry_seconds > 0)) and not sender:
        raise MegaAPIError(
            "sender email is required when setting message / password / expiry"
        )
    if recipients and not sender:
        raise MegaAPIError("recipients require sender email")

    total_bytes = sum(f.stat().st_size for f in files)
    uploaded_bytes = 0

    xh, root_h, _ = api.create_transfer(title)
    dir_handles = build_remote_tree(api, root_h, dir_rel_paths)

    pools = api.upload_pools()

    def _pick_pool(sz: int) -> tuple[str, str]:
        for entry in pools:
            if len(entry) < 2:
                continue
            host, uri = entry[0], entry[1]
            limit = entry[2] if len(entry) > 2 else 0
            if not limit or sz <= limit:
                return host, uri
        raise MegaAPIError(f"no upload pool available: {pools!r}")

    for f in files:
        fsize = f.stat().st_size
        host, uri = _pick_pool(fsize)
        ul_key = rand_a32(6)
        idx = fileno_provider()

        if on_file_start:
            on_file_start(idx, f, fsize)

        file_start = uploaded_bytes

        def _cb(sent: int, _tot: int, _base=file_start) -> None:
            if on_progress:
                on_progress(min(_base + sent, total_bytes), total_bytes)

        token, macs = asyncio.run(
            _ws_upload_one(
                host,
                uri,
                f,
                ul_key,
                fileno=idx,
                concurrency=concurrency,
                progress=_cb,
            )
        )

        rel_parent = f.parent.relative_to(local_root).as_posix() if p.is_dir() else ""
        if rel_parent == ".":
            rel_parent = ""
        target_h = dir_handles.get(rel_parent, root_h)
        api.finalise_file(target_h, token, ul_key, macs, f.name)

        uploaded_bytes += fsize
        if on_file_done:
            on_file_done(idx, f, fsize)

    extras_set = (
        any(
            v is not None and v != ""
            for v in (message, password, sender, expiry_seconds, max_downloads)
        )
        or notify_expiry
    )

    if extras_set:
        api.set_transfer_attributes(
            xh,
            title=title,
            message=message,
            password=password,
            sender=sender,
            expiry_seconds=expiry_seconds,
            notify_before_expiry_seconds=(3 * 864_000) if notify_expiry else None,
            max_downloads=max_downloads,
        )

    if recipients:
        for email in recipients:
            api.set_transfer_recipient(xh, email, schedule=schedule)

    api.close_transfer(xh)

    return UploadResult(
        xh=xh,
        url=f"{SHARE_BASE}/t/{xh}",
        title=title,
        total_bytes=total_bytes,
        file_count=len(files),
        folder_count=len(dir_rel_paths),
    )
