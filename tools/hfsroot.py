#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Populate a fresh, non-journaled HFS+ image without mounting it.

Uses mkfs.hfsplus (hfsprogs 540.1.linux3) for metadata layout, then writes a
small ASCII-name catalog and contiguous regular-file forks. Format reference:
Apple hfs-627.100.6 core/hfs_format.h. Rejects layouts needing overflow maps.
Inputs are copied unmodified. Not a general-purpose HFS editor.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess


def get32(data, at): return struct.unpack_from('>I',data,at)[0]
def put32(data,at,value): struct.pack_into('>I',data,at,value)
def key(parent,name):
    text=name.encode('utf-16be')
    return struct.pack('>HIH',6+len(text),parent,len(text)//2)+text

def node(size,kind,height,records,forward=0,backward=0):
    result=bytearray(size)
    struct.pack_into('>IIbBHH',result,0,forward,backward,kind,height,len(records),0)
    at=14
    for i,record in enumerate(records):
        if at+len(record)>size-2*(len(records)+1): raise ValueError('B-tree node overflow')
        struct.pack_into('>H',result,size-2*(i+1),at)
        result[at:at+len(record)]=record;at+=len(record)
    struct.pack_into('>H',result,size-2*(len(records)+1),at)
    return result

def populate(image, files, volume='A04BR3Root'):
    if not volume.isascii() or not volume: raise ValueError('ASCII volume required')
    with image.open('r+b') as disk:
        disk.seek(1024);header=bytearray(disk.read(512))
        if header[:2]!=b'H+' or get32(header,4)&0x2000: raise ValueError('fresh non-journaled HFS+ required')
        block=get32(header,40);total=get32(header,44)
        def fork(offset):
            length,clump,blocks=struct.unpack_from('>QII',header,offset)
            first,count=struct.unpack_from('>II',header,offset+16)
            if any(header[offset+24:offset+80]) or count!=blocks: raise ValueError('single-extent metadata required')
            return first*block,length,blocks
        bitmap_at,bitmap_size,_=fork(112)
        catalog_at,catalog_size,_=fork(272)
        disk.seek(bitmap_at);bitmap=bytearray(disk.read(bitmap_size))
        if len(bitmap)*8<total: raise ValueError('short allocation bitmap')
        disk.seek(catalog_at);old=disk.read(512)
        node_size=struct.unpack_from('>H',old,14+18)[0]
        disk.seek(catalog_at);old=disk.read(node_size)
        offsets=[struct.unpack_from('>H',old,node_size-2*(i+1))[0] for i in range(4)]
        bt=bytearray(old[offsets[0]:offsets[1]])
        if len(bt)!=106 or get32(bt,22)*node_size!=catalog_size: raise ValueError('unexpected catalog header')
        dirs={PurePosixPath('/'):2};next_id=16
        for directory in ('/dev','/tmp','/private/var'):
            path=PurePosixPath(directory)
            for parent in reversed([path]+list(path.parents)[:-1]):
                if parent not in dirs: dirs[parent]=next_id;next_id+=1
        normalized={}
        for name,source in files.items():
            path=PurePosixPath(name)
            if not path.is_absolute() or '..' in path.parts or path==PurePosixPath('/'): raise ValueError('invalid path')
            if any(not x.isascii() or len(x)>255 for x in path.parts): raise ValueError('ASCII names <=255 required')
            if not source.is_file(): raise ValueError('regular file input required')
            normalized[path]=source
        for path in sorted(normalized,key=str):
            for parent in reversed(list(path.parents)[:-1]):
                if parent not in dirs: dirs[parent]=next_id;next_id+=1
        if set(dirs)&set(normalized): raise ValueError('file/directory collision')
        records=[];cursor=get32(header,52);added=0;timestamp=2082844800+1688515200
        def bsd(mode,restricted=False): return struct.pack('>IIBBHI',0,0,8 if restricted else 0,0,mode,0)
        def add(parent,name,data): records.append(((parent,name.lower()),key(parent,name)+data))
        def thread(identifier,parent,name,isdir):
            text=name.encode('utf-16be')
            add(identifier,'',struct.pack('>HHIH',3 if isdir else 4,0,parent,len(text)//2)+text)
        for path,identifier in dirs.items():
            parent=1 if identifier==2 else dirs[path.parent];name=volume if identifier==2 else path.name
            children=sum(x.parent==path for x in list(dirs)+list(normalized) if x!=path)
            rec=struct.pack('>HHII5I',1,0,children,identifier,*([timestamp]*4),0)+bsd(0o041777 if str(path)=='/tmp' else 0o040755)+bytes(40)
            assert len(rec)==88;add(parent,name,rec);thread(identifier,parent,name,True)
        for path,source in sorted(normalized.items(),key=lambda p:str(p[0])):
            length=source.stat().st_size;blocks=(length+block-1)//block
            first=cursor
            while first+blocks<total:
                occupied=next((i for i in range(first,first+blocks) if bitmap[i//8]&(0x80>>(i%8))),None)
                if occupied is None: break
                first=occupied+1
            else: raise ValueError('no contiguous free space')
            for i in range(first,first+blocks): bitmap[i//8]|=0x80>>(i%8)
            disk.seek(first*block)
            with source.open('rb') as src: shutil.copyfileobj(src,disk,1024*1024)
            cursor=first+blocks;added+=blocks
            forkdata=struct.pack('>QIIII',length,0,blocks,first if blocks else 0,blocks)+bytes(56)
            executable=path.suffix not in ('.dylddata','.dyldlinkedit') and not path.name.startswith('dyld_shared_cache')
            rec=struct.pack('>HHII5I',2,2,0,next_id,*([timestamp]*4),0)+bsd(0o100755 if executable else 0o100644,True)+bytes(40)+forkdata+bytes(80)
            assert len(rec)==248;add(dirs[path.parent],path.name,rec);thread(next_id,dirs[path.parent],path.name,False);next_id+=1
        records.sort(key=lambda item:item[0]);leaves=[];current=[];used=14
        for _,record in records:
            if used+len(record)+2*(len(current)+2)>node_size:
                leaves.append(current);current=[];used=14
            current.append(record);used+=len(record)
        if current: leaves.append(current)
        total_nodes=get32(bt,22);used_nodes=len(leaves)+2
        if used_nodes>total_nodes: raise ValueError('catalog capacity exceeded')
        index=[]
        for i,leaf in enumerate(leaves,2):
            first_key_size=struct.unpack_from('>H',leaf[0])[0]+2
            index.append(leaf[0][:first_key_size]+struct.pack('>I',i))
            disk.seek(catalog_at+i*node_size)
            disk.write(node(node_size,-1,1,leaf,i+1 if i<len(leaves)+1 else 0,i-1 if i>2 else 0))
        disk.seek(catalog_at+node_size);disk.write(node(node_size,0,2,index))
        struct.pack_into('>H4I',bt,0,2,1,len(records),2,len(leaves)+1)
        put32(bt,26,total_nodes-used_nodes)
        map_size=offsets[3]-offsets[2];node_map=bytearray(map_size)
        if total_nodes>map_size*8: raise ValueError('overflow node map unsupported')
        for i in range(used_nodes): node_map[i//8]|=0x80>>(i%8)
        disk.seek(catalog_at);disk.write(node(node_size,1,0,[bt,bytes(128),node_map]))
        disk.seek(bitmap_at);disk.write(bitmap)
        put32(header,32,len(normalized));put32(header,36,len(dirs)-1)
        put32(header,48,get32(header,48)-added);put32(header,52,cursor);put32(header,64,next_id)
        disk.seek(1024);disk.write(header);disk.seek(total*block-1024);disk.write(header)
    return {'files':len(normalized),'folders':len(dirs),'bytes':total*block,'file_data_blocks':added}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path,help='JSON absolute guest path to host regular file')
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--mkfs',default='mkfs.hfsplus')
    parser.add_argument('--size',type=int,default=4*1024**3)
    a=parser.parse_args()
    if a.out.exists(): parser.error('refusing to overwrite an existing image')
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('wb') as stream: stream.truncate(a.size)
    subprocess.run([a.mkfs,'-v','A04BR3Root','-b','4096',str(a.out)],check=True)
    print(json.dumps(populate(a.out,{n:Path(p) for n,p in json.loads(a.manifest.read_text()).items()}),indent=2))

if __name__=='__main__': main()
