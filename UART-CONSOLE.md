# UART console and boot updates

Build the pinned kernel normally, then build this device with `--uart-console`
and the existing full writable development-root options. Kernel build support
copies the explicitly pinned console source from this device repository,
builds an ad-hoc signed arm64 iOS executable with the public iPhone SDK, and
embeds the generated executable. No local binary is a required build input.

The opt-in image installs a separate `/usr/appleinternal/sbin/launchd.a04br3`.
It forks a small console supervisor and immediately execs the unmodified native
`/sbin/launchd` as PID1, even when the supervisor fork fails. The supervisor
starts and waits for the UART command-console child. It does not replace native launchd or its service cache.
The console supports `exec /absolute/program arguments`; it is not a bundled
POSIX shell or a bundled dd executable. An installed shell can be executed.

Keep the existing UART collector running. The host client opens UART for
writing only and reads replies from that collector's regular log file:

```
python3 tools/uart_client.py --uart /dev/ttyUSB0 --raw-log /path/to/uart.log status
python3 tools/uart_client.py --uart /dev/ttyUSB0 --raw-log /path/to/uart.log --timeout 60 upload boot.img --reboot
```

Upload stages the complete image in memory. `/dev/a04boot` exposes INFO,
BEGIN, sequential writes, COMMIT and ABORT to root only. COMMIT checks the
complete transfer, Android/MTK framing and the CRC-verified current GPT boot
range, then writes only boot, flushes and verifies its readback. Raw disk
writes remain disabled. Upload does not reboot without `--reboot`. No image
is written merely by booting or starting the console.

Deployment first requires installing the console-capable image through the
existing flashing path. A single boot partition cannot recover itself if a
new kernel fails before reaching the console or if its overwrite is
interrupted. Host tests and successful builds do not establish hardware
update/reboot success. Verify the console, then the update and reboot, before
relying on it for unattended trials.


Console recovery is bounded to four child starts per supervisor lifetime
(initial start plus at most three restarts, delayed 2, 4 and 8 seconds).
Signal termination such as SIGINT and exhausted input recovery (exit 122)
can restart. Normal exit and permanent console open/dup/setup failures
(120, 121, 124) stop immediately. Fork or wait failure also stops; there is
no unbounded restart loop. The supervisor never opens the boot updater.
Terminal configuration must succeed before READY, including on re-open.
Canonical line input is retained, echo and software flow control are off,
BREAK is ignored, and ISIG/IEXTEN are off. Ctrl-T ttyinfo is consequently
disabled for this console.

An unfinished upload does not survive console death. Only the console owns
its boot fd; process exit closes it and the kernel last-close path discards
staging. Programs launched through `exec` close their inherited boot fd
before exec. Replacement consoles start with no fd, no stage offset and no
cached replies. Restart an interrupted upload with a fresh full BEGIN and
all image bytes; resuming the old offset or issuing COMMIT cannot flash an
old partial stage. Console-side size checks also reject incomplete uploads;
write failure or COMMIT failure requires a new BEGIN. Kernel full-stage,
SHA256 and header checks remain authoritative. Explicit reboot commands
are still explicit user operations, not supervisor recovery actions.

Host tests execute the actual source with ASan/UBSan, real fork/waitpid and
SIGINT termination. A socket fd models staging-fd lifetime and verifies EOF
on child death without a supervisor-held writer; it does not emulate the
kernel updater itself. Tests also cover bounded restarts, fatal setup exit,
unchanged native-launchd exec on supervisor fork failure, partial/broken
upload rejection and the actual terminal configuration on a PTY. These
checks do not establish hardware recovery or recovery from a kernel hang,
power loss during COMMIT, or a candidate that fails before the console.

Framebuffer observations prefer the source-built helper installed at
`/usr/appleinternal/sbin/a04-fb-capture`. Record the collector byte offset and
a fresh 32-character lowercase hexadecimal frame id before running:

```
python3 tools/uart_client.py --uart /dev/ttyUSB0 --raw-log /path/to/uart.log --timeout 60 exec /usr/appleinternal/sbin/a04-fb-capture --uart-preview FRAMEID
python3 tools/a04-fb-decode.py --log /path/to/uart.log --frame-id FRAMEID --start-offset BASELINE --output /absolute/new-screen.png
```

The helper reads only the actual 800x1280 panel surface returned by the board
framebuffer, validates its geometry and BGRA layout, and sends a 200x320
preview. The host follows the existing collector file and verifies frame id,
contiguous chunks, per-chunk CRC and complete SHA before writing a PNG. Mixed
native logs are tolerated; incomplete or corrupted frames are rejected.
The current surface lock does not freeze presentation: captures are best effort.
Use the tablet camera only when framebuffer capture is unavailable. A successful
host test or SDK build is not proof of a screenshot from the device.
