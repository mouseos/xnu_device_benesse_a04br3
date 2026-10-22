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
# Data migration: record the image's own build as already migrated, as
# dipa-studio/ios-sweet tools/sweet-root-tweaks.py (067c9b3) merge_pref
# 'com.apple.migration' does. Otherwise DataMigrator runs on first boot (about
# an hour on this SoC behind the progress bar) and SystemAppMigrator leaves
# staged system-app placeholders whose Settings specifiers (Wallet) have no
# icon. On iOS the CurrentHost domain is the plain mobile preferences plist.
with open(root/'System/Library/CoreServices/SystemVersion.plist','rb') as handle:
    build=plistlib.load(handle)['ProductBuildVersion']
prefs=root/'private/var/mobile/Library/Preferences'
prefs.mkdir(parents=True,exist_ok=True)
def merge_pref(name,values):
    path=prefs/(name+'.plist');data={}
    if path.exists():
        with open(path,'rb') as handle:data=plistlib.load(handle)
    data.update(values)
    path.write_bytes(plistlib.dumps(data,fmt=plistlib.FMT_BINARY))
    os.chown(path,501,501);os.chmod(path,0o600)
merge_pref('com.apple.migration',{'LastSystemVersion':build,
    'DMUserDataDisposition':{'SyntheticDidUpgrade':False,'buildVersion':build}})
# Development Setup skip, also from ios-sweet sweet-root-tweaks.py (067c9b3):
# purplebuddy SetupDone/SetupFinishedAllSteps/SetupVersion 11, mini-buddy
# flags off, no cloud configuration UI. ForceNoBuddy is the A04BR3 addition:
# BYSetupAssistantNeedsToRun (REA, SetupAssistant 0x1a3af1270) otherwise also
# requires the purplebuddy.sentinel semaphore, which does not survive reboot.
# The Activated state comes from the bootshim ADT /chosen security status.
merge_pref('com.apple.purplebuddy',{'SetupDone':True,'SetupFinishedAllSteps':True,
    'SetupVersion':11,'daemonizeNeedsToRun':False,'ForceNoBuddy':True})
merge_pref('com.apple.purplebuddy.notbackedup',{name:False for name in (
    'ShouldRunDiagnosticsMiniBuddy','ShouldRunHSA2MiniBuddy','ShouldRunKeychainSyncMiniBuddy',
    'ShouldRunMesaMiniBuddy','ShouldRunPaymentMiniBuddy','ShouldRunStandardMiniBuddy','SilentICDPUpgrade')})
merge_pref('com.apple.managedconfiguration.notbackedup',
    {'LockdownActivationIndicatesCloudConfigurationAvailable':False})
merge_pref('com.apple.springboard',{'MCForceShowCloudConfigurationUI':False})
for directory in (prefs,prefs.parent,prefs.parent.parent):
    os.chown(directory,501,501)
# Retain the source-built diagnostic and probe; donor launchd stays untouched.
for source,target in [('a04br3-init','usr/local/libexec/a04br3-diagnostic-init'),('dynamic-probe','usr/bin/a04br3-dynamic-probe')]:
    path=root/target;path.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(Path('/input/probes')/source,path);os.chmod(path,0o755)
for required in ('sbin/launchd','usr/lib/dyld','private/etc/passwd','private/etc/group','System/Library/xpc/launchd.plist','System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64'):
    if not (root/required).is_file():raise RuntimeError('full root missing: '+required)
print('A04BR3 full root: cryptex aliases',linked,'native daemon graph retained',flush=True)
