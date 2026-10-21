#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Decode one identified preview from an EXISTING regular collector log only.
No serial open, UART reads, camera, reset or device writes. Default PNG output.
"""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib

HEADER=b'P6\n200 320\n255\n'
FRAME_BYTES=len(HEADER)+200*320*3
ID_RE=re.compile(r'[0-9a-f]{32}\Z')
DATA_RE=re.compile(rb'DATA (0|[1-9][0-9]{0,5}) ([1-9][0-9]{0,2}) ([0-9a-f]{8}) ([A-Za-z0-9+/]+={0,2})\Z')
END_RE=re.compile(rb'END ([1-9][0-9]{0,5}) ([0-9a-f]{64})\Z')

class CaptureError(ValueError):
    pass

def bounded_lines(chunks,prefix):
    pending=b'';discard=False
    for chunk in chunks:
        while chunk:
            end=chunk.find(b'\n')
            piece=chunk if end<0 else chunk[:end]
            if not discard:
                if len(pending)+len(piece)>1024:
                    if (pending+piece).startswith(prefix):raise CaptureError('overlong selected record')
                    discard=True;pending=b''
                elif end<0:pending+=piece
                else:yield (pending+piece).rstrip(b'\r')
            if end<0:break
            pending=b'';discard=False;chunk=chunk[end+1:]
    if pending.startswith(prefix):raise CaptureError('truncated selected record')

def decode_chunks(chunks,frame_id):
    if not ID_RE.fullmatch(frame_id):raise CaptureError('frame ID must be 32 lowercase hex digits')
    prefix=b'!A04FB1 '+frame_id.encode()+b' '
    started=False;complete=False;image=bytearray()
    for line in bounded_lines(chunks,prefix):
        if not line.startswith(prefix):continue  # Native logs/echo/other frame IDs.
        if complete:raise CaptureError('extra selected record after END (reused frame ID)')
        body=line[len(prefix):]
        if body==f'BEGIN 200 320 P6 {FRAME_BYTES}'.encode():
            if started:raise CaptureError('duplicate BEGIN')
            started=True;continue
        if not started:raise CaptureError('selected data without valid BEGIN')
        match=DATA_RE.fullmatch(body)
        if match:
            offset,count=int(match[1]),int(match[2])
            if offset!=len(image) or count!=min(384,FRAME_BYTES-len(image)) or not count:
                raise CaptureError('chunk offset/length mismatch or duplicate chunk')
            try:block=base64.b64decode(match[4],validate=True)
            except ValueError as error:raise CaptureError('invalid base64') from error
            if len(block)!=count or base64.b64encode(block)!=match[4]:raise CaptureError('base64 size/encoding mismatch')
            if zlib.crc32(block)&0xffffffff!=int(match[3],16):raise CaptureError('chunk CRC32 mismatch')
            image.extend(block);continue
        match=END_RE.fullmatch(body)
        if match:
            if int(match[1])!=FRAME_BYTES or len(image)!=FRAME_BYTES:raise CaptureError('incomplete frame')
            if hashlib.sha256(image).hexdigest().encode()!=match[2]:raise CaptureError('frame SHA256 mismatch')
            if not image.startswith(HEADER):raise CaptureError('unexpected P6 header')
            complete=True;continue
        raise CaptureError('malformed selected record')
    if not complete:raise CaptureError('no complete selected frame')
    return bytes(image)

def decode_log(path,frame_id,start_offset=0):
    path=Path(path)
    if start_offset<0 or not stat.S_ISREG(path.stat().st_mode):raise CaptureError('input must be a regular collector log')
    flags=os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or start_offset>info.st_size:raise CaptureError('invalid regular log/baseline')
        # A fixed finite snapshot: appending collector output never prolongs this call.
        stream.seek(start_offset);remaining=info.st_size-start_offset
        def chunks():
            nonlocal remaining
            while remaining:
                block=stream.read(min(65536,remaining))
                if not block:raise CaptureError('collector log truncated during decode')
                remaining-=len(block);yield block
        return decode_chunks(chunks(),frame_id)

def png_from_ppm(ppm):
    if len(ppm)!=FRAME_BYTES or not ppm.startswith(HEADER):raise CaptureError('invalid bounded P6 image')
    pixels=ppm[len(HEADER):]
    raw=b''.join(b'\0'+pixels[y*600:(y+1)*600] for y in range(320))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',200,320,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--frame-id',required=True,help='exact unique ID used for --uart-preview')
    parser.add_argument('--start-offset',type=int,default=0,help='collector byte baseline before command')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--format',choices=('png','ppm'),default='png')
    args=parser.parse_args()
    try:
        ppm=decode_log(args.log,args.frame_id,args.start_offset)
        data=png_from_ppm(ppm) if args.format=='png' else ppm
        # No output file is created until the entire frame verifies. Never overwrite.
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as output:output.write(data)
        except BaseException:
            args.output.unlink(missing_ok=True);raise
        print(f'A04FB verified frame={args.frame_id} width=200 height=320 sha256={hashlib.sha256(ppm).hexdigest()} output={args.output}')
        return 0
    except (CaptureError,OSError) as error:
        print(f'A04FB decode failed: {error}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
