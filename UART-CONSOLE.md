# UART console and boot updates

Build the pinned kernel normally, then build this device with `--uart-console`
and the existing full writable development-root options. Kernel build support
copies the explicitly pinned console source from this device repository,
builds an ad-hoc signed arm64 iOS executable with the public iPhone SDK, and
embeds the generated executable. No local binary is a required build input.

The opt-in image installs a separate `/usr/appleinternal/sbin/launchd.a04br3`.
It starts one UART command-console child and execs the unmodified native
`/sbin/launchd` as PID1. It does not replace native launchd or its service cache.
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
