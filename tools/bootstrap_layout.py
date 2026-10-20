# SPDX-License-Identifier: MIT
"""Copy the bounded donor timezone subtree; preserve guest link targets."""
from pathlib import Path


def timezone_layout(os_root):
    root=Path(os_root);files={};links={};directories=[]
    base=root/'usr/share/zoneinfo.default'
    if not (base/'+VERSION').is_file():raise ValueError('donor timezone +VERSION required')
    def visit(source):
        guest='/'+source.relative_to(root).as_posix()
        if source.is_symlink():links[guest]=str(source.readlink())
        elif source.is_dir():
            directories.append(guest)
            for child in sorted(source.iterdir()):visit(child)
        elif source.is_file():files[guest]=source
        else:raise ValueError('unsupported donor timezone entry: '+guest)
    visit(base)
    for alias in ('var','etc','tmp','usr/share/zoneinfo'):
        source=root/alias
        if not source.is_symlink():raise ValueError('donor standard symlink required: '+alias)
        links['/'+alias]=str(source.readlink())
    directories += ['/private/etc','/private/var/run','/private/var/tmp',
        '/private/var/db/timezone','/private/var/log/com.apple.xpc.launchd',
        '/Library/Preferences/FeatureFlags']
    return files,links,directories


def account_layout(os_root):
    root=Path(os_root);files={};modes={};owners={};directories=[];accounts={}
    for name in ('passwd','master.passwd','group'):
        source=root/'private/etc'/name
        if not source.is_file():raise ValueError('donor account file required: '+name)
        guest='/private/etc/'+name;files[guest]=source
        modes[guest]=source.stat().st_mode&0o7777
    for line in files['/private/etc/passwd'].read_text().splitlines():
        if not line or line.startswith('#'):continue
        fields=line.split(':')
        if len(fields)!=7:raise ValueError('invalid donor passwd record')
        name,_,uid,gid,_,home,_=fields
        if name not in ('root','mobile'):continue
        uid=int(uid);gid=int(gid)
        if not home.startswith('/var/') or '..' in Path(home).parts:raise ValueError('unexpected donor home path')
        canonical='/private'+home;directories.append(canonical)
        modes[canonical]=0o700;owners[canonical]=(uid,gid)
        accounts[name]={'uid':uid,'gid':gid,'home':home}
    if set(accounts)!=set(('root','mobile')):raise ValueError('root and mobile donor accounts required')
    return files,directories,modes,owners,accounts
