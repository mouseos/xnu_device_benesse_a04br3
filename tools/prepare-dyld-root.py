#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a local-only HFS root from source-built probes and donor artifacts.

Input layout: macho/dyld and cache/dyld_shared_cache_arm64* copied without
patches from iPhone X iOS 16.7.16 build 20H392. Donor files are never fetched
from or committed to this repository. Requires Linux hfsprogs mkfs/fsck.
"""
import argparse
import hashlib
import plistlib
import json
from pathlib import Path
import struct
import subprocess
import hfsroot
import boot_tasks
import bootstrap_layout
import launchd_profile


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts',required=True,type=Path)
    p.add_argument('--probes',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--pid1',choices=('diagnostic','apple'),default='diagnostic',
        help='apple installs the unmodified donor launchd as PID1; diagnostic remains the default')
    p.add_argument('--service-profile',type=Path,help='Explicit minimal launchd service profile; needs development cache boot flag')
    p.add_argument('--bootstrap-layout',action='store_true',help='Preserve donor standard symlinks and include timezone/runtime layout')
    p.add_argument('--dev-keystore',action='store_true',help='Seed an empty, unencrypted development systembag plist')
    p.add_argument('--os-root',type=Path,help='read-only mounted donor OS root; required for Apple PID1 boot tasks')
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
    pid1=a.artifacts/'macho/launchd' if a.pid1=='apple' else a.probes/'a04br3-init'
    with pid1.open('rb') as f: pid1_header=f.read(16)
    if len(pid1_header)!=16 or struct.unpack_from('<III',pid1_header)!=(0xfeedfacf,0x100000c,0):
        p.error('plain arm64 PID1 required')
    files={'/sbin/launchd':pid1,
        '/usr/local/libexec/a04br3-diagnostic-init':a.probes/'a04br3-init',
        '/usr/bin/a04br3-dynamic-probe':a.probes/'dynamic-probe',
        '/usr/lib/dyld':dyld,'/etc/a04br3-stage':marker}
    tasks={}
    if a.pid1=='apple':
        if not a.os_root:p.error('--pid1 apple requires --os-root')
        boot_files,tasks=boot_tasks.donor_files(pid1,a.os_root)
        files.update(boot_files)
        helper=a.os_root/'System/Library/Filesystems/hfs.fs/mount_hfs'
        if not helper.is_file():p.error('donor System/Library/Filesystems/hfs.fs/mount_hfs required')
        files['/sbin/mount_hfs']=helper
        files['/System/Library/Filesystems/hfs.fs/mount_hfs']=helper
        files['/Library/Filesystems/hfs.fs/Contents/Resources/mount_hfs']=helper
        version=a.os_root/'System/Library/CoreServices/SystemVersion.plist'
        if version.is_file():files['/System/Library/CoreServices/SystemVersion.plist']=version
    for file in sorted(cache.glob('dyld_shared_cache_arm64*')):
        if file.is_file():files['/System/Library/Caches/com.apple.dyld/'+file.name]=file
    modes={};links={};directories=[];owners={};accounts={}
    if a.bootstrap_layout:
        if not a.os_root:p.error('--bootstrap-layout requires --os-root')
        layout_files,links,directories=bootstrap_layout.timezone_layout(a.os_root)
        files.update(layout_files)
        modes.update({name:source.stat().st_mode&0o7777 for name,source in layout_files.items()})
        files['/private/etc/a04br3-stage']=files.pop('/etc/a04br3-stage')
        modes['/private/var/tmp']=0o1777
        account_files,account_dirs,account_modes,owners,accounts=bootstrap_layout.account_layout(a.os_root)
        files.update(account_files);directories.extend(account_dirs);modes.update(account_modes)
    if a.dev_keystore:
        if a.pid1!='apple':p.error('--dev-keystore requires --pid1 apple')
        seed=a.out/'development-systembag.plist'
        seed.write_bytes(plistlib.dumps({'KeyBagKeys':b''}))
        files['/private/var/keybags/systembag.kb']=seed
        modes.update({'/private/var/keybags':0o700,'/private/var/keybags/systembag.kb':0o600})
    services=[]
    if a.service_profile:
        if not a.os_root or a.pid1!='apple':p.error('--service-profile requires donor OS root and Apple PID1')
        service_files,services=launchd_profile.prepare(a.os_root,a.service_profile,a.out/'launchd-profile')
        files.update(service_files)
        modes.update({name:(0o644 if name.endswith('.plist') else source.stat().st_mode&0o7777) for name,source in service_files.items()})
    image=a.out/'hfs-root.img'
    if image.exists():p.error('refusing to overwrite existing root image')
    with image.open('wb') as f:f.truncate(4*1024**3)
    subprocess.run([a.mkfs,'-v','A04BR3Root','-b','4096',str(image)],check=True)
    info=hfsroot.populate(image,files,modes=modes,symlinks=links,directories=directories,owners=owners)
    result=subprocess.run([a.fsck,'-fn',str(image)],text=True,capture_output=True)
    print(result.stdout,end='');print(result.stderr,end='')
    if 'appears to be OK' not in result.stdout+result.stderr:raise SystemExit('HFS verification failed')
    info.update(pid1=a.pid1,accounts=accounts,service_profile=str(a.service_profile) if a.service_profile else None,services=services,bootstrap_layout=a.bootstrap_layout,symlink_targets=links,development_keystore=a.dev_keystore,boot_tasks=tasks,donor='iPhone10,3 iOS16.7.16 20H392',donor_unmodified=True,
        inputs={name:{'path':str(file.resolve()),'bytes':file.stat().st_size} for name,file in files.items()})
    (a.out/'root-build.json').write_text(json.dumps(info,indent=2)+'\n')
    print(json.dumps({k:v for k,v in info.items() if k!='inputs'},indent=2))

if __name__=='__main__':main()
