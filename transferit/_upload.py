"""
WebSocket upload pipeline.

Fan-out matches MEGA's ``WsPoolMgr`` / ``WsUploadMgr`` behaviour from
``bdl4.js``: one pool per file (picked by size from ``usc``), up to
``DEFAULT_CONCURRENCY`` concurrent WS connections per pool, shared
chunk queue, ``bufferedAmount < 1.5 MB`` back-pressure gate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import websockets

from ._api import MegaAPIError
from ._crypto import (
    CHUNKMAP,
    ONE_MB,
    crc32b,
    encrypt_chunk_and_mac,
)

if TYPE_CHECKING:
    from ._api import MegaAPI

log = logging.getLogger(__name__)

# Matches ulmanager.ulDefConcurrency in bdl4.js — WS fan-out per pool.
DEFAULT_CONCURRENCY: int = 8

# Matches the ws.bufferedAmount < 1_500_000 gate in bdl4.js sendchunk().
WS_BUFFER_LIMIT: int = 1_500_000


# ---------- chunking ----------


def iter_chunks(size: int) -> tuple[list[tuple[int, int]], bool]:
    """Compute MEGA chunkmap offsets/lengths and whether an empty tail frame is needed."""
    chunks: list[tuple[int, int]] = []
    pos = 0
    truncated_last = False
    while pos < size:
        nominal = CHUNKMAP.get(pos, ONE_MB)
        remaining = size - pos
        if remaining < nominal:
            chunks.append((pos, remaining))
            pos += remaining
            truncated_last = True
        else:
            chunks.append((pos, nominal))
            pos += nominal
            truncated_last = False
    need_empty_tail = (size == 0) or (not truncated_last)
    return chunks, need_empty_tail


# ---------- single-file WebSocket upload ----------


async def _ws_upload_one(
    ws_host: str,
    ws_uri: str,
    path: Path,
    ul_key: list[int],
    *,
    fileno: int = 1,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress=None,
) -> tuple[bytes, list[list[int]]]:
    """
    Upload a single file across up to ``concurrency`` WebSockets.  Returns
    ``(completion_token, chunk_macs_ordered_by_offset)``.

    ``fileno`` must be unique per file within a MEGA session — the server
    tracks upload state by ``(session, fileno)``, not per WS connection.
    """
    url = f"wss://{ws_host}/{ws_uri}"
    size = path.stat().st_size

    chunk_offsets, need_empty_tail = iter_chunks(size)
    work_queue: list[tuple[int, int]] = list(chunk_offsets)
    if need_empty_tail:
        work_queue.append((size, 0))

    q_lock = asyncio.Lock()
    file_lock = asyncio.Lock()
    macs_by_offset: dict[int, list[int]] = {}
    completion_token: list[bytes | None] = [None]
    done = asyncio.Event()
    bytes_acked = [0]
    lengths_by_offset = dict(chunk_offsets)
    progress_lock = asyncio.Lock()

    fh = path.open("rb")

    async def _take_chunk() -> tuple[int, int] | None:
        async with q_lock:
            if not work_queue:
                return None
            return work_queue.pop(0)

    async def _read_and_encrypt(pos: int, length: int) -> bytes:
        async with file_lock:
            fh.seek(pos)
            data = fh.read(length)
        ct, mac = encrypt_chunk_and_mac(data, ul_key, pos)
        macs_by_offset[pos] = mac
        return ct

    async def _handle_message(mview: bytes) -> None:
        if len(mview) < 9:
            return
        body, mcrc = mview[:-4], struct.unpack_from("<I", mview, len(mview) - 4)[0]
        if crc32b(body) != mcrc:
            raise MegaAPIError("ws CRC mismatch on server msg")
        mtype = struct.unpack_from("<b", body, 12)[0]
        if mtype < 0:
            raise MegaAPIError(f"server signalled upload error type={mtype}")
        mpos = struct.unpack_from("<Q", body, 4)[0]
        if mtype in (1, 7):
            length = lengths_by_offset.get(mpos, 0)
            async with progress_lock:
                bytes_acked[0] += length
                if progress:
                    progress(min(bytes_acked[0], size), size)
        elif mtype == 3:
            raise MegaAPIError(f"server reports chunk CRC fail at offset {mpos}")
        elif mtype == 4:
            tlen = body[13]
            completion_token[0] = bytes(body[14 : 14 + tlen])
            done.set()

    async def worker(worker_id: int) -> None:
        try:
            async with websockets.connect(
                url, max_size=None, ping_interval=20, ping_timeout=60
            ) as ws:

                async def recv_loop() -> None:
                    async for msg in ws:
                        if isinstance(msg, (bytes, bytearray)):
                            await _handle_message(bytes(msg))
                            if done.is_set():
                                return

                recv_task = asyncio.create_task(recv_loop())
                try:
                    while not done.is_set():
                        chunk = await _take_chunk()
                        if chunk is None:
                            break

                        pos, length = chunk

                        while True:
                            transport = getattr(ws, "transport", None)
                            buffered = (
                                getattr(transport, "_buffer_size", 0)
                                if transport
                                else 0
                            )
                            if buffered < WS_BUFFER_LIMIT or done.is_set():
                                break
                            await asyncio.sleep(0.01)

                        if done.is_set():
                            break

                        ct = await _read_and_encrypt(pos, length)
                        header = bytearray(20)
                        struct.pack_into("<I", header, 0, fileno)
                        struct.pack_into("<Q", header, 4, pos)
                        struct.pack_into("<I", header, 12, length)
                        struct.pack_into(
                            "<I", header, 16, crc32b(ct, crc32b(bytes(header[:16])))
                        )
                        await ws.send(bytes(header))
                        if ct:
                            await ws.send(ct)

                    try:
                        await asyncio.wait_for(done.wait(), timeout=120)
                    except asyncio.TimeoutError:
                        pass
                finally:
                    if not recv_task.done():
                        recv_task.cancel()
                        try:
                            await recv_task
                        except (asyncio.CancelledError, Exception):
                            pass
        except Exception:
            done.set()
            raise

    try:
        n = max(1, min(concurrency, len(work_queue)))
        workers = [asyncio.create_task(worker(i)) for i in range(n)]
        results = await asyncio.gather(*workers, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                raise r
    finally:
        fh.close()

    if completion_token[0] is None:
        raise MegaAPIError("upload ended without completion token")

    ordered_macs = [macs_by_offset[o] for o in sorted(macs_by_offset)]
    return completion_token[0], ordered_macs


# ---------- folder walker ----------


def walk_folder(root: Path) -> tuple[list[Path], list[str]]:
    """
    Walk a folder (non-symlink-following), return:
        files          — regular files, deterministic order
        dir_rel_paths  — sub-directories as POSIX-style relative paths,
                          parent-before-child, ready to feed to mkdir

    Empty folders are preserved.  Hidden / dotfiles are included.
    """
    if not root.is_dir():
        raise NotADirectoryError(root)

    files: list[Path] = []
    dir_set: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        base = Path(dirpath)
        rel = base.relative_to(root)
        if str(rel) != ".":
            dir_set.add(rel.as_posix())
        for fn in filenames:
            files.append(base / fn)

    dir_rel_paths = sorted(dir_set, key=lambda s: (s.count("/"), s))
    return files, dir_rel_paths


def build_remote_tree(
    api: MegaAPI, root_handle: str, dir_rel_paths: list[str]
) -> dict[str, str]:
    """Materialise a remote folder tree; return ``rel_posix_path → handle``."""
    handles: dict[str, str] = {"": root_handle}
    for rel in dir_rel_paths:  # parents precede children
        parent_rel, _, name = rel.rpartition("/")
        parent_h = handles.get(parent_rel, root_handle)
        handles[rel] = api.create_subfolder(parent_h, name)
    return handles
