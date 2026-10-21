#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Private USB ASCII console, stdout retrieval and existing boot uploader.
No raw LBA API. Explicit selected/configured vendor interface only. No reset,
set_configuration, kernel-driver detach, UART or device discovery on import.
"""
import argparse
import json
import math
import select
from pathlib import Path
import shlex
import sys
import time
import uuid
import uart_client as uart

MINI_COMMANDS=frozenset(('help','ls','cat','pwd','cd'))

class Console:
    """Adapt a bounded USB byte stream to the existing canonical ASCII Client."""
    def __init__(self,stream,*,timeout=60,retries=2,log=None,stdout=None,clock=time.monotonic,sleep=time.sleep):
        self.stream=stream;self.timeout=timeout;self.clock=clock
        self.deadline=None;self.stdout=stdout or (lambda data:None)
        self.client=uart.Client(lambda data:None,self.read,timeout=timeout,retries=retries,log=log,
                               clock=clock,sleep=sleep,write_deadline=self.write_deadline)
    def write_deadline(self,data,deadline):
        self.deadline=deadline
        self.stream.send(data,deadline)
    def read(self):
        if self.deadline is None:raise uart.ProtocolError('no active USB console request')
        data=self.stream.receive(self.deadline)
        if not isinstance(data,bytes):raise uart.ProtocolError('USB RX must return raw bytes')
        if data:self.stdout(data)
        return data
    def drain(self,timeout=.2):
        if not self.client.lock.acquire(blocking=False):return
        try:
            data=self.stream.receive(self.clock()+timeout)
            if data:self.stdout(data);self.client.parser.feed(data)
        finally:self.client.lock.release()
    def wait_ready(self,seconds):
        deadline=self.clock()+seconds;tail=b''
        while self.clock()<deadline:
            data=self.stream.receive(deadline)
            if data:
                self.stdout(data);self.client.parser.feed(data);tail=(tail+data)[-512:]
                if b'A04BR3_USBCONSOLE READY' in tail:
                    self.client.event(event='usb-native-console-ready');return
            else:self.client.sleep(min(.05,max(0,deadline-self.clock())))
        raise uart.AckTimeout('native USB console READY not observed before deadline')
    def command(self,command,*args):
        if command in MINI_COMMANDS:
            detail=self.client.request('shell',command,*args)
            if detail!='shell-status=0':raise uart.RemoteError('native shell failed: '+detail)
            return detail
        if command not in uart.COMMANDS:raise ValueError('unsupported native command')
        detail=self.client.request(command,*args)
        if command=='exec' and detail!='wait-status=0':raise uart.RemoteError('native exec failed: '+detail)
        return detail
    def upload(self,path,*,gzip=False,reboot=False):
        return self.client.upload(path,reboot=reboot,compression='gzip' if gzip else 'raw')

def run_line(console,line):
    words=shlex.split(line)
    if not words:return None
    command,*args=words
    if command in ('quit','exit') and not args:return False
    if command=='upload':
        parser=argparse.ArgumentParser(prog='upload',exit_on_error=False)
        parser.add_argument('image',type=Path);parser.add_argument('--gzip',action='store_true');parser.add_argument('--reboot',action='store_true')
        try:options=parser.parse_args(args)
        except SystemExit as error:raise ValueError('upload IMAGE [--gzip] [--reboot]') from error
        return console.upload(options.image.expanduser(),gzip=options.gzip,reboot=options.reboot)
    if command=='ps':command,args='exec',['/bin/ps',*args]
    elif command.startswith('/'):command,args='exec',[command,*args]
    return console.command(command,*args)

def interactive(console,input_stream=sys.stdin,output=sys.stdout):
    output.write('USB root console: help/ls/cat/pwd/cd, exec /absolute/program, ps, status, upload IMAGE [--gzip] [--reboot], quit\n');output.flush()
    while True:
        try:
            try:fd=input_stream.fileno()
            except (AttributeError,OSError,ValueError):fd=None
            if fd is not None and not select.select([fd],[],[],.05)[0]:
                console.drain();continue # Unsolicited READY/stdout while waiting for keyboard input.
            line=input_stream.readline()
            if not line:return
            result=run_line(console,line)
            if result is False:return
            if result is not None:output.write(str(result)+'\n');output.flush()
        except (ValueError,argparse.ArgumentError,uart.RemoteError) as error:
            output.write('ERROR '+str(error)+'\n');output.flush()
        except (uart.ProtocolError,OSError) as error:
            output.write('TRANSPORT STOPPED '+str(error)+'\n');output.flush();return

# Bulk protocol transport is implemented below against the shared native TX/RX
# framing. No executable behavior occurs merely by importing this module.

import struct
import zlib
import usb_bulk_probe as framing

PRODUCT='A04BR3 XNU USB Console'
VID=0x0e8d
PID=0x20ff
BCD_DEVICE=0x0100

class BulkStream:
    """Exact native op7 RX(host->device), op8 TX(device->host)."""
    def __init__(self,exchange,maxpacket,*,clock=time.monotonic,sleep=time.sleep,retries=2,read_reply=None):
        if maxpacket not in (64,512):raise ValueError('FS64/HS512 required')
        self.exchange=exchange;self.maxpacket=maxpacket;self.clock=clock;self.sleep=sleep;self.retries=retries;self.seq=1;self.poisoned=False;self.read_reply=read_reply;self.last_reply=None
    def remaining(self,deadline):
        left=deadline-self.clock()
        if left<=0:raise uart.AckTimeout('USB console deadline expired')
        return left
    def exchange_until(self,frame,deadline):
        # Only console ops have proven previous-success reply replay caching.
        for attempt in range(self.retries+1):
            timeout=max(1,min(3000,math.ceil(self.remaining(deadline)*1000)))
            try:
                reply=bytes(self.exchange(frame,timeout))
                drained=0
                while self.last_reply is not None and reply==self.last_reply:
                    # Prior successful console reply can remain queued after a
                    # timeout+duplicate OUT. Do not resend this new OUT: drain
                    # only byte-identical previously validated prior-seq IN.
                    if self.read_reply is None or drained>self.retries+1:
                        raise uart.ProtocolError('stale USB IN reply cannot be drained safely')
                    timeout=max(1,min(3000,math.ceil(self.remaining(deadline)*1000)))
                    try:reply=bytes(self.read_reply(timeout))
                    except TimeoutError as error:
                        raise uart.ProtocolError('USB stale reply drained but current IN unavailable; no new OUT') from error
                    drained+=1
                return reply
            except TimeoutError:
                if attempt==self.retries:raise uart.ProtocolError('USB response lost; no further command sent')
        raise AssertionError('unreachable')
    def rpc(self,op,data,deadline):
        if self.poisoned:raise uart.ProtocolError('USB stream stopped after unresolved transport failure')
        if op not in (7,8):raise ValueError('console stream op required')
        if not 0<self.seq<0xffffffff:raise uart.ProtocolError('USB sequence exhausted')
        frame=framing.encode(op,self.seq,0,data)
        if len(frame)>self.maxpacket:raise ValueError('USB request exceeds one endpoint packet')
        while True:
            reply=self.exchange_until(frame,deadline)
            if not 48<=len(reply)<=self.maxpacket:raise uart.ProtocolError('USB reply length invalid')
            magic,version,kind,seq,offset,length,crc,hcrc,reserved=framing.HEADER.unpack_from(reply)
            header=bytearray(reply[:32]);header[24:28]=bytes(4)
            if ((magic,version,kind,seq,offset,reserved)!=(framing.MAGIC,1,op|0x8000,self.seq,0,0) or
                    length!=len(reply)-32 or zlib.crc32(header)!=hcrc or zlib.crc32(reply[32:])!=crc):
                raise uart.ProtocolError('USB reply identity/length/CRC mismatch')
            status,next_seq,count,zero=struct.unpack_from('<4I',reply,32)
            payload=reply[48:]
            if zero:raise uart.ProtocolError('USB reply reserved field invalid')
            if status==7:
                if next_seq!=self.seq or count or payload:raise uart.ProtocolError('USB BUSY state invalid')
                self.sleep(min(.01,self.remaining(deadline)));continue # Same exact request, no acceptance.
            if status or next_seq!=self.seq+1:raise uart.ProtocolError('USB stream status/sequence invalid')
            if op==7:
                if payload or count!=len(data):raise uart.ProtocolError('USB input acceptance mismatch')
            elif count!=len(payload) or count>struct.unpack('<I',data)[0]:
                raise uart.ProtocolError('USB output count mismatch')
            self.last_reply=reply;self.seq=next_seq
            return payload
    def send(self,data,deadline):
        try:
            for offset in range(0,len(data),self.maxpacket-32):
                self.rpc(7,data[offset:offset+self.maxpacket-32],deadline)
        except (OSError,uart.ProtocolError):
            self.poisoned=True;raise
    def receive(self,deadline):
        try:return self.rpc(8,struct.pack('<I',self.maxpacket-48),deadline)
        except (OSError,uart.ProtocolError) as error:
            self.poisoned=True
            raise uart.ProtocolError('USB RX unresolved; stream stopped') from error
    def handshake(self,deadline):
        # Read-only PING can learn a live session's expected sequence without
        # resetting the gadget or re-sending any console bytes.
        for op in (1,2):
            for attempt in range(2):
                frame=framing.encode(op,self.seq)
                timeout=max(1,min(3000,math.ceil(self.remaining(deadline)*1000)))
                reply=framing.decode_reply(bytes(self.exchange(frame,timeout)),op,self.seq)
                if reply['status']==2 and attempt==0 and 0<reply['next']<0xffffffff:
                    self.seq=reply['next'];continue
                if reply['status'] or reply['next']!=self.seq+1 or reply['chunk']!=self.maxpacket-32:
                    raise uart.ProtocolError('USB PING/INFO refused')
                self.seq=reply['next'];break
            else:raise uart.ProtocolError('USB session synchronization failed')
        return reply

def validate_device(dev):
    if (dev.idVendor,dev.idProduct,dev.bcdDevice)!=(VID,PID,BCD_DEVICE) or dev.product!=PRODUCT:
        raise ValueError('not the exact project USB console identity')
    interface=dev.get_active_configuration()[(0,0)]
    endpoints={e.bEndpointAddress:e for e in interface}
    if (interface.bInterfaceClass!=0xff or interface.bAlternateSetting!=0 or
            set(endpoints)!={1,0x81} or any(e.bmAttributes&3!=2 for e in endpoints.values())):
        raise ValueError('not the configured private bulk interface0')
    mps=endpoints[1].wMaxPacketSize
    if mps not in (64,512) or endpoints[0x81].wMaxPacketSize!=mps:raise ValueError('USB endpoint sizes invalid')
    return mps

def wait_device(find,seconds,*,clock=time.monotonic,sleep=time.sleep,event=lambda **kw:None):
    deadline=clock()+seconds;event(event='usb-wait-start',seconds=seconds,vid=VID,pid=PID,product=PRODUCT)
    while True:
        candidates=[]
        for dev in find():
            try:mps=validate_device(dev)
            except (ValueError,OSError):continue
            candidates.append((dev,mps))
        if len(candidates)>1:raise uart.ProtocolError('multiple matching project USB consoles')
        if candidates:return candidates[0]
        remaining=deadline-clock()
        if remaining<=0:raise uart.AckTimeout('exact configured project USB console not found before wait deadline')
        sleep(min(1,remaining))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vid',type=lambda s:int(s,0),default=VID)
    parser.add_argument('--pid',type=lambda s:int(s,0),default=PID)
    parser.add_argument('--wait',type=float,default=0,help='finite enumeration wait, e.g.300 before flash')
    parser.add_argument('--timeout',type=float,default=60)
    parser.add_argument('--ready-wait',type=float,default=0,help='optional finite native READY wait after enumeration, e.g.900')
    parser.add_argument('--retries',type=int,default=2)
    parser.add_argument('--log',type=Path,default=uart.DEFAULT_LOG.with_name('usb-console.jsonl'))
    sub=parser.add_subparsers(dest='mode',required=True)
    sub.add_parser('shell')
    smoke=sub.add_parser('smoke');smoke.add_argument('--reboot',action='store_true',help='explicit reboot after shell/status ACKs; no boot write')
    cmd=sub.add_parser('cmd');cmd.add_argument('command');cmd.add_argument('args',nargs=argparse.REMAINDER)
    exe=sub.add_parser('exec');exe.add_argument('path');exe.add_argument('args',nargs=argparse.REMAINDER)
    upload=sub.add_parser('upload');upload.add_argument('image',type=Path);upload.add_argument('--gzip',action='store_true');upload.add_argument('--reboot',action='store_true')
    args=parser.parse_args(argv)
    if (args.vid,args.pid)!=(VID,PID):parser.error('only exact board project VID0e8d PID20ff supported')
    if not math.isfinite(args.wait) or not 0<=args.wait<=300 or not math.isfinite(args.timeout) or args.timeout<=0 or args.retries<0 or not math.isfinite(args.ready_wait) or not 0<=args.ready_wait<=1200:parser.error('invalid finite wait/timeout/retries')
    dev=None;claimed=False
    try:
        import usb.core
        import usb.util
        args.log.parent.mkdir(parents=True,exist_ok=True)
        with args.log.open('a',encoding='utf-8') as log:
            def event(**fields):log.write(json.dumps({'time':time.time(),**fields})+'\n');log.flush()
            dev,mps=wait_device(lambda:usb.core.find(find_all=True,idVendor=VID,idProduct=PID),args.wait,event=event)
            usb.util.claim_interface(dev,0);claimed=True # No reset, configure, detach or UART.
            def read_reply(timeout):
                try:return bytes(dev.read(0x81,mps,timeout=timeout))
                except usb.core.USBTimeoutError as error:raise TimeoutError('USB IN timeout') from error
            def exchange(frame,timeout):
                try:
                    if dev.write(1,frame,timeout=timeout)!=len(frame):raise uart.ProtocolError('short USB write; transfer stopped')
                except usb.core.USBTimeoutError as error:raise TimeoutError('USB OUT timeout') from error
                return read_reply(timeout)
            stream=BulkStream(exchange,mps,retries=args.retries,read_reply=read_reply)
            stream.handshake(time.monotonic()+args.timeout)
            event(event='usb-console-connected',maxpacket=mps,identity='0e8d:20ff/0100/'+PRODUCT)
            def stdout(data):sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
            console=Console(stream,timeout=args.timeout,retries=args.retries,log=log,stdout=stdout)
            if args.ready_wait:console.wait_ready(args.ready_wait)
            else:console.drain()
            if args.mode=='smoke':
                console.command('status');console.command('help');console.command('pwd');console.command('ls','/')
                console.command('cat','/System/Library/CoreServices/SystemVersion.plist')
                if args.reboot:console.command('reboot')
                print(json.dumps({'smoke':'native-shell-and-status-ACKs','reboot_requested':args.reboot,'boot_write_performed':False}))
            elif args.mode=='shell':interactive(console)
            elif args.mode=='upload':print(console.upload(args.image,gzip=args.gzip,reboot=args.reboot))
            elif args.mode=='exec':print(console.command('exec',args.path,*args.args))
            else:print(console.command(args.command,*args.args))
        return 0
    except (ImportError,OSError,ValueError,uart.ProtocolError) as error:
        print('USB console failed: '+str(error),file=sys.stderr);return 1
    finally:
        if claimed:usb.util.release_interface(dev,0)
        if dev is not None:usb.util.dispose_resources(dev)

if __name__=='__main__':raise SystemExit(main())
