#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and verify the diagnostic image using locked sibling source."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
from bootimg import build_from_source, sha256
import payload
import mkinit
import rogue_firmware
import wmt_firmware
import wlan_firmware

ROOT = Path(__file__).resolve().parents[1]


def output(command, cwd=None):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def git_revision(path):
    result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=path,
                            text=True, capture_output=True)
    if result.returncode or Path(result.stdout.strip()).resolve() != path.resolve():
        return None
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=path,
                            text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def load_gpu_firmware(path):
    """Read only an explicitly supplied path, with a bounded read before parse."""
    with path.open('rb') as stream:
        blob = stream.read(rogue_firmware.LIMIT + 1)
    manifest, _ = rogue_firmware.stage(blob)
    return blob, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootshim', type=Path, default=ROOT.parent / 'bootshim')
    parser.add_argument('--out', type=Path, default=ROOT.parent / 'out' / 'diagnostic')
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--wlan-firmware', type=Path, help='Explicit MT6630 MTKW input; requires WMT/GPU assets and eMMC root')
    parser.add_argument('--wmt-firmware', type=Path, help='Explicit A04WMT1 bundle; requires --gpu-firmware and eMMC root')
    parser.add_argument('--gpu-firmware', type=Path, help='Explicit matching raw Rogue firmware, CPU staging only; requires eMMC root')
    parser.add_argument('--uart-samples',action='store_true',help='Development-only Ctrl-T live stack sampling on UART')
    parser.add_argument('--display-hz',type=int,choices=(5,10,20),default=5,help='Changed-pixel copy limit with --fast-development')
    parser.add_argument('--fast-development',action='store_true',help='Opt-in low-load software display: 20Hz notifications, 5Hz changed-pixel copies')
    parser.add_argument('--prepare-development-root',action='store_true',help='Create missing container parents on the full writable development root')
    parser.add_argument('--display-probe',action='store_true',help='Opt-in reserved LK framebuffer RGB probe and Video handoff')
    parser.add_argument('--full-donor-root',action='store_true',help='Explicit filesystem development policy for complete donor copy')
    parser.add_argument('--development-launchd-cache',action='store_true',help='Explicitly use the development unsigned launchd service cache')
    parser.add_argument('--dev-keystore',action='store_true',help='Explicit development-only unlocked KeyStore without data protection')
    parser.add_argument('--writable-root', action='store_true', help='Enable userdata-only eMMC writes and development writable HFS root')
    parser.add_argument('--platform-root', action='store_true', help='Development platform status for signed binaries on read-only root')
    parser.add_argument('--dyld-probe', action='store_true', help='Allow donor iOS arm64 probe and root shared cache search')
    parser.add_argument('--emmc-root', action='store_true', help='Boot HFS from the GPT userdata partition')
    parser.add_argument('--msdc-probe', action='store_true', help='Probe inherited eMMC using read-only PIO')
    parser.add_argument('--rootfs', type=Path, help='Raw source-generated HFS+ root RAMDisk')
    parser.add_argument('--bringup-init', action='store_true', help='Build minimal PID 1 and boot it through mockfs/md0')
    parser.add_argument('--kernel-build-json', type=Path)
    parser.add_argument("--cpus", type=int, choices=(1, 2, 4), default=1, help="Opt-in A35 SMP development topology; default one CPU")
    parser.add_argument("--emmc-dma", action="store_true", help="Opt-in read descriptor DMA after PIO byte comparison")
    parser.add_argument("--virtual-audio", choices=("unchanged","disabled","restored"),
        default="unchanged", help="Reversible development HAL plug-in move before launchd")
    parser.add_argument("--fairplay-service", choices=("unchanged","disabled","restored"),
        default="unchanged", help="Development native launchd override; does not implement FairPlay")
    args = parser.parse_args()
    if args.display_hz != 5 and not args.fast_development:
        parser.error('--display-hz requires --fast-development')
    if args.fairplay_service != "unchanged" and not (args.full_donor_root and args.prepare_development_root):
        parser.error("--fairplay-service requires full donor root preparation")
    if args.virtual_audio != "unchanged" and not (args.full_donor_root and args.prepare_development_root):
        parser.error("--virtual-audio requires full donor root preparation")

    if args.emmc_dma and not args.emmc_root:
        parser.error("--emmc-dma requires --emmc-root")
    if args.cpus != 1 and not args.full_donor_root:
        parser.error("--cpus > 1 requires full donor root")
    if args.uart_samples and not args.full_donor_root:
        parser.error('--uart-samples requires full donor root')
    if args.fast_development and not (args.full_donor_root and args.display_probe):
        parser.error('--fast-development requires full donor root and display probe')
    if args.prepare_development_root and not args.full_donor_root:
        parser.error('--prepare-development-root requires --full-donor-root')
    if args.display_probe and not args.full_donor_root:
        parser.error('--display-probe requires --full-donor-root')
    if args.full_donor_root and not (args.writable_root and args.platform_root):
        parser.error('--full-donor-root requires writable development platform root')
    if args.development_launchd_cache and not args.platform_root:
        parser.error('--development-launchd-cache requires --platform-root')
    if args.dev_keystore and not args.writable_root:
        parser.error('--dev-keystore requires --writable-root')
    if args.writable_root and not args.emmc_root:
        parser.error('--writable-root requires --emmc-root')
    if args.emmc_root and (args.rootfs or args.bringup_init):
        parser.error('--emmc-root cannot include a RAMDisk root')
    if args.rootfs and args.bringup_init:
        parser.error('choose --rootfs or --bringup-init')
    if (args.bringup_init or args.rootfs or args.emmc_root) and not args.kernel:
        parser.error('--bringup-init requires --kernel')
    wmt_blob = None
    if args.wmt_firmware is not None:
        if args.gpu_firmware is None or not args.kernel or not args.emmc_root or args.rootfs or args.bringup_init:
            parser.error('--wmt-firmware requires --gpu-firmware, --kernel and --emmc-root without a RAMDisk')
        try:
            wmt_blob, _ = wmt_firmware.load(args.wmt_firmware)
        except (OSError, ValueError) as error:
            parser.error('invalid --wmt-firmware: ' + str(error))
    gpu_blob = None
    if args.gpu_firmware is not None:
        if not args.kernel or not args.emmc_root or args.rootfs or args.bringup_init:
            parser.error('--gpu-firmware requires --kernel and --emmc-root without a RAMDisk')
        try:
            gpu_blob, _ = load_gpu_firmware(args.gpu_firmware)
        except (OSError, ValueError) as error:
            parser.error('invalid --gpu-firmware: ' + str(error))
    wlan_blob = None
    if args.wlan_firmware is not None:
        if args.wmt_firmware is None or args.gpu_firmware is None:
            parser.error('--wlan-firmware requires --wmt-firmware and --gpu-firmware')
        try:
            wlan_blob, _ = wlan_firmware.load(args.wlan_firmware)
        except (OSError, ValueError) as error:
            parser.error('invalid --wlan-firmware: ' + str(error))
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    shim_root = args.bootshim.resolve()
    revision = git_revision(shim_root)
    files = json.loads((ROOT / 'sources' / 'bootshim.files.json').read_text())
    for name, expected in files.items():
        path = shim_root / name
        if not path.is_file() or sha256(path.read_bytes()) != expected:
            parser.error('bootshim source differs from locked source: ' + name)
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    dtb_path = destination / 'mt8167-benesse-a04br3.dtb'
    subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(dtb_path),
                    str(ROOT / 'dts' / 'mt8167-benesse-a04br3.dts')], check=True)
    dtb = dtb_path.read_bytes()
    board = json.loads((ROOT / 'board.json').read_text())
    config = dict(board['boot_image'])
    if args.full_donor_root:
        config['ramdisk_addr'] = board['full_donor_handoff']['ramdisk_addr']
    packed = None
    kernel_info = None
    if args.kernel:
        if not args.kernel_build_json:
            parser.error('--kernel requires its source-build provenance JSON')
        kernel = args.kernel.read_bytes()
        provenance = json.loads(args.kernel_build_json.read_text())
        if (not provenance.get('full_kernel_built') or
                provenance.get('source_commit') != lock['kernel']['commit'] or
                provenance.get('kernel_sha256') != sha256(kernel)):
            parser.error('kernel is not the successful source build locked by this device revision')
        memory = dict(board['xnu_handoff'])
        if args.full_donor_root:
            memory.update(board['full_donor_handoff'])
        cmdline = (memory['cmdline'] + ' a04br3.kernel=' + lock['kernel']['commit'] +
                   ' a04br3.bootshim=' + lock['bootshim']['commit'] +
                   ' a04br3.device=' + (git_revision(ROOT) or 'archive'))
        cmdline = re.sub(r'(?<!\S)cpus=\d+(?!\S)', 'cpus=' + str(args.cpus), cmdline)
        if args.full_donor_root:
            cmdline += ' a04br3.full_root=1'
        if args.emmc_dma:
            cmdline += ' a04br3.msdc_dma=1'
        if args.virtual_audio != "unchanged":
            cmdline += ' a04br3.no_va=' + ('1' if args.virtual_audio == 'disabled' else '2')
        if args.fairplay_service != "unchanged":
            cmdline += ' a04br3.fp=' + ('1' if args.fairplay_service == 'disabled' else '2')

        if args.uart_samples:
            cmdline += ' a04br3.sample_uart=1'
        if args.prepare_development_root:
            cmdline += ' a04br3.prepare_root=1'
        if args.fast_development:
            cmdline += f' a04br3fb_diff=1 a04br3fb_vsync_hz=20 a04br3fb_hz={args.display_hz}'
        if args.display_probe:
            cmdline += ' a04br3.display_probe=1'
        if args.development_launchd_cache:
            cmdline += ' launchd_unsecure_cache=1'
        if args.dev_keystore:
            cmdline += ' a04br3.dev_keystore=1'
        if args.writable_root:
            cmdline += ' a04br3.root_rw=1 a04br3.emmc_rw=1'
        if args.platform_root:
            cmdline += ' a04br3.platform_root=1'
        root_image = None
        if args.bringup_init:
            obj = destination / 'a04br3-init.o'
            subprocess.run([os.environ.get('CLANG', 'clang'), '-target', 'arm64-apple-ios16.0',
                            '-mcpu=cortex-a35', '-c', str(ROOT / 'tools/a04br3-init.S'),
                            '-o', str(obj)], check=True)
            root_image = mkinit.build(obj.read_bytes())
            (destination / 'a04br3-init').write_bytes(root_image)
            cmdline += ' rd=md0'
        if args.rootfs:
            root_image = args.rootfs.read_bytes()
            if root_image[1024:1026] not in (b'H+', b'HX'):
                parser.error('--rootfs must be a raw HFS+ volume')
            cmdline += ' rd=md0'
        if args.dyld_probe:
            cmdline += ' unentitled_ios_sim_launch=1 ignition_level=1'
        if args.emmc_root:
            cmdline += ' rd=emmcroot0 a04br3.unsealed_root=1'
        if args.msdc_probe or args.emmc_root:
            cmdline += ' a04br3.msdc_probe=1'
        raw, kernel_info = payload.build(kernel, int(memory['physical_base'], 0),
                                         int(memory['managed_size'], 0), cmdline, ramdisk=root_image, compact_ramdisk=bool(args.rootfs), gpu_firmware=gpu_blob, wmt_firmware=wmt_blob, wlan_firmware=wlan_blob)
        if wlan_blob is not None:
            kernel_info["wlan_firmware"]["input_path"] = str(args.wlan_firmware.resolve())
        if wmt_blob is not None:
            kernel_info['wmt_firmware']['input_path'] = str(args.wmt_firmware.resolve())
        packed = raw
        kernel_info.update(source_build=provenance,
                           payload_bytes=len(packed), kernel_sha256=sha256(kernel))
    env = dict(os.environ, SHIM_OUT=str(destination / 'shim'))
    subprocess.run(['bash', str(shim_root / 'build.sh')], check=True, env=env)
    shim = (destination / 'shim' / 'Image').read_bytes()
    image, manifest = build_from_source(dtb, shim, config, packed)
    repeated, _ = build_from_source(dtb, shim, config, packed)
    if image != repeated:
        raise RuntimeError('packaging is not deterministic')
    prefix = os.environ.get('CROSS_COMPILE', 'aarch64-linux-gnu-')
    manifest.update(source_lock=lock, bootshim_commit=revision,
                    device_commit=git_revision(ROOT),
                    device_dirty=bool(output(['git', 'status', '--porcelain'], ROOT)) if git_revision(ROOT) else None,
                    gcc=output([prefix + 'gcc', '--version']).splitlines()[0],
                    binutils=output([prefix + 'ld', '--version']).splitlines()[0],
                    gzip=output(['gzip', '--version']).splitlines()[0],
                    dtc=output(['dtc', '--version']),
                    python=output(['python3', '--version']),
                    packaging_reproducible=True)
    manifest['kernel'] = kernel_info
    (destination / 'boot.img').write_bytes(image)
    (destination / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'SHA256SUMS').write_text(sha256(image) + '  boot.img\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
