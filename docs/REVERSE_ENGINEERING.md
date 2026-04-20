# Reverse-engineering transfer.it

A field-tested walkthrough of the transfer.it upload/download protocol —
enough detail that, if the site rotates bundle hashes or tweaks its framing,
you can re-derive everything with a one-shot `curl` and a text editor.

> **TL;DR** — `transfer.it` is a thin white-label on top of MEGA.nz's
> storage infrastructure, talking to `https://bt7.api.mega.co.nz/`.  The
> proprietary bits are (a) a handful of `x`-prefixed API commands that
> create/manage transfer containers and (b) a MEGA Limited Code Review
> Licensed WebSocket upload protocol.  Files are encrypted client-side
> with AES-128-CTR + a CCM-style CBC-MAC; the transfer URL
> (`https://transfer.it/t/<xh>`) carries **only** a 12-character handle —
> the decryption key is stored server-side in plaintext, so anyone with
> the URL can download.

---

## 1. Site anatomy

### 1.1 Entry point

```
GET https://transfer.it/start
```

returns a 15-line HTML shell whose only script is
`/secureboot.js?x=<cachebuster>`.  That single file defines:

- the build metadata (`buildVersion`),
- the **static CDN base URL** — `https://st.transfer.it/` — where all JS
  bundles live,
- the **API base URL** — `https://g.api.mega.co.nz/` by default, but
  overridden to `https://bt7.api.mega.co.nz/` for transfer.it flows (see
  `T.core.apipath` in `transferit-group1.js`),
- a `jsl3.transferit` manifest pointing at the two transfer.it-specific
  bundles and an HTML templates bundle.

### 1.2 Bundles to inspect

```
https://st.transfer.it/secureboot.js?x=<buster>
https://st.transfer.it/js/BDL-1_<hash>.js       ← crypto primitives (prepare_key, encrypt_key, a32)
https://st.transfer.it/js/BDL-2_<hash>.js       ← storage, quota UI
https://st.transfer.it/js/BDL-3_<hash>.js       ← API layer (api.req, api.screq, getsid), sessions, xh parsing
https://st.transfer.it/js/BDL-4_<hash>.js       ← WebSocket upload manager (mega.wsuploadmgr), ulFinalize
https://st.transfer.it/js/transferit-group1_<hash>.js  ← T.core (xn/xp/xc/xd/xl/xv/xm/xr/xrf)
https://st.transfer.it/encrypter.js             ← worker: AES-CTR + CBC-MAC via aesasm
https://st.transfer.it/aesasm.js                ← asm.js AES primitive
```

Hashes rotate on deploy; grab a fresh manifest with:

```bash
curl -sL https://transfer.it/start | grep -oE '/secureboot\.js\?x=[^"]+'
curl -sL "https://st.transfer.it/secureboot.js?x=..." \
  | grep -oE "js/(BDL-[0-9]+|transferit-group[0-9]+)_[0-9a-f]{64}\.js"
```

### 1.3 Why is_transferit matters

Several code paths branch on `self.is_transferit`.  When searching the
bundles for transfer-specific code, grep for `is_transferit`, `xput`,
`bt7.api.mega.co.nz`, and the `xn/xp/xc/xd/xl/xv/xm/xr/xrf` command codes.

---

## 2. The API surface (`https://bt7.api.mega.co.nz/cs`)

All API calls are `POST /cs?id=<seqno>[&sid=<session>][&x=<xh>][&pw=<pbkdf2>]`
with a JSON **array** body (each element is a request; the response is a
parallel array).  A bare JSON integer in the response is a global error
(`-3` = EAGAIN — retry, `-14` = EKEY — wrong/missing password, `-15` = ESID
— invalid session, etc.).

### 2.1 Generic MEGA commands (needed for anonymous access)

| cmd  | request                                                                 | response                                              | notes                                                      |
|------|-------------------------------------------------------------------------|-------------------------------------------------------|------------------------------------------------------------|
| `up` | `{a:"up", k:<b64(enc_pw_key(u_k))>, ts:<b64(ssc ‖ enc_u_k(ssc))>}`       | `"Xxxxxxxxxxx"` (11-char user handle)                 | creates ephemeral account                                  |
| `us` | `{a:"us", user:<handle>}`                                               | `{k:<enc(u_k)>, tsid:<b64(16B ssc ‖ 16B enc_u_k(ssc))>}` | anonymous session, `tsid` becomes the `sid` query arg      |
| `g`  | `{a:"g", n:<node_handle>, g:1, ssl:1}`                                  | `{g:<dl_url>, s:<size>, at:<enc_attrs>, fa:<...>}`    | download URL per file                                      |
| `f`  | `{a:"f", c:1, r:1}` + `?x=<xh>`                                         | `{f:[ {h,p,t,a,k,s,ts}, ... ]}`                        | list transfer contents                                     |
| `ug` | `{a:"ug"}`                                                              | user attrs                                            | not used for the anonymous uploader                        |
| `usc`| `{a:"usc"}`                                                             | `[[host,uri,sizelimit], ..., [host,uri]]`             | **upload pool directory** — WS endpoints                   |

### 2.2 Transfer-container commands (the `x*` family)

| cmd   | request                                           | response                    | notes                                                |
|-------|---------------------------------------------------|-----------------------------|------------------------------------------------------|
| `xn`  | `{a:"xn", at:<enc_attrs>, k:<b64 folder_key>}`    | `[xh, root_h]`              | create transfer                                      |
| `xp`  | `{a:"xp", t:<root_h>, n:[{t:0,h,a,k}]}`           | `{f:[{h,p,t,s,ts,...}]}`    | attach a freshly-uploaded node (like MEGA's `p`)     |
| `xc`  | `{a:"xc", xh}`                                    | numeric                     | close the transfer (makes it read-only)              |
| `xd`  | `{a:"xd", xh}`                                    | numeric                     | delete a transfer                                    |
| `xl`  | `{a:"xl"}`                                        | `[{xh, ct, h, a, k, size}]` | list transfers owned by the current session          |
| `xi`  | `{a:"xi", xh}`                                    | transfer meta               | fetch transfer settings — see §2.3.1                 |
| `xm`  | `{a:"xm", xh, t, e, m, pw, se, en, mc}`           | numeric                     | set title, expiry, message, password, sender, etc.   |
| `xv`  | `{a:"xv", xh, pw}`                                | `1` on success              | validate password (PBKDF2 result, see §4.3)          |
| `xr`  | `{a:"xr", xh, rh, ex, e, s}`                      | numeric                     | set recipient(s) and schedule                        |
| `xrf` | `{a:"xrf", xh}`                                   | recipient array             | list recipients                                      |

#### 2.2.1 `xi` is dual-mode — `x=` query param matters

The `xi` endpoint has two distinct behaviours depending on how the
transfer handle reaches it:

| Request                                                                  | Auth model          | Behaviour on pw-protected transfer  |
|--------------------------------------------------------------------------|---------------------|-------------------------------------|
| `POST /cs?id=N` with `[{"a":"xi","xh":"<xh>"}]`  *(xh in body only)*     | anonymous peek      | Returns basic metadata **without pw** — title, sender, size aggregate, `pw:1` flag. |
| `POST /cs?id=N&x=<xh>` with the same body                                | access-scoped       | Requires valid `pw=<pbkdf2>` token alongside; otherwise `-14/EKEY`.  |

The web client's `T.core.getTransferInfo(xh)` uses the first form (no
`x=`) — that's what populates the landing page's title / sender / size
preview on a password-protected transfer before the visitor enters the
password.  `f` and `g` have no such duality: they always require the
`x=` query param, which in turn enforces the password.

Practical consequence for clients: **fetch `xi` without attaching `x=`**
unless you deliberately want the access-gate behaviour.  Attaching `x=`
with `xh` in body re-activates `-14/EKEY` for password-protected
transfers.

### 2.3 URL shape

```
https://transfer.it/t/<xh>
                      └─ 12-char base64url handle (9 raw bytes)
```

No decryption key in the URL.  The server stores file keys **in the clear**
and returns them via `f`/`g` calls keyed only on `xh` — that is the whole
point of the transfer-container design, and the main way it diverges from
regular MEGA folder links.

### 2.4 The two web forms (`Create link` vs `Send files`)

The front page hosts a single upload form with a 2-option segmented
control (`input[name="glb-manage-sgm"]`):

| segment value | mode                | extra fields exposed                     |
|---------------|---------------------|------------------------------------------|
| `0` (default) | **Create link**     | — (just upload and return the URL)       |
| `1`           | **Send files**      | recipients (email chips) + schedule date |

Both modes share the same settings panel.  Every form field maps onto
one of the API verbs above:

| HTML element                  | API field            | command | notes                                                   |
|-------------------------------|----------------------|---------|---------------------------------------------------------|
| `#glb-title-input`            | `t` (transfer title) | `xn` / `xm` | used as the folder name at `xn` time; resent via `xm` when other settings change |
| `#glb-email-input`            | `se` (sender email)  | `xm`    | required whenever message/password/expiry/recipient is set |
| `#glb-msg-area`               | `m` (message body)   | `xm`    | shown on the transfer landing page                      |
| `#glb-password-input`         | `pw`                 | `xm`    | raw string on the wire is PBKDF2(pw, salt_from_xh)      |
| `input[name="glb-expire-radio"]` | `e`               | `xm`    | **duration in seconds** (NOT absolute unix ts); web UI offers `{0, 7, 30, 90, 180, 365}` days; client multiplies days<1000 by 86400.  `e=0` is rejected by the server (-11/EACCESS) — to disable expiry, simply omit the `e` field.  Empirically the server accepts ≤ ~2^32 seconds (~136 y), with spurious -11 at exact powers-of-2 (2^32, 2^33, 2^40, 2^50, 2^53); a sensible client cap is 1 day to 10 years. |
| `input[name="gbl-exp-notif"]` | `en`                 | `xm`    | set only when `e > 0 AND sender`; value is seconds-before-expiry (30 days default) |
| `input[name="gbl-dl-notif"]`  | *(unused)*           | —       | UI-only toggle in the current build; no API field       |
| `#glb-recipients-input`       | `e`                  | `xr`    | one `xr` call per recipient email                       |
| `#glb-scheduled-input`        | `s`                  | `xr`    | unix timestamp — delays the delivery email              |

The field `mc` (max downloads) accepted by `xm` is not surfaced on the
current web UI but is accepted by the API and honoured by the server.

The JS client calls `xm` only when at least one of
`sender/message/password/expiry` was set — if the user just types a
title and clicks "Get link", no `xm` happens.  Mirror that behaviour in
any client you build.

---

## 3. Crypto primitives (identical to the rest of MEGA)

### 3.1 Byte-order conventions

- `a32` = array of uint32s, **big-endian** on the wire
  (`struct.pack('>I', …)` in Python).
- `b64url` = RFC 4648 base64url **without** padding.
- `encrypt_key(aes_key, data_a32)` = AES-ECB encrypt each 16-byte slice
  of `data_a32`.  `decrypt_key` is the inverse.

### 3.2 Node attributes

```
plaintext = b"MEGA" + json.dumps({"n": name, ...}, separators=(',',':'))
padded    = zero-pad to 16-byte boundary
ciphertext = AES_CBC_encrypt(key=attr_key, iv=0, padded)
```

where `attr_key` is the **XOR-reduced** node key:

```
attr_key = [k[0]^k[4], k[1]^k[5], k[2]^k[6], k[3]^k[7]]
```

For 4-element folder keys the reduction is a no-op (the missing tail is
treated as zeros).  For files the full 8-element filekey collapses into a
16-byte AES key.

Canary: after decrypt+strip-zeros, valid output starts with `MEGA{"`.

### 3.3 File encryption (per chunk)

For each file, generate a **6-uint32 upload key** `ul_key`:

- `ul_key[0..3]` → 128-bit AES key
- `ul_key[4..5]` → 64-bit nonce

Then per chunk of up to 1 MiB located at byte offset `pos`:

| item          | definition                                           |
|---------------|------------------------------------------------------|
| CTR IV        | `nonce ‖ counter_be64`  where `counter = pos // 16`  |
| ciphertext    | `AES_128_CTR(ul_key[0..3], CTR IV, plaintext)`       |
| MAC IV        | `nonce ‖ nonce`  (16 bytes)                          |
| CBC-MAC state | start from MAC IV, XOR-then-AES each 16-byte block   |
| chunk MAC     | the final AES output (4 × uint32)                    |

Fast-path implementation: the CBC-MAC equals the **last 16-byte block of
an AES-CBC encryption** of the (zero-padded) plaintext using `mac_iv`, so
you can lift it straight out of any standard crypto library.

### 3.4 Condensing MACs → filekey

After every chunk is ingested, collect the per-chunk MACs in offset
order and fold them:

```python
acc = [0, 0, 0, 0]
for mac in macs_in_offset_order:      # each mac is 4 uint32s
    acc = [acc[i] ^ mac[i] for i in range(4)]
    acc = AES_ECB_encrypt(ul_key[:4], acc)
```

The final `acc` gives the **file MAC**, which is mixed into the filekey:

```
filekey = [
    ul_key[0] ^ ul_key[4],
    ul_key[1] ^ ul_key[5],
    ul_key[2] ^ mac[0] ^ mac[1],
    ul_key[3] ^ mac[2] ^ mac[3],
    ul_key[4],
    ul_key[5],
    mac[0] ^ mac[1],
    mac[2] ^ mac[3],
]
```

Send `a32_to_b64(filekey)` as the `k` field of the `xp` request.  The
server stores it verbatim; download clients will just use
`attr_key(filekey)` + `nonce = filekey[4..6]` to decrypt.

---

## 4. The upload flow, step by step

### 4.1 Anonymous session

```
A  ← random a32[4]                       # u_k (master key)
P  ← random a32[4]                       # password key
S  ← random a32[4]                       # session self-challenge

POST /cs?id=1   [{a:"up", k:b64(encrypt_key(P, A)), ts:b64(S ‖ encrypt_key(A, S))}]
                →  "HuM3dSk-mY"         # 11-char user handle

POST /cs?id=2   [{a:"us", user:"HuM3dSk-mY"}]
                →  { k:"…", tsid:"…" }  # tsid is 32 bytes b64url

# verify: encrypt_key(A, tsid[0:16]) == tsid[16:32]
sid = tsid                                # used as ?sid=… on all subsequent calls
```

### 4.2 Create the transfer container

```
folder_key = random a32[4]
attrs      = {"name": "My transfer", "mtime": <epoch>}
at         = b64(AES_CBC(attr_key(folder_key), IV=0, pad16("MEGA"+json(attrs))))

POST /cs?id=3&sid=…  [{a:"xn", at, k:a32_to_b64(folder_key)}]
                     → ["xh_12chars", "root_8ch"]
```

### 4.3 Optional: apply form fields (`xm`)

Issue **one** `xm` call after `xp` if any of sender / message / password /
expiry / max-downloads / expiry-notification were set on the form.  The
JS client short-circuits if every field is empty — don't send a no-op
`xm` (the server doesn't care, but it's the observable web behaviour).

```python
# transferit.js createPassword()  (only if a password was set)
xh_raw = b64url_decode(xh)              # 9 bytes for a 12-char xh
salt   = xh_raw[-6:] * 3                # 18 bytes
pw_tok = b64url_encode(pbkdf2_hmac('sha256', password.strip().encode(), salt, 100_000, 32))

POST /cs?…  [{
    a:  "xm",
    xh,
    t:  b64url(title.encode("utf-8")),           # optional resend
    se: sender_email,                            # required if any other setting is non-null
    m:  b64url(message.encode("utf-8")),         # optional
    pw: pw_tok,                                  # optional (see above)
    e:  days * 86400,                            # or absolute unix seconds if > 1000
    en: 30 * 86400,                              # only if expiry > 0 AND sender set
    mc: 10,                                      # max downloads (hidden from the web UI)
}]
```

Validation rules inherited from the frontend:

- `pw`, `m`, `e > 0`, `en`, `recipients` **require** `se` (the sender's
  email) — the UI refuses to send without it.
- `en` **requires** `e > 0` — no point notifying about an expiry that
  never happens.
- `mc` is accepted by the server but not surfaced in the form; you can
  set it via the API without harm.

Recipients then must pass `pw=pw_tok` (same PBKDF2 result) on every
`f`/`g` request; `xv` is the dedicated validate endpoint.

### 4.3b Optional: add recipients (`xr`, Send-files mode only)

One `xr` call per recipient; the server returns `[0, rh]` where `rh` is
the recipient handle (used for `xrf` listings and later `xr` edits).

```
POST /cs?…  [{a:"xr", xh, e:"alice@example.com"[, s:<unix_ts>][, ex:<unix_ts>]}]
            → [0, "rh_11chars"]
```

`s` is the schedule (when to actually send the invitation mail); `ex` is
an execution window for retry.  Both default to "send immediately" on
the UI when the user leaves the schedule date blank.

### 4.4 Request the WebSocket pool

```
POST /cs?…  [{a:"usc"}]
            → [ [host1, uri1, sizelimit1], ..., [hostN, uriN] ]
```

Entries are ordered by ascending size class; the last entry (no
`sizelimit`) catches oversized files.  Pick the first pool whose
`sizelimit` is ≥ your file size (or the no-limit tail).

### 4.5 WebSocket framing

Open `wss://<host>/<uri>` (up to **8 connections per pool** — mirror of
`ulmanager.ulDefConcurrency = 8` in `bdl4.js`).

Client → server, per chunk:

```
struct {
  u32 fileno;        // client-assigned, monotonic within the session
  u64 pos;           // little-endian byte offset into the file
  u32 length;        // bytes of encrypted payload to follow
  u32 crc32b;        // CRC-32 (poly 0xEDB88320) over the first 16 bytes
} header;            // 20 bytes, sent as its own WS binary frame
encrypted_chunk      // sent as the immediately-following WS binary frame
```

The crc32b over `header[0..16]` is **seeded** with the crc of the payload:
`crc = crc32b(payload, crc32b(header[0..16]))`.  Use any zlib-compatible
CRC32 implementation — Python's `zlib.crc32` gives the right answer.

Ordering rules, reverse-engineered from `WsPoolMgr.sendchunk`:

- first 8 chunks follow the **chunkmap**: 128 KiB, 256 KiB, 384 KiB,
  512 KiB, 640 KiB, 768 KiB, 896 KiB, 1024 KiB (cumulative positions
  `0, 128K, 384K, 768K, 1.25M, 1.875M, 2.625M, 3.5M`);
- every chunk after that is exactly 1 MiB;
- the **last** chunk may be shorter (naturally signals EOF).  If the file
  size exactly matches a chunk boundary, send an additional zero-length
  chunk header at offset `size` — the server treats it as the
  size-confirmation frame;
- per connection, gate sends on `bufferedAmount < 1_500_000`.

Server → client, per WS message:

```
struct {
  u32 fileno;
  u64 pos;
  i8  type;        // see below
  u8  reserved_or_token_len;
  u8  token[type==4 ? reserved_or_token_len : 0];
  u32 crc32b;      // CRC-32 over everything before this field
}
```

Type codes:

| type | meaning                                                          |
|------|------------------------------------------------------------------|
| 1    | chunk ingested                                                   |
| 2    | chunk already on server (idempotent resend)                      |
| 3    | chunk CRC failed — client should retry                           |
| 4    | **upload complete**; `token` (36 bytes) is the completion handle |
| 5    | server overloaded — refresh pool URLs via a fresh `usc`          |
| 6    | backoff — pause for `pos` milliseconds                           |
| 7    | final chunk ingested (type-4 follows)                            |
| < 0  | upload error; magnitude maps to `EAGAIN/EFAILED/…`               |

Exactly one connection — whichever one sent the last chunk — receives the
type-4 frame with the 36-byte completion token.

### 4.6 Finalise into the transfer

```
mac     = condense_macs(chunk_macs_ordered_by_offset, ul_key)
filekey = [ul_key[0]^ul_key[4],
           ul_key[1]^ul_key[5],
           ul_key[2]^mac[0]^mac[1],
           ul_key[3]^mac[2]^mac[3],
           ul_key[4], ul_key[5],
           mac[0]^mac[1], mac[2]^mac[3]]

POST /cs?…  [{a:"xp",
              t: "<root_h>",
              n: [{t:0,
                   h: b64url(completion_token),     # 36 bytes, 48 chars
                   a: b64url(AES_CBC(attr_key(filekey), 0, pad16("MEGA"+json({"n":filename})))),
                   k: a32_to_b64(filekey)}]}]
            → {f: [{h:"…", …}]}                     # the new file node
```

### 4.7 Close (optional but recommended)

```
POST /cs?…  [{a:"xc", xh}]
```

The transfer becomes read-only; the share URL is immediately usable.

---

## 5. The download flow

### 5.1 List the transfer

```
POST /cs?id=1&x=<xh>[&pw=<pbkdf2>]  [{a:"f", c:1, r:1}]
→ { f: [
      {h:"AAAA", p:"",     t:1, a:"…", k:"22-char b64"},   # transfer root (folder)
      {h:"BBBB", p:"AAAA", t:1, a:"…", k:"…"},              # (nested folder)
      {h:"CCCC", p:"AAAA", t:0, a:"…", k:"43-char b64", s:1234, ts:…}, # file
      ...
    ] }
```

`n.a` and `n.k` are base64url.  Decrypt `n.a` with `attr_key(n.k_a32)` and
AES-CBC-IV-zero; the canary `MEGA{"` confirms a good key.

### 5.2 Get a download URL

```
POST /cs?…&x=<xh>[&pw=<pbkdf2>]  [{a:"g", n:"CCCC", g:1, ssl:1}]
→ { s: 1234,
    at: "<enc attrs>",          # same as f.a
    fa: "<file attrs>",         # thumbnail/preview descriptors
    g:  "https://gfs<xxx>.userstorage.mega.co.nz/dl/<token>",
    ip: [...], fh: "..." }
```

### 5.3 Stream-decrypt

```python
aes_key = attr_key(filekey_a32)        # the XOR-reduced node key
nonce   = a32_to_bytes(filekey_a32[4:6])
ctr     = Counter(64 bits, prefix=nonce, initial_value=0)
cipher  = AES.new(aes_key, AES.MODE_CTR, counter=ctr)

for chunk in HTTP_stream(download_url):
    sink.write(cipher.decrypt(chunk))
```

The download URL is a simple HTTP GET returning the raw AES-CTR
ciphertext.  No range/skip tricks are needed for a start-to-finish
download, but the ciphertext does support HTTP `Range` for resumes;
just remember to set `initial_value = start_byte // 16` when resuming
mid-stream.

### 5.4 Integrity

The file MAC embedded in `filekey[6..8]` lets you verify integrity after
download: recompute per-chunk CBC-MACs of the decrypted plaintext, run
`condense_macs`, and check `(mac[0]^mac[1], mac[2]^mac[3]) == filekey[6:8]`.
This is optional — the MEGA web client only does it on explicit "verify
integrity" UI actions.

---

## 6. Sample exchange (lightly redacted)

```
>>> POST https://bt7.api.mega.co.nz/cs?id=123456789
    [{"a":"up","k":"Uq…==","ts":"ab…"}]
<<< ["HuM3dSk-mY"]

>>> POST /cs?id=123456790  [{"a":"us","user":"HuM3dSk-mY"}]
<<< [{"k":"K_aJ…","tsid":"AAECAw…"}]

>>> POST /cs?id=123456791&sid=AAECAw…
    [{"a":"xn","at":"Qw…","k":"Zw…"}]
<<< [["dFc9CDghxLaS", "OXxjmTDC"]]       ← xh, root_h

>>> POST /cs?id=123456792&sid=AAECAw…  [{"a":"usc"}]
<<< [[["gws3.uploader…", "ul/abc", 104857600], ["gws5.uploader…", "ul/def"]]]

>>> WS wss://gws3.uploader…/ul/abc
    (client) <20B header><131072B payload>   ← chunk at pos=0
    (server) <13B header><4B CRC>            ← type=1 ack
    ...
    (client) <20B header>                    ← empty frame at pos=size
    (server) <50B payload>                   ← type=4, embeds 36-byte token

>>> POST /cs?id=123456793&sid=AAECAw…
    [{"a":"xp","t":"OXxjmTDC",
      "n":[{"t":0,"h":"<b64 token>","a":"<enc attrs>","k":"<enc key>"}]}]
<<< [{"f":[{"h":"yTpVnDzC","p":"OXxjmTDC","t":0,"a":"…","k":"…","s":64,"ts":…}]}]

>>> POST /cs?id=123456794&sid=AAECAw…  [{"a":"xc","xh":"dFc9CDghxLaS"}]
<<< [0]

>>> https://transfer.it/t/dFc9CDghxLaS      ← share this URL
```

---

## 7. Re-deriving this document after a site update

1. `curl -sL https://transfer.it/start`  — grab the shell, note the
   `secureboot.js` cachebuster.
2. `curl -sL https://st.transfer.it/secureboot.js?x=…` — look for
   `apipath`, `is_transferit`, the `jsl3.transferit` manifest, and any
   reference to `bt7.` hostnames.
3. Download each referenced JS bundle from `https://st.transfer.it/js/…`.
   Plain, un-minified, well-commented MEGA code — this is gold.
4. Grep for:
   - `api.req({a:` / `api_req({a:` — every API verb in use;
   - `ulmanager`, `wsuploadmgr`, `WsPool`, `FileUploadReader` — upload engine;
   - `T.core` / `sendAPIRequest` inside `transferit-group1.js` — the
     `x*` command wrappers;
   - `createPassword` — PBKDF2 parameters for password-protected
     transfers (confirm iterations haven't drifted from `1e5`).
5. Skim `encrypter.js` — **confirm** the MAC IV is still `nonce‖nonce`
   and CTR IV is still `nonce‖counter`, and that the CCM framing hasn't
   been swapped out.
6. Check the WS message types in `WsUploadMgr.process` — this is the
   surface that's most likely to evolve.  Any new codes (8+) show up as
   warnings in a running JS client and are safe to ignore for the single-
   file client path.
7. If `bt7.api.mega.co.nz` rotates, search secureboot for
   `.api.mega.co.nz` — the cluster that serves the transfer commands is
   referenced explicitly in `options.apipath` overrides.

This is enough to keep a third-party client functional through typical
iteration of the site; only a deep protocol redesign (e.g. moving the
upload layer to QUIC or introducing client-side key wrapping on `xp`)
would require a fresh pass.

---

## 8. File / code map (this repo)

- `transfer_it.py` — pure-Python client implementing everything above,
  with `upload` / `download` / `info` click subcommands.
- `REVERSE_ENGINEERING.md` — you are here.

Core helpers of interest inside `transfer_it.py`:

| function                       | purpose                                                   |
|--------------------------------|-----------------------------------------------------------|
| `MegaAPI`                      | `httpx`-based POST/JSON helper with seqno + sid handling  |
| `create_ephemeral_session`     | `up` + `us` flow, sets `api.sid`                          |
| `create_transfer`              | `xn` wrapper                                              |
| `_ws_upload_one`               | concurrent WS upload matching `ulDefConcurrency = 8`      |
| `encrypt_chunk_and_mac`        | AES-CTR + CBC-MAC (fast path via `AES_CBC` last block)    |
| `condense_macs`                | folds per-chunk MACs into the file MAC                    |
| `finalise_file`                | `xp` wrapper                                              |
| `fetch_transfer`               | `f` wrapper + attr decryption                             |
| `get_download_url`             | `g` wrapper                                               |
| `stream_decrypt_to_file`       | `httpx.stream` + AES-CTR decryption                        |
| `derive_transfer_password`     | PBKDF2-SHA256 for protected transfers                     |
| `set_transfer_attributes`      | `xm` wrapper (title/message/password/sender/expiry/mc)    |
| `set_transfer_recipient`       | `xr` wrapper (one call per recipient email)               |
