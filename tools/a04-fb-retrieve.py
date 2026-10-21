#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""One finite framebuffer capture through the existing single UART collector.
Write-only UART; no termios, camera, reset, boot update or automatic exec retry.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import select
from pathlib import Path
import sys
import time
import uuid

import uart_client

_spec=importlib.util.spec_from_file_location('a04_fb_decode',Path(__file__).with_name('a04-fb-decode.py'))
decoder=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decoder)
HELPER='/usr/appleinternal/sbin/a04-fb-capture'


def retrieve(uart,raw_log,output,record,*,timeout=120,tx_interval=0.005):
    if not math.isfinite(timeout) or timeout<=0 or not math.isfinite(tx_interval) or tx_interval<0:
        raise ValueError('finite positive timeout and nonnegative TX interval required')
    uart,raw_log,output,record=map(Path,(uart,raw_log,output,record))
    paths=[uart,raw_log,output,record]
    for i,path in enumerate(paths):
        for other in paths[:i]:
            if path.resolve()==other.resolve() or (path.exists() and other.exists() and os.path.samefile(path,other)):
                raise ValueError('UART, collector log, image and record must be distinct')
    if os.path.lexists(output):raise FileExistsError('image output already exists')
    frame_id=uuid.uuid4().hex
    request_id=uuid.uuid4().hex
    raw=uart_client.RawLog(raw_log)
    fd=None
    try:
        info=os.fstat(raw.file.fileno());baseline=raw.file.tell()
        # Exclusive metadata creation before transmission makes the exact capture
        # identity/baseline recoverable even on timeout or host interruption.
        with record.open('x',encoding='utf-8') as log:
            def event(**fields):
                log.write(json.dumps({'time':time.time(),**fields})+'\n');log.flush()
            event(event='capture-start',frame_id=frame_id,request_id=request_id,
                  helper=HELPER,raw_log=str(raw_log.resolve()),baseline=baseline,
                  collector_device=info.st_dev,collector_inode=info.st_ino,
                  output=str(output.resolve()),timeout=timeout)
            try:
                deadline=time.monotonic()+timeout
                fd=uart_client.open_writer(uart)
                def remaining():
                    left=deadline-time.monotonic()
                    if left<=0:raise uart_client.AckTimeout('capture deadline expired; exec was not retried')
                    return left
                def send(data):
                    # Nonblocking write-only UART, including a finite bound if
                    # its TX queue stops draining. No UART reads/termios access.
                    for byte in data:
                        while True:
                            remaining()
                            try:
                                count=os.write(fd,bytes((byte,)))
                                if count!=1:raise uart_client.ProtocolError('UART write made no progress')
                                break
                            except InterruptedError:continue
                            except BlockingIOError:select.select([], [fd], [], min(.2,remaining()))
                        if tx_interval:time.sleep(min(tx_interval,remaining()))
                def receive():
                    remaining()
                    return raw.read()
                client=uart_client.Client(
                    send,receive,timeout=timeout,retries=0,log=log,
                    sleep=lambda seconds:time.sleep(max(seconds,0.2)))
                detail=client.request('exec',HELPER,'--uart-preview',frame_id,request_id=request_id)
                if detail!='wait-status=0':raise uart_client.RemoteError('capture process failed: '+detail)
                now=raw_log.stat()
                if (now.st_dev,now.st_ino)!=(info.st_dev,info.st_ino):
                    raise uart_client.ProtocolError('collector log replaced during capture')
                ppm=decoder.decode_log(raw_log,frame_id,baseline,(info.st_dev,info.st_ino))
                png=decoder.png_from_ppm(ppm)
                decoder.publish_image(output,png)
                result=dict(frame_id=frame_id,baseline=baseline,width=200,height=320,
                            ppm_sha256=hashlib.sha256(ppm).hexdigest(),
                            png_sha256=hashlib.sha256(png).hexdigest(),output=str(output.resolve()),
                            consistency='best-effort scanout; not hardware latched')
                event(event='capture-verified',**result)
                return result
            except BaseException as error:
                event(event='capture-failed',error=type(error).__name__,detail=str(error))
                raise
    finally:
        if fd is not None:os.close(fd)
        raw.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uart',required=True)
    parser.add_argument('--raw-log',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--record',type=Path,help='new JSONL file (default OUTPUT.capture.jsonl)')
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--tx-interval',type=float,default=0.005)
    args=parser.parse_args(argv)
    record=args.record or Path(str(args.output)+'.capture.jsonl')
    try:
        result=retrieve(args.uart,args.raw_log,args.output,record,timeout=args.timeout,tx_interval=args.tx_interval)
        print(json.dumps(result));return 0
    except (OSError,ValueError,uart_client.ProtocolError) as error:
        print('A04FB retrieval failed: '+str(error),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
