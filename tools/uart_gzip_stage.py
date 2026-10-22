#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit gzip memory-stage diagnostic. Never COMMIT, reboot or validate boot.
Requires the gzip-capable native console and the existing single UART collector.
--prefix-bytes is diagnostic input length, NOT a shortened boot image for flash.
"""
from contextlib import contextmanager
import argparse
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import zlib
import uart_client as uart

ALLOWED = frozenset(('status','boot-zbegin','boot-zdata','boot-zfinish','boot-abort'))

@contextmanager
def packed_stage(path, prefix_bytes=None):
    with open(path,'rb') as source, tempfile.TemporaryFile() as packed:
        before=os.fstat(source.fileno())
        if not 6144<=before.st_size<=uart.GZIP_RAW_MAX or before.st_size%2048:
            raise ValueError('source must have bounded aligned boot-image length; header is not validated')
        size=before.st_size if prefix_bytes is None else prefix_bytes
        if not isinstance(size,int) or not 6144<=size<=before.st_size or size%2048:
            raise ValueError('explicit prefix must be 2048-aligned, at least6144 and no larger than source')
        digest=hashlib.sha256();remaining=size
        with gzip.GzipFile(filename='',mode='wb',fileobj=packed,mtime=0) as encoder:
            while remaining:
                block=source.read(min(1024*1024,remaining))
                if not block:raise uart.ProtocolError('source truncated before diagnostic BEGIN')
                encoder.write(block);digest.update(block);remaining-=len(block)
        after=os.fstat(source.fileno())
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise uart.ProtocolError('source changed before diagnostic BEGIN')
        compressed=packed.tell()
        if not 18<=compressed<=uart.GZIP_INPUT_MAX:raise ValueError('compressed diagnostic input exceeds native bounds')
        packed.seek(0)
        yield packed,dict(raw_bytes=size,compressed_bytes=compressed,source_bytes=before.st_size,
                          prefix_bytes=prefix_bytes,expected_stage_sha256=digest.hexdigest())

def stage_gzip(client,path,*,prefix_bytes=None):
    """Only return success after zfinish + matched stage + ABORT + clean INFO.

    On protocol/TX failure stop; no implicit cleanup commands that could append
    to a partial request. Failure never proves the stage was discarded.
    """
    def request(command,*args):
        if command not in ALLOWED:raise uart.ProtocolError('command forbidden in gzip stage-only diagnostic')
        return client.request(command,*args)
    def info():
        match=uart.STATUS_RE.fullmatch(request('status'))
        if not match:raise uart.ProtocolError('unrecognized stage diagnostic status')
        version,flags,partition,staged,image=map(int,match.groups())
        if version!=1 or not 0<partition<=0xffffffffffffffff:
            raise uart.ProtocolError('unsupported stage diagnostic INFO')
        return flags,partition,staged,image
    with packed_stage(path,prefix_bytes) as (packed,result):
        total=result['raw_bytes'];compressed=result['compressed_bytes']
        flags,partition,staged,image=info()
        if flags or staged or image or total>partition:
            raise uart.ProtocolError('initial stage is not clean; no BEGIN/ABORT sent')
        client.event(event='gzip-stage-only-start',**result)
        if request('boot-zbegin',str(compressed),str(total),result['expected_stage_sha256'])!='offset=0 raw=0':
            raise uart.ProtocolError('diagnostic BEGIN ACK mismatch')
        offset=raw_staged=0
        while True:
            block=packed.read(uart.BLOCK_BYTES)
            if not block:break
            detail=request('boot-zdata',str(offset),f'{zlib.crc32(block):08x}',__import__('base64').b64encode(block).decode('ascii'))
            match=uart.GZIP_ACK_RE.fullmatch(detail)
            if not match or int(match[1])!=offset+len(block) or not raw_staged<=int(match[2])<=total:
                raise uart.ProtocolError('diagnostic compressed/raw ACK mismatch')
            offset,raw_staged=int(match[1]),int(match[2])
        if offset!=compressed or request('boot-zfinish')!=f'offset={compressed} raw={total}':
            raise uart.ProtocolError('diagnostic FINISH ACK mismatch')
        if info()!=(1,partition,total,total):
            raise uart.ProtocolError('diagnostic stage count/state mismatch; clean status not established')
        if request('boot-abort')!='':raise uart.ProtocolError('diagnostic ABORT ACK mismatch')
        if info()!=(0,partition,0,0):raise uart.ProtocolError('diagnostic ABORT did not produce clean INFO')
        result.update(scope='gzip-memory-stage-only',zfinish_ack=True,stage_counts_matched=True,
                      abort_ack=True,clean_status_observed=True,kernel_sha_verified=False,
                      boot_image_validated=False,boot_write_performed=False,reboot_requested=False)
        client.event(event='gzip-stage-only-complete',**result)
        return result

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uart',required=True)
    parser.add_argument('--raw-log',required=True)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--prefix-bytes',type=int,help='explicit aligned diagnostic prefix, e.g.65536; default whole source')
    parser.add_argument('--log',type=Path,default=uart.DEFAULT_LOG.with_name('gzip-stage.jsonl'))
    parser.add_argument('--timeout',type=float,default=5)
    parser.add_argument('--retries',type=int,default=2)
    parser.add_argument('--tx-interval',type=float,default=.005)
    parser.add_argument('--tx-burst',type=int,default=1)
    args=parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout<=0 or args.retries<0 or not math.isfinite(args.tx_interval) or args.tx_interval<0 or args.tx_burst<1:
        parser.error('invalid finite timeout/retries/pacing')
    if args.prefix_bytes is not None and (args.prefix_bytes<6144 or args.prefix_bytes%2048):parser.error('invalid aligned diagnostic prefix')
    for protected in (args.uart,args.raw_log,args.image):
        path=Path(protected)
        if args.log.resolve()==path.resolve() or (args.log.exists() and path.exists() and os.path.samefile(args.log,path)):
            parser.error('metadata log must differ from UART, collector and source')
    raw=None;fd=None
    try:
        raw=uart.RawLog(args.raw_log)
        args.log.parent.mkdir(parents=True,exist_ok=True)
        with args.log.open('a',encoding='utf-8') as log:
            fd=uart.open_writer(args.uart)
            client=uart.Client(lambda data:None,raw.read,timeout=args.timeout,retries=args.retries,log=log,
                write_deadline=lambda data,deadline:uart.write_paced(fd,data,burst=args.tx_burst,interval=args.tx_interval,deadline=deadline))
            print(json.dumps(stage_gzip(client,args.image,prefix_bytes=args.prefix_bytes)))
        return 0
    except (OSError,ValueError,uart.ProtocolError) as error:
        print('gzip stage-only failed; clean stage is NOT established: '+str(error),file=sys.stderr);return 1
    finally:
        if fd is not None:os.close(fd)
        if raw is not None:raw.close()

if __name__=='__main__':raise SystemExit(main())
