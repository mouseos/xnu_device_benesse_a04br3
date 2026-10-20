#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a complete HFSX donor root in a rootless Linux VM, never on host mounts."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request
import fullroot_xattrs

TOOLS=Path(__file__).resolve().parent
LOCK=json.loads((TOOLS.parent/'config/fullroot-runtime.json').read_text())


def checked_package(path,record):
    path=Path(path)
    if not path.exists():
        temporary=path.with_suffix('.download')
        for url in record.get('mirrors',[])+[record['url']]:
            try:
                urllib.request.urlretrieve(url,temporary)
                if hashlib.sha256(temporary.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('runtime download mismatch')
                temporary.rename(path);break
            except Exception:
                temporary.unlink(missing_ok=True)
        else:raise RuntimeError('all runtime package sources failed')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
        raise ValueError('runtime package mismatch: '+str(path))


def copy_program(program,destination,guest_path=None):
    source=Path(shutil.which(program) or program).resolve()
    target=destination/(guest_path or str(source)).lstrip('/')
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    result=subprocess.run(['ldd',str(source)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if result.returncode and not any(x in result.stdout for x in ('not a dynamic executable','statically linked')):raise RuntimeError(result.stdout)
    dependencies=result.stdout
    for library in re.findall(r'(?<!\S)(/[^\s()]+)',dependencies):
        path=Path(library);output=destination/str(path).lstrip('/')
        output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,output)


def runtime(out,kernel_deb):
    version=LOCK['kernel_version'];checked_package(kernel_deb,LOCK['kernel'])
    extracted=out/'linux';extracted.mkdir()
    subprocess.run(['dpkg-deb','-x',str(kernel_deb),str(extracted)],check=True)
    initrd=out/'initrd-tree';initrd.mkdir()
    for path in ('proc','sys','dev','bin','sbin','usr/bin','modules','lib64'): (initrd/path).mkdir(parents=True,exist_ok=True)
    copy_program('busybox',initrd,'/bin/busybox')
    for applet in ('sh','mount','umount','mkdir','insmod','poweroff','sync','cat','sleep'):(initrd/'bin'/applet).symlink_to('busybox')
    copy_program('rsync',initrd,'/usr/bin/rsync')
    copy_program('python3.12',initrd,'/usr/bin/python3.12');(initrd/'usr/bin/python3').symlink_to('python3.12')
    stdlib=Path('/usr/lib/python3.12')
    shutil.copytree(stdlib,initrd/'usr/lib/python3.12',ignore=shutil.ignore_patterns('__pycache__','test','tests','config-*'))
    for extension in stdlib.glob('lib-dynload/*.so'):
        dependencies=subprocess.check_output(['ldd',str(extension)],text=True,stderr=subprocess.STDOUT)
        for library in re.findall(r'(?<!\S)(/[^\s()]+)',dependencies):
            path=Path(library);target=initrd/str(path).lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    module_root=Path('/lib/modules')/version
    if not module_root.exists():
        package=out/'linux-modules.deb';checked_package(package,LOCK['modules'])
        subprocess.run(['dpkg-deb','-x',str(package),str(extracted)],check=True)
        module_root=extracted/'lib/modules'/version
    modules=[]
    for name in ('hfsplus','9p','9pnet_virtio'):
        command=['modprobe','--set-version',version,'--show-depends',name]
        if str(module_root).startswith(str(extracted)):command[1:1]=['--dirname',str(extracted)]
        for line in subprocess.check_output(command,text=True).splitlines():
            if not line.startswith('insmod '):continue
            source=Path(line.split()[1])
            if source not in modules:modules.append(source)
    for index,source in enumerate(modules):
        target=initrd/'modules'/f'{index:03}-{source.name.split(".")[0]}.ko'
        if source.suffix=='.zst':
            with target.open('wb') as stream:subprocess.run(['zstd','-dc',str(source)],stdout=stream,check=True)
        else:shutil.copy2(source,target)
    shutil.copyfile(TOOLS/'fullroot_guest.sh',initrd/'init');os.chmod(initrd/'init',0o755)
    archive=out/'initramfs.cpio.gz'
    paths=subprocess.check_output(['find','.','-print0'],cwd=initrd)
    with tempfile.TemporaryFile() as stream:
        subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=paths,cwd=initrd,stdout=stream,check=True)
        stream.seek(0)
        with gzip.open(archive,'wb') as output:shutil.copyfileobj(stream,output)
    return extracted/'boot'/('vmlinuz-'+version),archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--os-root',type=Path);p.add_argument('--system-root',type=Path);p.add_argument('--app-root',type=Path)
    p.add_argument('--probes',type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--kernel-deb',required=True,type=Path);p.add_argument('--mkfs',default='mkfs.hfsplus');p.add_argument('--fsck',default='fsck.hfsplus')
    p.add_argument('--log',required=True,type=Path);p.add_argument('--probe',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if out.exists():p.error('refusing to overwrite an existing output directory')
    out.mkdir(parents=True);inputs=out/'input';inputs.mkdir()
    roots={'os':a.os_root,'system':a.system_root,'app':a.app_root}
    if a.probe:
        roots={name:out/('fixture-'+name) for name in roots}
        for root in roots.values():root.mkdir()
        (roots['os']/'file').write_bytes(b'fixture');(roots['os']/'alias').symlink_to('file')
        (roots['os']/'unicode-é').write_bytes(b'unicode')
    elif any(path is None or not path.is_dir() for path in roots.values()) or not a.probes:
        p.error('all three mounted donor roots and source-built probes required')
    for name in ('fullroot_xattrs.py','fullroot_finish.py'):shutil.copyfile(TOOLS/name,inputs/name)
    if a.probes:shutil.copytree(a.probes,inputs/'probes')
    (inputs/'mode').write_text('probe' if a.probe else 'full')
    stats={}
    with (inputs/'xattrs.jsonl').open('w') as stream:
        for name,root in roots.items():
            prefix={'os':'.','system':'private/preboot/Cryptexes/OS','app':'private/preboot/Cryptexes/App'}[name]
            stats[name]=fullroot_xattrs.export_tree(root,prefix,stream)
    kernel,initramfs=runtime(out,a.kernel_deb.resolve())
    image=out/'hfs-root.img';size=128*1024**2 if a.probe else 14*1024**3
    with image.open('wb') as stream:stream.truncate(size)
    subprocess.run([a.mkfs,'-s','-J','-v','A04BR3Root',str(image)],check=True)
    command=['qemu-system-x86_64','-nodefaults','-nographic','-serial','stdio','-no-reboot','-m','2048','-smp','4',
        '-kernel',str(kernel),'-initrd',str(initramfs),'-append','console=ttyS0 panic=1 rdinit=/init',
        '-drive','file='+str(image)+',format=raw,if=virtio,cache=writeback']
    if os.access('/dev/kvm',os.R_OK|os.W_OK):command+=['-enable-kvm','-cpu','host']
    else:command+=['-accel','tcg','-cpu','max']
    for name,path in {**roots,'input':inputs}.items():
        path=path.resolve()
        if ',' in str(path):raise ValueError('commas in export paths unsupported')
        command+=['-fsdev',f'local,id={name},path={path},security_model=none,readonly=on','-device',f'virtio-9p-pci,fsdev={name},mount_tag={name}']
    a.log.parent.mkdir(parents=True,exist_ok=True)
    with a.log.open('w') as stream:subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True)
    if 'A04BR3_FULL_ROOT_VM_SUCCESS' not in a.log.read_text(errors='replace'):raise RuntimeError('VM copy failed; inspect log')
    # Match Apple's Unicode normalization before the final read-only check.
    repair=subprocess.run([a.fsck,'-fy',str(image)],capture_output=True,text=True)
    (out/'fsck-repair.log').write_text(repair.stdout+repair.stderr)
    check=subprocess.run([a.fsck,'-fn',str(image)],capture_output=True,text=True)
    (out/'fsck-check.log').write_text(check.stdout+check.stderr)
    if 'appears to be OK' not in check.stdout+check.stderr:raise RuntimeError('final HFS check failed')
    (out/'root-build.json').write_text(json.dumps({'kind':'full-donor-root','bytes':size,'donor_roots':{n:str(r.resolve()) for n,r in roots.items()},'metadata':stats,'runtime':LOCK,'probe':a.probe},indent=2)+'\n')
    print('Full root verified:',image,flush=True)

if __name__=='__main__':main()
