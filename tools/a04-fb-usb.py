#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Capture the panel over the USB root console (no UART) and write a PNG.

Runs the installed helper "a04-fb-capture --uart-preview ID SCALE" through
usb_console.py exec, keeps the complete console transcript next to the image,
and decodes it with the same CRC/SHA-verified decoder used for UART captures.
SCALE 1 = full 800x1280, 2 = 400x640, 4 = 200x320 (requires the helper from
the device revision with scale support pinned by the running kernel).
"""
import argparse,importlib.util,subprocess,sys,uuid
from pathlib import Path
TOOLS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('a04_fb_decode',TOOLS/'a04-fb-decode.py')
decoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(decoder)
HELPER='/usr/appleinternal/sbin/a04-fb-capture'
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scale',choices=('1','2','4'),default='1')
    p.add_argument('--timeout',type=float,default=180)
    p.add_argument('--no-zlib',action='store_true',help='uncompressed P6 records (helper without z support)')
    a=p.parse_args(argv)
    if a.output.exists():raise SystemExit('output exists')
    frame=uuid.uuid4().hex
    transcript=a.output.with_name(a.output.name+'.usb.txt')
    cmd=[sys.executable,str(TOOLS/'usb_console.py'),'--wait','10','--timeout',str(int(a.timeout)),
         'cmd','exec',HELPER,'--uart-preview',frame,a.scale]+([] if a.no_zlib else ['z'])
    with transcript.open('xb') as out:
        r=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT,timeout=a.timeout+30)
    data=transcript.read_bytes()
    if r.returncode!=0:print(f'console exit {r.returncode}; decoding transcript anyway',file=sys.stderr)
    ppm=decoder.decode_chunks(iter([data]),frame,recover_interleaved=True)
    decoder.publish_image(a.output,decoder.png_from_ppm(ppm))
    print(f'A04FB USB capture frame={frame} scale={a.scale} output={a.output}')
if __name__=='__main__':main()
