# USB root console and boot updater

Build a USB-enabled development image with `tools/build.py --usb-console`
and its required `--uart-console`/writable full-root options. The fixed kernel
source must include the MT8167 gadget implementation. UART remains available.
The private board interface is `0e8d:20ff`, product `A04BR3 XNU USB Console`.
This uses the existing board debug identity; it is not a production USB-ID allocation.

The host requires Python 3, PyUSB and libusb. It verifies product, version and
vendor bulk endpoints before sending commands. It does not reset the USB device.

```
python3 tools/usb_console.py --wait 300 --ready-wait 900 shell
python3 tools/usb_console.py cmd status
python3 tools/usb_console.py exec /bin/ps
python3 tools/usb_console.py upload boot.img --gzip --reboot
python3 tools/usb_console.py --wait 300 --ready-wait 900 smoke --reboot
```

The shell provides `help`, `pwd`, `cd`, `ls`, `cat`, `ps`, and `exec /absolute/program`.
It is a source-built command shell; it does not require a donor `/bin/sh`.
Paths/arguments currently use the canonical whitespace-token protocol.
`smoke` checks native commands and reads SystemVersion.plist. `--reboot` requests
a native reboot only after these checks. Smoke does not overwrite boot.

Upload uses the existing exclusive `/dev/a04boot` staging, full-image SHA256,
Android/MTK framing, GPT bounds, flush and readback verification. A host must see
exact `boot-write-verified` before success or an upload-triggered reboot.
The console drains the real USB IN completion before rebooting; disconnect or
drain failure prevents that reboot. Upload/reboot are explicit commands.

The source tests cover production protocol/console/kernel code with modeled
USB/MMIO/eMMC. They do not establish physical enumeration or installation success.
