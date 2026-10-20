# SPDX-License-Identifier: MIT
"""Make declared development additions after the complete donor copy."""
import os
from pathlib import Path
import plistlib
import shutil
import sys

root=Path(sys.argv[1]);linked=0
for cryptex in ('OS','App'):
    source=root/'private/preboot/Cryptexes'/cryptex
    for directory,dirs,files in os.walk(source,followlinks=False):
        for name in list(dirs):
            if (Path(directory)/name).is_symlink():files.append(name);dirs.remove(name)
        for name in files:
            path=Path(directory)/name;guest=path.relative_to(source);target=root/guest
            if target.exists() or target.is_symlink():continue
            target.parent.mkdir(parents=True,exist_ok=True)
            if path.is_symlink():target.symlink_to(path.readlink())
            else:os.link(path,target)
            linked+=1
# Logs normally created by restore. Native logd owns these, not root.
for name,mode in [('diagnostics',0o751),('uuidtext',0o755),('diagnostics/timesync',0o755)]:
    path=root/'private/var/db'/name;path.mkdir(parents=True,exist_ok=True)
    os.chown(path,272,272);os.chmod(path,mode)
    marker=path/'.migrated';marker.touch();os.chown(marker,272,272);os.chmod(marker,0o644)
path=root/'private/var/keybags';path.mkdir(parents=True,exist_ok=True);os.chmod(path,0o700)
seed=path/'systembag.kb';seed.write_bytes(plistlib.dumps({'KeyBagKeys':b''}));os.chmod(seed,0o600)
for directory in ('private/var/run','private/var/tmp','private/var/db/timezone','private/var/log/com.apple.xpc.launchd','Library/Preferences/FeatureFlags'):
    (root/directory).mkdir(parents=True,exist_ok=True)
# Container parents are normally established by restore, not the OS image.
for directory in ('containers','containers/Bundle','containers/Bundle/Application',
                  'containers/Data','containers/Data/Application','containers/Data/System',
                  'containers/Shared','containers/Shared/SystemGroup','containers/Temp'):
    path=root/'private/var'/directory
    if not path.exists():
        path.mkdir(mode=0o755);os.chown(path,0,0)
os.chmod(root/'private/var/tmp',0o1777)
# Retain the source-built diagnostic and probe; donor launchd stays untouched.
for source,target in [('a04br3-init','usr/local/libexec/a04br3-diagnostic-init'),('dynamic-probe','usr/bin/a04br3-dynamic-probe')]:
    path=root/target;path.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(Path('/input/probes')/source,path);os.chmod(path,0o755)
for required in ('sbin/launchd','usr/lib/dyld','private/etc/passwd','private/etc/group','System/Library/xpc/launchd.plist','System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'):
    if not (root/required).is_file():raise RuntimeError('full root missing: '+required)
print('A04BR3 full root: cryptex aliases',linked,'native daemon graph retained',flush=True)
