#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate bounded mtkclient wo commands between two source-generated HFS roots.

The old image must be the exact generated image already written to the
read-only target partition. No device readback or binary-code patch is used.
"""
import argparse
import json
from pathlib import Path


def plan(old,new,out,base,capacity,chunk=1024*1024):
    size=old.stat().st_size
    if size!=new.stat().st_size or size%512 or size>capacity or base<0 or base%512 or chunk<512 or chunk%512:
        raise ValueError('invalid image sizes or partition bounds')
    if any(c.isspace() for c in str(out.resolve())):
        raise ValueError('mtkclient script paths cannot contain whitespace')
    if out.exists():raise ValueError('refusing to overwrite delta directory')
    for path in (old,new):
        with path.open('rb') as f:
            f.seek(1024)
            if f.read(2) not in (b'H+',b'HX'):raise ValueError('raw HFS images required')
    out.mkdir(parents=True)
    ranges=[];stream=None
    try:
        with old.open('rb') as a,new.open('rb') as b:
            for offset in range(0,size,chunk):
                before=a.read(chunk);after=b.read(chunk)
                if before==after:
                    if stream:stream.close();stream=None
                    continue
                if offset+len(after)>capacity:raise ValueError('delta exceeds partition')
                if not stream:
                    path=out/f'range-{offset:012x}.bin'
                    stream=path.open('wb');ranges.append({'offset':offset,'absolute_offset':base+offset,'length':0,'file':str(path.resolve())})
                stream.write(after);ranges[-1]['length']+=len(after)
    finally:
        if stream:stream.close()
    script=out/'write.mtk'
    script.write_text(''.join(f"wo {r['absolute_offset']:#x} {r['length']:#x} {r['file']}\n" for r in ranges))
    info={'old_image':str(old.resolve()),'new_image':str(new.resolve()),'partition_start':base,
          'partition_bytes':capacity,'image_bytes':size,'write_bytes':sum(r['length'] for r in ranges),'ranges':ranges}
    (out/'delta.json').write_text(json.dumps(info,indent=2)+'\n')
    return info


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('old',type=Path);p.add_argument('new',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--partition-start',type=lambda x:int(x,0),required=True)
    p.add_argument('--partition-bytes',type=lambda x:int(x,0),required=True)
    a=p.parse_args();print(json.dumps(plan(a.old,a.new,a.out,a.partition_start,a.partition_bytes),indent=2))

if __name__=='__main__':main()
