#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a local-only HFS root from source-built probes and donor artifacts.

Input layout: macho/dyld and cache/dyld_shared_cache_arm64* copied without
patches from iPhone X iOS 16.7.16 build 20H392. Donor files are never fetched
from or committed to this repository. Requires Linux hfsprogs mkfs/fsck.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import hfsroot


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts',required=True,type=Path)
    p.add_argument('--probes',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--mkfs',default='mkfs.hfsplus')
    p.add_argument('--fsck',default='fsck.hfsplus')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    dyld=a.artifacts/'macho/dyld';cache=a.artifacts/'cache'
    with dyld.open('rb') as f: header=f.read(32)
    magic,cpu,sub=struct.unpack_from('<III',header)
    if (magic,cpu,sub)!=(0xfeedfacf,0x100000c,0):p.error('plain arm64 donor dyld required')
    with (cache/'dyld_shared_cache_arm64').open('rb') as f: magic=f.read(16)
    if not magic.startswith(b'dyld_v1') or b'arm64' not in magic:p.error('arm64 dyld shared cache required')
    marker=a.out/'stage.txt';marker.write_text('A04BR3 HFS regular-file read: PASS\n')
    files={'/sbin/launchd':a.probes/'a04br3-init',
        '/usr/bin/a04br3-dynamic-probe':a.probes/'dynamic-probe',
        '/usr/lib/dyld':dyld,'/etc/a04br3-stage':marker}
    for file in sorted(cache.glob('dyld_shared_cache_arm64*')):
        if file.is_file():files['/System/Library/Caches/com.apple.dyld/'+file.name]=file
    image=a.out/'hfs-root.img'
    if image.exists():p.error('refusing to overwrite existing root image')
    with image.open('wb') as f:f.truncate(4*1024**3)
    subprocess.run([a.mkfs,'-v','A04BR3Root','-b','4096',str(image)],check=True)
    info=hfsroot.populate(image,files)
    result=subprocess.run([a.fsck,'-fn',str(image)],text=True,capture_output=True)
    print(result.stdout,end='');print(result.stderr,end='')
    if 'appears to be OK' not in result.stdout+result.stderr:raise SystemExit('HFS verification failed')
    info.update(donor='iPhone10,3 iOS16.7.16 20H392',donor_unmodified=True,
        inputs={name:{'path':str(file.resolve()),'bytes':file.stat().st_size} for name,file in files.items()})
    (a.out/'root-build.json').write_text(json.dumps(info,indent=2)+'\n')
    print(json.dumps({k:v for k,v in info.items() if k!='inputs'},indent=2))

if __name__=='__main__':main()
