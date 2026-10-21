#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Private PING/INFO/CRC-stream client; NO boot/disk/write/commit/reset commands.
No USB discovery or imports happen until explicit --vid and --pid execution.
Requires an already enumerated/configured private vendor interface0 EP1 pair.
This is not a gadget driver. CRC success is transport evidence only.
"""
import argparse
import struct
import zlib
MAGIC=0x34305541
HEADER=struct.Struct('<IHH6I')
MAX_STREAM=32*1024*1024

def encode(op,seq,offset=0,data=b''):
    if not 0<seq<0xffffffff or not 0<=offset<=MAX_STREAM or len(data)>480:
        raise ValueError('bounded sequence/offset/data required')
    header=bytearray(HEADER.pack(MAGIC,1,op,seq,offset,len(data),zlib.crc32(data),0,0))
    struct.pack_into('<I',header,24,zlib.crc32(header))
    return bytes(header)+data

def decode_reply(frame,op,seq):
    if len(frame)!=64:
        raise ValueError('reply must be64 bytes')
    magic,version,kind,seen,offset,size,crc,hcrc,reserved=HEADER.unpack_from(frame)
    header=bytearray(frame[:32]);header[24:28]=bytes(4)
    if (magic,version,kind,seen,size,reserved)!=(MAGIC,1,op|0x8000,seq,32,0) or zlib.crc32(header)!=hcrc or zlib.crc32(frame[32:])!=crc:
        raise ValueError('reply identity/bounds/CRC mismatch')
    status,next_seq,total,received,running,active,limit,chunk=struct.unpack_from('<8I',frame,32)
    if offset!=received or received>total or total>MAX_STREAM or active>1 or limit!=MAX_STREAM or chunk not in (32,480):
        raise ValueError('reply state out of bounds')
    return dict(status=status,next=next_seq,total=total,received=received,crc=running,active=active,chunk=chunk)

class Client:
    def __init__(self,exchange,maxpacket):
        if maxpacket not in (64,512):raise ValueError('FS/HS endpoint required')
        self.exchange=exchange;self.maxpacket=maxpacket;self.seq=1
    def request(self,op,offset=0,data=b''):
        frame=encode(op,self.seq,offset,data)
        if len(frame)>self.maxpacket:raise ValueError('single USB packet required')
        reply=decode_reply(self.exchange(frame),op,self.seq)
        if reply['status'] or reply['next']!=self.seq+1:
            raise RuntimeError(f"transport refused status={reply['status']}; no flash action")
        self.seq=reply['next'];return reply
    def crc_stream(self,stream):
        total=crc=0
        while True:
            block=stream.read(65536)
            if not block:break
            total+=len(block)
            if total>MAX_STREAM:raise ValueError('stream exceeds32MiB')
            crc=zlib.crc32(block,crc)
        if not total:raise ValueError('empty stream')
        stream.seek(0);self.request(3,0,struct.pack('<II',total,crc));received=0
        while received<total:
            block=stream.read(min(self.maxpacket-32,total-received))
            if not block:raise ValueError('stream changed/truncated')
            reply=self.request(4,received,block);received+=len(block)
            if reply['received']!=received:raise ValueError('offset mismatch')
        reply=self.request(5,total)
        if reply['crc']!=crc or reply['active']:raise ValueError('final CRC/state mismatch')
        return reply

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vid',required=True,type=lambda x:int(x,0));p.add_argument('--pid',required=True,type=lambda x:int(x,0))
    p.add_argument('--crc-file',help='CRC-only stream; never installs/writes boot')
    args=p.parse_args()
    if not 0<args.vid<=65535 or not 0<args.pid<=65535:p.error('explicit assigned16-bit VID/PID required')
    import usb.core
    import usb.util
    dev=usb.core.find(idVendor=args.vid,idProduct=args.pid)
    if dev is None:raise RuntimeError('selected device absent')
    interface=dev.get_active_configuration()[(0,0)]
    endpoints={e.bEndpointAddress:e for e in interface}
    if interface.bInterfaceClass!=0xff or set(endpoints)!={1,0x81} or any(e.bmAttributes&3!=2 for e in endpoints.values()):
        raise RuntimeError('not the explicitly configured private bulk pair')
    mps=endpoints[1].wMaxPacketSize
    if endpoints[0x81].wMaxPacketSize!=mps:raise RuntimeError('endpoint size mismatch')
    usb.util.claim_interface(dev,0) # no detach_kernel_driver/set_configuration/reset
    try:
        def exchange(frame):
            if dev.write(1,frame,timeout=3000)!=len(frame):raise RuntimeError('short bulk write')
            return bytes(dev.read(0x81,64,timeout=3000))
        client=Client(exchange,mps);client.request(1);print('INFO',client.request(2))
        if args.crc_file:
            with open(args.crc_file,'rb') as stream:print('CRC TRANSPORT ONLY',client.crc_stream(stream))
    finally:usb.util.release_interface(dev,0)
if __name__=='__main__':main()
