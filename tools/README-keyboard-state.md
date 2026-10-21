# Passive UIKit keyboard-state reader

`a04br3_keyboard_state.c` is an original source-built diagnostic for the original
UIKitCore in iOS 16.7.16 / 20H392, UUID
`1242978a-2c2c-3781-8d6c-9777edce2804`. It does not repair keyboard presentation.
The adjacent `a04br3_keyboard_state_sources.json` retains the original artifact
SHA-256, unslid image address, REA evidence IDs and exact ivar metadata. This is
layout provenance, not an Apple binary dependency shipped in this repository.
No extracted executable, framework, donor code or opaque payload is included.

## Build and optional delivery

On the build Mac with the iPhoneOS SDK, clang, codesign and shasum installed:

```sh
bash tools/build-a04br3-keyboard-state.sh /absolute/output/a04br3-keyboard-state
```

The script compiles ARM64 iOS source, ad-hoc signs and strictly verifies it before
replacing the output. It requests no policy-bypass entitlements. Its SHA-256
sidecar records the source, provenance, script and signed output. Build and sign
failures preserve the previous executable. It neither installs nor runs it.
Real SDK linking/signing and target task access need separate verification;
Linux tests use explicit tool doubles and declaration-only ARM64 code generation.

Kernel `build-support/uart_console.py` recognizes this independent optional pin
group (all three are required, with immutable commit and content hashes):

- `tools/a04br3_keyboard_state.c`
- `tools/a04br3_keyboard_state_sources.json`
- `tools/build-a04br3-keyboard-state.sh`

The parent owns the device source commit, bootstrap/source pin update and SDK
build. Until that pin includes the complete group, no reader payload is emitted.
A partial group, source digest mismatch, failed build or malformed Mach-O fails
the stage and preserves its previous generated include. Console-only,
console/capture and console/UI pins remain valid, with every optional combination
supported. Cache source digests and binary digests are checked on reuse.

When included, `A04BR3_KEYBOARD_STATE_PAYLOAD_PRESENT` and
`a04br3_keyboard_state_payload` select the existing atomic installer. Under the
existing full-root preparation and console boot-argument gates it installs
`/usr/appleinternal/sbin/a04br3-keyboard-state` as root-owned mode 0755. It uses
no-follow, regular-file / single-link / owner checks, full readback and a
same-directory rename. Optional failure is logged and preserves the previous
executable. Nothing auto-launches the reader, changes launchd, or replaces UIKit.
The separate capture/UI groups and their failure behavior are unchanged.

## Observation contract

An authorized operator can explicitly invoke the installed reader as root with
`PID CACHE_SLIDE_HEX`. PID must be at least 2. Cache slide must be page aligned
and no larger than 4 GiB. The reader requires actual `task_for_pid` permission;
MAC/sandbox denial is reported and never bypassed. It verifies the Mach-O header
and exact UIKit UUID before reading any layout-specific globals. A different or
unreadable image is rejected; there is no fallback layout.

A sample makes at most 128 Mach reads, each at most 64 bytes, and reads only
fixed flags and pointer values. It does not dereference strings or scene/object
payloads, send target messages, freeze tasks, write target memory, alter HID,
change preferences, or claim an atomic snapshot. Null, unreadable and
uninitialized values are distinct: `unreadable=...`, `unavailable-object`, or
`unavailable-uninitialized-or-unreadable-once` cannot be interpreted as false.
The cached inputUIOOP byte is reported only after its dispatch-once token is
observed complete (`UINT64_MAX`). Numeric zero denotes an actual successful
read, not a substitute for an error. Pointer presence alone does not prove an
XPC handshake, visible window, or successful rendering.

Exit status 64 means invalid arguments, 77 means root/task access denied, and 2
means image verification failed. Exit 0 means the matching image was sampled;
individual fields can still be unavailable and must be assessed from each line.
Process exit, changing objects or concurrent target activity can invalidate a
field between reads. Diagnostics do not establish a keyboard fix.

## Reproducible host checks

```sh
python3 -m unittest discover -s tests -p 'test_keyboard_state*.py' -v
```

These compile the exact reader with ASAN/UBSAN and synthetic Mach memory, exercise
its real CLI/root/permission cleanup paths, generate ARM64 object code against
explicit declarations, and verify the build/sign command and failure contract.
They require Python 3, a host C compiler, clang and bash; no donor image, sibling
source tree, private SDK, target PID or physical device is needed. Kernel
`test_uart_console_stage.py` and `test_console_install.py` additionally exercise
pin/cache/tamper failure handling and all eight optional payload combinations.
