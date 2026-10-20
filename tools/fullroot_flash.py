#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write all allocated HFS blocks, independent of any previous target image."""
import argparse
import json
import os
from pathlib import Path
import struct
import gpt


def allocated_runs(image):
    size=image.stat().st_size
    with image.open('rb') as source:
        source.seek(1024);header=source.read(512)
        if header[:2] not in (b'H+',b'HX'):raise ValueError('raw HFS volume required')
        block,total=struct.unpack_from('>II',header,40)
        if block<512 or block&(block-1) or total*block!=size:raise ValueError('invalid HFS geometry')
        length=struct.unpack_from('>Q',header,112)[0];bitmap=bytearray()
        for i in range(8):
            first,count=struct.unpack_from('>II',header,128+i*8)
            if not count:continue
            if first+count>total:raise ValueError('allocation file outside image')
            source.seek(first*block);bitmap.extend(source.read(min(count*block,length-len(bitmap))))
            if len(bitmap)==length:break
        if len(bitmap)!=length or length*8<total:raise ValueError('allocation file overflow extents unsupported')
    # Include reserved boot/header blocks even if not represented as allocated.
    def used(index):return index in (0,total-1) or bool(bitmap[index//8]&(0x80>>(index%8)))
    runs=[];start=None
    for i in range(total+1):
        if i<total and used(i):
            if start is None:start=i
        elif start is not None:
            runs.append([start*block,(i-start)*block]);start=None
    # Small holes are cheaper to write than to reconnect separate wo commands.
    merged=[]
    for offset,length in runs:
        if merged and offset-sum(merged[-1])<=65536:
            merged[-1][1]=offset+length-merged[-1][0]
        else:merged.append([offset,length])
    return merged


def copy_range(source,target,offset,length):
    source.seek(offset);left=length;native=True
    with target.open('wb') as output:
        while left:
            count=min(left,16*1024**2)
            if native:
                try:written=os.copy_file_range(source.fileno(),output.fileno(),count)
                except OSError:native=False;continue
            else:
                data=source.read(count);written=len(data);output.write(data)
            if not written:raise ValueError('incomplete HFS range')
            left-=written


def plan(image,boot,layout,out):
    image=image.resolve();boot=boot.resolve();out=out.resolve()
    if any(c.isspace() for c in str(out)+str(boot)):raise ValueError('mtkclient script output path cannot contain whitespace')
    if out.exists():raise ValueError('refusing to overwrite flash plan')
    partitions={p['name']:p for p in layout['partitions']}
    root=partitions['userdata'];kernel=partitions['boot'];sector=layout['sector_bytes']
    if image.stat().st_size>root['sectors']*sector or boot.stat().st_size>kernel['sectors']*sector:raise ValueError('artifact exceeds target partition')
    primary,secondary,backup=gpt.build(layout)
    runs=allocated_runs(image);out.mkdir(parents=True)
    commands=[];records=[]
    with image.open('rb') as source:
        for index,(offset,length) in enumerate(runs):
            if offset+length>root['sectors']*sector:raise ValueError('HFS range exceeds userdata')
            path=out/f'userdata-{index:04}.bin';copy_range(source,path,offset,length)
            address=root['first_lba']*sector+offset
            commands.append(f'wo {address:#x} {length:#x} {path}\n')
            records.append({'partition':'userdata','source_offset':offset,'offset':address,'length':length,'file':str(path)})
    commands.append(f'wo {kernel["first_lba"]*sector:#x} {boot.stat().st_size:#x} {boot}\n')
    # Activate new layout after root/boot data and backup GPT are in place.
    for name,address,data in [('gpt-secondary.bin',backup,secondary),('gpt-primary.bin',0,primary)]:
        path=out/name;path.write_bytes(data);commands.append(f'wo {address:#x} {len(data):#x} {path}\n')
    (out/'write.mtk').write_text(''.join(commands))
    info={'kind':'full-filesystem-allocation-plan','old_image_required':False,'image':str(image),'image_bytes':image.stat().st_size,
          'root_write_bytes':sum(n for _,n in runs),'unallocated_bytes_skipped':image.stat().st_size-sum(n for _,n in runs),
          'layout':layout,'ranges':records,'boot':str(boot),'gpt_activation_last':True}
    (out/'flash-plan.json').write_text(json.dumps(info,indent=2)+'\n');return info

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--image',required=True,type=Path);p.add_argument('--boot',required=True,type=Path)
    p.add_argument('--layout',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    ready=json.loads((a.image.parent/'root-build.json').read_text())
    if ready.get('kind')!='full-donor-root' or ready.get('probe'):raise SystemExit('verified complete donor root required')
    info=plan(a.image,a.boot,json.loads(a.layout.read_text()),a.out)
    print(json.dumps({k:v for k,v in info.items() if k not in ('ranges','layout')},indent=2))
