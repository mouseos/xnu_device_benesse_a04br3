# SPDX-License-Identifier: MIT
"""Export decompressed APFS metadata and apply native HFS+ xattrs in the VM."""
import argparse
import base64
import json
import os
from pathlib import Path


def export_tree(root,prefix,stream):
    root=Path(root);count=skipped=0
    paths=[root]
    while paths:
        source=paths.pop()
        if source.is_dir() and not source.is_symlink():paths.extend(source.iterdir())
        attrs=os.listxattr(source,follow_symlinks=False)
        compressed='com.apple.decmpfs' in attrs
        values={}
        for name in attrs:
            if name=='com.apple.decmpfs' or (compressed and name=='com.apple.ResourceFork'):
                skipped+=1;continue
            values[name]=base64.b64encode(os.getxattr(source,name,follow_symlinks=False)).decode()
        if values:
            guest=(Path(prefix)/source.relative_to(root)).as_posix()
            stream.write(json.dumps({'path':guest,'attrs':values})+'\n');count+=1
    return {'paths_with_attributes':count,'compression_attributes_omitted':skipped}


def apply(manifest,target):
    target=Path(target);count=0
    with Path(manifest).open() as stream:
        for line in stream:
            record=json.loads(line);relative=Path(record['path'])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('invalid metadata path')
            path=target/relative
            for name,data in record['attrs'].items():
                # Linux HFS+ maps osx.NAME to the native on-disk NAME.
                os.setxattr(path,'osx.'+name,base64.b64decode(data),follow_symlinks=False)
                count+=1
    print('A04BR3 full root: native xattrs applied',count,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('target');a=p.parse_args();apply(a.manifest,a.target)
