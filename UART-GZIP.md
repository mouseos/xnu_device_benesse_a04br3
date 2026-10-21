# Optional gzip boot transport

Source: `tools/uart-console.c`. This is an explicit optional transport above
unchanged `/dev/a04boot` ABI v1. Existing raw commands and PID1/native launch,
console supervisor, termios and UART ownership are unchanged. No startup libz
load or `-lz` dependency. Only `boot-zbegin` loads `/usr/lib/libz.1.dylib` with
`dlopen` and resolves public `zlibVersion`, `inflateInit2_`, `inflate`,
`inflateEnd`, `crc32`. Absent/incompatible library/symbols return Darwin
ENOTSUP (45), leaving raw available. SDK `<zlib.h>` supplies real types;
compile-time field/layout/callback type checks reject incompatible ABI.

```
@A04 ID boot-zbegin COMPRESSED_BYTES IMAGE_BYTES RAW_SHA256_HEX64
@A04 ID boot-zdata COMPRESSED_OFFSET CHUNK_CRC32_HEX8 BASE64
@A04 ID boot-zfinish
@A04 ID boot-commit
```

Every request ends with newline, IDs are at most32 alphanumeric characters.
`boot-zdata` has at most384 decoded bytes (512 base64 characters). Chunk CRC32
is zlib/IEEE CRC32 of exactly those compressed bytes, eight hex digits, not a
running CRC. Check CRC/offset before inflate. Responses are the existing
`!A04 ID OK ...` / `!A04 ID ERR ERRNO` protocol with a preceding newline.
Begin replies `offset=0 raw=0`. Data/finish reply `offset=COMPRESSED_ACK raw=STAGED`.
Host must check both monotonically bounded offsets; use compressed offset for
next chunk, not raw offset. Exact same ID+line retry returns cached response
without repeated inflate/write/commit. Reuse of last ID for another line fails.
One outstanding request is required, as for raw transport.

Input policy: one gzip member only (`windowBits=31`), no raw deflate/zlib,
concatenated members or trailing bytes. Compressed length18..129MiB; image
length6144..128MiB and2048-aligned, additionally checked against actual kernel
partition size. Unknown INFO version or nonzero kernel flags rejects begin.
An existing local stage rejects optional begin with EBUSY (16), without load,
reset, close or write. Explicit `boot-abort` is required before switching mode.
Library/init/open/INFO/BEGIN failures do not replace or write old staging.

CRC/offset/base64 errors before inflate are retryable with a new ID and correct
same-offset data. Inflate/data/trailer errors, output overrun, no progress with
input, premature/truncated END, partial write followed by error, or zero write
mark FAILED, release inflate/libz, close/discard staging and block COMMIT/reboot.
ABORT/reset releases libz even on an incomplete stream. Complete END releases
inflate/libz immediately; `boot-zfinish` still checks exact counts and is
mandatory before COMMIT. No implicit flash or reboot occurs. After failed COMMIT
reboot is blocked. New explicit BEGIN after recovery or ABORT is required.

The existing kernel still validates the full **uncompressed** SHA256, Android/
MTK header and bounds, GPT, writes, flushes and reads back/re-hashes/flushes
before returning success. A gzip ACK is not image validation or a hardware
write acknowledgment. Explicit `reboot` remains separate, permitted after
verified COMMIT (or with no upload), and only executes after its full ACK.

Build/pinning: no new required helper header/source. Parent pins the changed
`tools/uart-console.c` and `tools/uart-libSystem.tbd` with existing
`tools/build-uart-console.sh` in kernel `build-support/uart-console-source.json`
after the device commit; do not publish new hashes against an old commit.
`tests/test_uart_console_gzip.py` and this document are independent source/tests.
Actual iPhoneOS SDK build and native libz availability require parent validation.

Provenance: independent BSD-2-Clause-Patent transport implementation. Public
zlib API/format contract from https://zlib.net/manual.html (checked2026-10-22),
zlib authors Jean-loup Gailly and Mark Adler; no zlib implementation or Apple
binary bytes copied. SDK headers are compile inputs, not vendored source.
