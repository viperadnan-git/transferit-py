<h1 align="center">transferit</h1>

<p align="center">
  <em>Upload and download files on <a href="https://transfer.it">transfer.it</a> from Python or your terminal — no browser, no MEGA account, no external CLI tools.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/transferit-py/"><img src="https://img.shields.io/pypi/v/transferit-py.svg?color=007aff" alt="PyPI" /></a>
  <a href="https://pypi.org/project/transferit-py/"><img src="https://img.shields.io/pypi/pyversions/transferit-py.svg?color=007aff" alt="Python" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-007aff.svg" alt="License" /></a>
</p>

```python
from transferit import Transferit

with Transferit() as tx:
    url = tx.upload("report.pdf").url     # → https://transfer.it/t/…
```

- **End-to-end encrypted** — AES-128-CTR + CBC-MAC, identical scheme to the official web client.
- **Concurrent WebSocket uploads** — up to 8 connections per file, matching MEGA's `ulDefConcurrency`.
- **Streaming download + decrypt** via `httpx.stream` — constant memory regardless of file size.
- **Folders** — uploaded recursively, hierarchy preserved both ways.
- **Every form field wired up** — title, message, password, sender, expiry, notify-on-expiry, max-downloads, recipients, scheduled delivery.
- **Rich CLI & library API** — the same four verbs (`upload` / `download` / `info` / `metadata`) in either surface, with `--json` everywhere for scripting.

## Install

**As a CLI** — global `transferit` command on your PATH:

```bash
uv tool install "transferit-py[cli]"      # recommended
pipx install "transferit-py[cli]"         # alternative
```

**As a library** — inside a project:

```bash
uv add transferit-py                      # library only
uv add "transferit-py[cli]"               # library + CLI entry point
pip install transferit-py                 # or pip, same story
pip install "transferit-py[cli]"
```

Requires **Python 3.11+**.

## CLI

| Command | Purpose |
|---|---|
| `transferit upload PATH` | Upload a file or folder; print the share link |
| `transferit download LINK` | Mirror a transfer into a local directory |
| `transferit info LINK` | Metadata panel + file/folder tree |
| `transferit metadata LINK` | Metadata panel only |

Every command supports `--json` for machine-readable output. `LINK` can be either a full share URL or the bare 12-character handle.

```bash
# simplest — one file, no options
transferit upload report.pdf

# folder upload, 7-day expiry
transferit upload ./project -e 7d --sender me@example.com

# full "Send files" mode
transferit upload big.mp4 \
    --title "Q1 demo"      --message "review please" \
    --password hunter2     --sender me@example.com  \
    --expiry 7d            --notify-expiry          \
    --max-downloads 50                              \
    -r alice@example.com -r bob@example.com         \
    --schedule 2026-04-25T09:00
```

`--expiry` accepts any duration from `1s` to `10y`: `30s`, `5m`, `2h`, `7d`, `1w`, `1y`, or compound forms like `1y6m3d`. Run `transferit <command> --help` for the full flag list.

## Library

The entire surface is a single class — **`Transferit`**. One instance owns an HTTP connection pool and (lazily) an anonymous ephemeral MEGA account that's reused across every write-side call.

```python
from transferit import Transferit

with Transferit(default_sender="me@example.com", default_expiry="7d") as tx:
    result = tx.upload("./q1.pdf", title="Quarterly report")
    # => UploadResult(xh=…, url=…, title=…, total_bytes=…, file_count=1, folder_count=0)

    meta   = tx.metadata(result.url)       # → TransferInfo
    nodes  = tx.info(result.url)           # → list[TransferNode]
    tx.download(result.url, "./dl")        # → DownloadResult
```

| Method | Returns | Needs session? |
|---|---|---|
| `tx.upload(path, **opts)` | `UploadResult` | yes (lazily created) |
| `tx.download(link, dir, **opts)` | `DownloadResult` | no |
| `tx.info(link, password=…)` | `list[TransferNode]` | no |
| `tx.metadata(link, password=…)` | `TransferInfo` | no |

**`upload()`** accepts every web-form field as a kwarg — `title`, `message`, `password`, `sender`, `expiry` (int seconds or duration string), `notify_expiry`, `max_downloads`, `recipients`, `schedule`, `concurrency`, and three progress callbacks (`on_progress`, `on_file_start`, `on_file_done`).

### Typed returns

`TransferInfo`, `TransferNode`, `UploadResult`, `DownloadResult` are frozen dataclasses. Every one has a `.to_json_dict()` for serialisation; `TransferNode` also exposes `.is_file` / `.is_folder`. Raw server responses are kept on `.raw` as an escape hatch.

```python
meta = tx.metadata(url)
print(meta.title, meta.total_bytes, meta.password_protected)
```

### Progress hooks

```python
def on_progress(sent: int, total: int) -> None:
    print(f"{sent/total:6.1%}  {sent:>12,} / {total:,} bytes")

tx.upload("./big.mp4", on_progress=on_progress)
```

`tx.download` exposes richer callbacks: `on_start`, `on_file_start`, `on_file_progress`, `on_file_done`, `on_skip`.

### Defaults

`Transferit(default_sender=…, default_expiry=…, default_concurrency=…)` — any of these is used when a per-call kwarg is omitted.

### Low-level

Advanced callers can use `MegaAPI` directly — every MEGA command (`create_ephemeral_session`, `create_transfer`, `fetch_transfer`, `set_transfer_attributes`, …) is a method on it. Inject via `Transferit(api=MegaAPI(...))` for testing or pinning to an alternate endpoint.

## How it works

transfer.it is a thin white-label on top of MEGA's `bt7` API cluster. Each upload spins up an anonymous ephemeral MEGA account, creates a transfer container, streams AES-128-CTR-encrypted chunks over WebSockets (with CBC-MAC integrity checks), and finalises the node list via a proprietary `x*` command family. Full protocol reference — API verbs, crypto scheme, WebSocket framing, password derivation — lives in [`docs/REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md).

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

**transferit is an independent, community-maintained project.** It is not
affiliated with, endorsed by, sponsored by, or otherwise connected to
MEGA Limited, transfer.it, or any of their subsidiaries. All product
names, logos, and brands are property of their respective owners.

This project interoperates with transfer.it by re-implementing the
publicly-served, client-side JavaScript that the official web client
ships to every visitor — no proprietary SDK, no private endpoint, no
circumvention of any access control. The protocol write-up in
[`docs/REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md) is provided
purely for educational and interoperability purposes.

**Before using transferit**, make sure your usage complies with:

- [transfer.it's terms of service](https://transfer.it/terms)
- [MEGA's terms of service](https://mega.io/terms)
- Any export-control, data-protection, or local laws that apply to you

You are solely responsible for the content you upload and the transfers
you access through this library. The maintainers accept no liability for
misuse, data loss, service disruption, account termination, or any other
consequence arising from use of this software. The software is provided
"as is", without warranty of any kind — see [`LICENSE`](LICENSE) for the
full legal text.

If you are a rights-holder and believe this project infringes on your
rights, please open an issue on the project's repository before taking
any other action — most concerns can be resolved directly.
