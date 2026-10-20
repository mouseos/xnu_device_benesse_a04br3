#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the minimal HFS+ root on macOS using source-built diagnostic init.

Usage: python3 mkhfsroot.py --out DIR
Requires the declared Apple Xcode toolchain and macOS hdiutil. The image is
an ordinary HFS+ volume, not an Apple OS distribution. hdiutil filesystem
metadata (UUIDs/times) may vary; the source files and construction are explicit.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import mkinit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path)
    a = p.parse_args()
    if sys.platform != 'darwin':
        p.error('HFS root construction requires macOS hdiutil')
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    tree = out / 'root-tree'
    if tree.exists():
        p.error('refusing to reuse an existing root tree')
    for name in ('sbin', 'dev', 'etc', 'private/var', 'tmp'):
        (tree/name).mkdir(parents=True, exist_ok=True)
    obj = out/'a04br3-init.o'
    subprocess.run(['xcrun','clang','-target','arm64-apple-ios16.0','-mcpu=cortex-a35','-c',
                    str(Path(__file__).with_name('a04br3-init.S')),'-o',str(obj)],check=True)
    (tree/'sbin/launchd').write_bytes(mkinit.build(obj.read_bytes()))
    (tree/'sbin/launchd').chmod(0o755)
    (tree/'etc/a04br3-stage').write_text('A04BR3 HFS regular-file read: PASS\n')
    (tree/'tmp').chmod(0o1777)
    subprocess.run(['sudo','chown','-R','0:0',str(tree)],check=True)
    dmg=out/'root.dmg'; cdr=out/'root.cdr'
    subprocess.run(['hdiutil','create','-size','32m','-fs','HFS+','-volname','A04BR3Root',
                    '-layout','NONE','-srcfolder',str(tree),'-format','UDRW',str(dmg)],check=True)
    subprocess.run(['hdiutil','convert',str(dmg),'-format','UDTO','-o',str(cdr)],check=True)
    data=cdr.read_bytes()
    if data[1024:1026] not in (b'H+',b'HX'):
        raise RuntimeError('expected a raw HFS+ volume at offset zero')
    image=out/'hfs-root.img';image.write_bytes(data)
    (out/'root-build.json').write_text(json.dumps({'filesystem':'HFS+', 'bytes':len(data),
        'contents':{'/sbin/launchd':'tools/a04br3-init.S via mkinit.py',
                    '/etc/a04br3-stage':'A04BR3 HFS regular-file read: PASS'},
        'byte_reproducible':False,'reason':'hdiutil assigns filesystem UUIDs and metadata times'},indent=2)+'\n')
    print(image)


if __name__ == '__main__':
    main()
