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
