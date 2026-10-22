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
import tempfile
import zlib

# Preview scale 4 (default, 200x320), 2 (400x640) or 1 (full 800x1280).
SIZES=((200,320),(400,640),(800,1280))
def header_for(width,height):return b'P6\n%d %d\n255\n'%(width,height)
def frame_bytes(width,height):return len(header_for(width,height))+width*height*3
HEADER=header_for(200,320)
FRAME_BYTES=frame_bytes(200,320)
BEGIN_RE=re.compile(rb'BEGIN (200|400|800) (320|640|1280) P6 ([1-9][0-9]{0,6})\Z')
ID_RE=re.compile(r'[0-9a-f]{32}\Z')
DATA_RE=re.compile(rb'DATA (0|[1-9][0-9]{0,6}) ([1-9][0-9]{0,2}) ([0-9a-f]{8}) ([A-Za-z0-9+/]+={0,2})\Z')
DATA_HEADER_RE=re.compile(rb'DATA (0|[1-9][0-9]{0,6}) ([1-9][0-9]{0,2}) ([0-9a-f]{8}) (.*)\Z')
BASE64_RE=re.compile(rb'[A-Za-z0-9+/]+={0,2}\Z')
MAX_INTERLEAVED_LINES=32
MAX_INTERLEAVED_BYTES=16384
MAX_RECOVERED_CHUNKS=32
MAX_RECOVERY_CANDIDATES=16384
END_RE=re.compile(rb'END ([1-9][0-9]{0,6}) ([0-9a-f]{64})\Z')

class CaptureError(ValueError):
    pass

def bounded_lines(chunks,prefix,*,report_sizes=False,max_line=1024):
    pending=b'';discard=False;wire_size=0
    for chunk in chunks:
        while chunk:
            end=chunk.find(b'\n')
            piece=chunk if end<0 else chunk[:end]
            wire_size+=len(piece)+(end>=0)
            if not discard:
                if len(pending)+len(piece)>max_line:
                    if (pending+piece).startswith(prefix):raise CaptureError('overlong selected record')
                    discard=True;pending=b''
                elif end<0:pending+=piece
                elif end>=0:
                    line=(pending+piece).rstrip(b'\r')
                    yield (line,wire_size) if report_sizes else line
            if end<0:break
            if discard and report_sizes:yield None,wire_size
            pending=b'';discard=False;wire_size=0;chunk=chunk[end+1:]
    if pending.startswith(prefix):raise CaptureError('truncated selected record')

def recovered_lines(chunks,prefix):
    """Normalize at most two insertions per DATA, using retained fragments only.

    Each window ends BEFORE the next capture record (any frame ID). A candidate
    is a first-line prefix, optionally ONE intermediate non-pure line's prefix,
    and a later pure base64 continuation. Total length leaves only one prefix
    cut to enumerate, with a fixed per-window candidate attempt limit. Lengths,
    canonical encoding and CRC must agree; distinct CRC-valid bytes are rejected
    even if END SHA would select one. Bounds include discarded log-line bytes.
    The caller still verifies every offset/count and the original whole END SHA.
    """
    lines=iter(bounded_lines(chunks,prefix,report_sizes=True,max_line=MAX_INTERLEAVED_BYTES))
    pending=None;recovered=0
    while True:
        try:line,size=pending if pending is not None else next(lines)
        except StopIteration:return
        pending=None
        if line is None:continue
        if not line.startswith(prefix):
            yield line;continue
        body=line[len(prefix):];header=DATA_HEADER_RE.fullmatch(body)
        if not header:
            yield line;continue  # BEGIN/END or a malformed header: strict decides.
        count=int(header[2]);encoded_size=4*((count+2)//3)
        if count>384 or (DATA_RE.fullmatch(body) and len(header[4])==encoded_size):
            yield line;continue  # Never repair a complete payload's bad CRC/data.
        if b'!A04FB1 ' in header[4]:raise CaptureError('unsupported embedded capture record')
        recovered+=1
        if recovered>MAX_RECOVERED_CHUNKS:raise CaptureError('interleaved chunk recovery limit')
        candidates=set();window_bytes=size;window_lines=1;attempts=0;middles=[]
        def consider(encoded):
            nonlocal attempts
            attempts+=1
            if attempts>MAX_RECOVERY_CANDIDATES:raise CaptureError('interleaved candidate attempt limit')
            try:block=base64.b64decode(encoded,validate=True)
            except ValueError:return
            if len(block)!=count or base64.b64encode(block)!=encoded:return
            if zlib.crc32(block)&0xffffffff!=int(header[3],16):return
            candidates.add(block)
            if len(candidates)>1:raise CaptureError('ambiguous interleaved chunk bytes')
        if window_bytes>MAX_INTERLEAVED_BYTES:raise CaptureError('interleaved byte limit')
        for continuation,cost in lines:
            if continuation is not None and continuation.startswith(b'!A04FB1 '):
                pending=(continuation,cost);break
            window_bytes+=cost;window_lines+=1
            if window_bytes>MAX_INTERLEAVED_BYTES:raise CaptureError('interleaved byte limit')
            if window_lines>MAX_INTERLEAVED_LINES:raise CaptureError('interleaved line limit')
            if continuation is None:raise CaptureError('overlong interleaved log line')
            if b'!A04FB1 ' in continuation:raise CaptureError('unsupported embedded capture record')
            if not BASE64_RE.fullmatch(continuation):
                # Do not combine multiple pure continuation lines. An inserted
                # suffix must remain on every prefix-bearing line.
                if continuation:middles.append(continuation)
                continue
            take=encoded_size-len(continuation)
            # A nonempty suffix must remain on the first line as the inserted
            # log fragment. No guessed bytes, whitespace stripping or padding.
            if take<0:continue
            if take<len(header[4]):consider(header[4][:take]+continuation)
            for middle in middles:
                # middle_take = take-first_take, both inserted suffixes nonempty.
                # A middle contributes at least one retained byte; the two-part
                # path above covers no intermediate fragment.
                lo=max(0,take-(len(middle)-1))
                hi=min(take-1,len(header[4])-1)
                for first_take in range(lo,hi+1):
                    middle_take=take-first_take
                    consider(header[4][:first_take]+middle[:middle_take]+continuation)
        else:
            raise CaptureError('truncated interleaved window')
        if not candidates:raise CaptureError(f'no CRC-valid interleaved chunk at offset {int(header[1])}')
        block=candidates.pop()
        yield line[:len(prefix)+header.start(4)]+base64.b64encode(block)

def decode_chunks(chunks,frame_id,*,recover_interleaved=False):
    if not ID_RE.fullmatch(frame_id):raise CaptureError('frame ID must be 32 lowercase hex digits')
    prefix=b'!A04FB1 '+frame_id.encode()+b' '
    started=False;complete=False;image=bytearray();total=FRAME_BYTES;header=HEADER
    lines=recovered_lines(chunks,prefix) if recover_interleaved else bounded_lines(chunks,prefix)
    for line in lines:
        if not line.startswith(prefix):continue  # Native logs/echo/other frame IDs.
        if complete:raise CaptureError('extra selected record after END (reused frame ID)')
        body=line[len(prefix):]
        begin=BEGIN_RE.fullmatch(body)
        if begin:
            size=(int(begin[1]),int(begin[2]))
            if size not in SIZES or int(begin[3])!=frame_bytes(*size):raise CaptureError('unsupported preview size')
            if started:raise CaptureError('duplicate BEGIN')
            total=frame_bytes(*size);header=header_for(*size)
            started=True;continue
        if not started:raise CaptureError('selected data without valid BEGIN')
        match=DATA_RE.fullmatch(body)
        if match:
            offset,count=int(match[1]),int(match[2])
            if offset!=len(image) or count!=min(384,total-len(image)) or not count:
                raise CaptureError('chunk offset/length mismatch or duplicate chunk')
            try:block=base64.b64decode(match[4],validate=True)
            except ValueError as error:raise CaptureError('invalid base64') from error
            if len(block)!=count or base64.b64encode(block)!=match[4]:raise CaptureError('base64 size/encoding mismatch')
            if zlib.crc32(block)&0xffffffff!=int(match[3],16):raise CaptureError('chunk CRC32 mismatch')
            image.extend(block);continue
        match=END_RE.fullmatch(body)
        if match:
            if int(match[1])!=total or len(image)!=total:raise CaptureError('incomplete frame')
            if hashlib.sha256(image).hexdigest().encode()!=match[2]:raise CaptureError('frame SHA256 mismatch')
            if not image.startswith(header):raise CaptureError('unexpected P6 header')
            complete=True;continue
        raise CaptureError('malformed selected record')
    if not complete:raise CaptureError('no complete selected frame')
    return bytes(image)

def decode_log(path,frame_id,start_offset=0,expected_identity=None,*,recover_interleaved=False):
    path=Path(path)
    if start_offset<0 or not stat.S_ISREG(path.stat().st_mode):raise CaptureError('input must be a regular collector log')
    flags=os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or start_offset>info.st_size:raise CaptureError('invalid regular log/baseline')
        if expected_identity is not None and (info.st_dev,info.st_ino)!=expected_identity:
            raise CaptureError('collector log replaced before decode')
        # A fixed finite snapshot: appending collector output never prolongs this call.
        stream.seek(start_offset);remaining=info.st_size-start_offset
        def chunks():
            nonlocal remaining
            while remaining:
                block=stream.read(min(65536,remaining))
                if not block:raise CaptureError('collector log truncated during decode')
                remaining-=len(block);yield block
        return decode_chunks(chunks(),frame_id,recover_interleaved=recover_interleaved)

def png_from_ppm(ppm):
    for width,height in SIZES:
        if len(ppm)==frame_bytes(width,height) and ppm.startswith(header_for(width,height)):break
    else:raise CaptureError('invalid bounded P6 image')
    pixels=ppm[len(header_for(width,height)):];row=width*3
    raw=b''.join(b'\0'+pixels[y*row:(y+1)*row] for y in range(height))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

def publish_image(path,data):
    """Publish a fully verified image atomically, without replacing any name."""
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Same-directory hard link is atomic and fails if the destination exists.
        os.link(name,path)
    finally:
        os.unlink(name)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log',type=Path,required=True)
    parser.add_argument('--frame-id',required=True,help='exact unique ID used for --uart-preview')
    parser.add_argument('--start-offset',type=int,default=0,help='collector byte baseline before command')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--format',choices=('png','ppm'),default='png')
    parser.add_argument('--recover-interleaved',action='store_true',
        help='opt in to bounded retained-fragment recovery; CRC and original END SHA still required')
    args=parser.parse_args()
    try:
        ppm=decode_log(args.log,args.frame_id,args.start_offset,recover_interleaved=args.recover_interleaved)
        data=png_from_ppm(ppm) if args.format=='png' else ppm
        # No final filename exists until all bytes verify and finish writing.
        publish_image(args.output,data)
        print(f'A04FB verified frame={args.frame_id} width=200 height=320 sha256={hashlib.sha256(ppm).hexdigest()} output={args.output}')
        return 0
    except (CaptureError,OSError) as error:
        print(f'A04FB decode failed: {error}',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
