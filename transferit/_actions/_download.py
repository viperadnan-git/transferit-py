"""``Transferit.download`` implementation — stateless; accepts a MegaAPI."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .._api import MegaAPI
from .._download import compute_folder_paths, stream_decrypt_to_file
from .._models import DownloadResult, TransferNode


def do_download(
    api: MegaAPI,
    url_or_xh: str,
    output_dir: str | Path,
    *,
    password: str | None = None,
    force: bool = False,
    on_start: Callable[[list[TransferNode], int], None] | None = None,
    on_file_start: Callable[[TransferNode, Path], None] | None = None,
    on_file_progress: Callable[[TransferNode, int, int], None] | None = None,
    on_file_done: Callable[[TransferNode, Path], None] | None = None,
    on_skip: Callable[[TransferNode, Path], None] | None = None,
) -> DownloadResult:
    """
    Mirror a transfer into ``output_dir``.  Folder hierarchy is recreated.
    Existing files are skipped unless ``force=True``.
    """
    xh = MegaAPI.parse_xh(url_or_xh)
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    node_dicts, pw_token = api.fetch_transfer(xh, password=password)
    nodes = [TransferNode.from_dict(n) for n in node_dicts]

    root = next((n.handle for n in nodes if n.is_folder and not n.parent), None)
    folder_paths = compute_folder_paths(node_dicts, root) if root else {}

    files = [n for n in nodes if n.is_file]
    total_bytes = sum(n.size or 0 for n in files)
    if on_start:
        on_start(files, total_bytes)

    paths: list[str] = []
    skipped: list[str] = []

    for n in files:
        rel = folder_paths.get(n.parent, "")
        out_path = out_root / rel / (n.name or n.handle)
        paths.append(str(out_path))

        if out_path.exists() and not force:
            skipped.append(str(out_path))
            if on_skip:
                on_skip(n, out_path)
            continue

        dl = api.get_download_url(xh, n.handle, pw_token=pw_token)
        size = dl["s"]

        if on_file_start:
            on_file_start(n, out_path)

        def _cb(d: int, t: int, _n=n) -> None:
            if on_file_progress:
                on_file_progress(_n, d, t)

        stream_decrypt_to_file(dl["g"], out_path, n.key, size, on_progress=_cb)

        if on_file_done:
            on_file_done(n, out_path)

    return DownloadResult(
        xh=xh,
        output_dir=str(out_root),
        paths=paths,
        skipped=skipped,
        total_bytes=total_bytes,
    )
