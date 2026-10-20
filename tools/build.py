#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and verify the diagnostic image using locked sibling source."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from bootimg import build_from_source, sha256
import payload
import mkinit

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootshim', type=Path, default=ROOT.parent / 'bootshim')
    parser.add_argument('--out', type=Path, default=ROOT.parent / 'out' / 'diagnostic')
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--bringup-init', action='store_true', help='Build minimal PID 1 and boot it through mockfs/md0')
    parser.add_argument('--kernel-build-json', type=Path)
    args = parser.parse_args()
    if args.bringup_init and not args.kernel:
        parser.error('--bringup-init requires --kernel')
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
    config = json.loads((ROOT / 'board.json').read_text())['boot_image']
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
        memory = json.loads((ROOT / 'board.json').read_text())['xnu_handoff']
        cmdline = (memory['cmdline'] + ' a04br3.kernel=' + lock['kernel']['commit'] +
                   ' a04br3.bootshim=' + lock['bootshim']['commit'])
        root_image = None
        if args.bringup_init:
            obj = destination / 'a04br3-init.o'
            subprocess.run([os.environ.get('CLANG', 'clang'), '-target', 'arm64-apple-ios16.0',
                            '-mcpu=cortex-a35', '-c', str(ROOT / 'tools/a04br3-init.S'),
                            '-o', str(obj)], check=True)
            root_image = mkinit.build(obj.read_bytes())
            (destination / 'a04br3-init').write_bytes(root_image)
            cmdline += ' rd=md0'
        raw, kernel_info = payload.build(kernel, int(memory['physical_base'], 0),
                                         int(memory['managed_size'], 0), cmdline, ramdisk=root_image)
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
