#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu
export PATH=/bin:/usr/bin:/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
for module in /modules/*.ko; do insmod "$module"; done
mkdir -p /source-os /source-system /source-app /input /target
mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144,cache=loose,ro os /source-os
mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144,cache=loose,ro system /source-system
mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144,cache=loose,ro app /source-app
mount -t 9p -o trans=virtio,version=9p2000.L,msize=262144,cache=loose,ro input /input
mount -t hfsplus -o force /dev/vda /target
if [ "$(cat /input/mode)" = probe ]; then
    rsync -aH --numeric-ids /source-os/ /target/
    python3 -c "from pathlib import Path; p=Path('/target'); assert (p/'alias').is_symlink(); assert (p/'file').read_bytes()==b'fixture'; assert (p/'unicode-é').read_bytes()==b'unicode'; print('native HFS Unicode and symlink probe PASS')"
    sync; umount /target; echo A04BR3_FULL_ROOT_VM_SUCCESS; poweroff -f
fi
echo 'A04BR3 full root: copying native OS'
rsync -aH --numeric-ids --info=progress2 /source-os/ /target/
mkdir -p /target/private/preboot/Cryptexes/OS /target/private/preboot/Cryptexes/App
echo 'A04BR3 full root: copying SystemOS cryptex'
rsync -aH --numeric-ids --info=progress2 /source-system/ /target/private/preboot/Cryptexes/OS/
echo 'A04BR3 full root: copying AppOS cryptex'
rsync -aH --numeric-ids --info=progress2 /source-app/ /target/private/preboot/Cryptexes/App/
python3 /input/fullroot_xattrs.py /input/xattrs.jsonl /target
python3 /input/fullroot_finish.py /target
echo 'A04BR3 full root: sync and unmount'
sync
umount /target
echo 'A04BR3_FULL_ROOT_VM_SUCCESS'
poweroff -f
