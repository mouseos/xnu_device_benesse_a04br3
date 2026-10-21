# SPDX-License-Identifier: MIT
"""Two real host processes and a PTY, never the tablet or its collector."""
import importlib.util
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import sys
import tempfile
import time
import tty
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
LINE=re.compile(rb'@A04 [0-9a-f]{32} (?:status|exec /usr/appleinternal/sbin/a04-fb-capture --uart-preview [0-9a-f]{32})\n')

class WriterLock(unittest.TestCase):
    def pair(self,first,second,expect_busy=True):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw=root/'collector';raw.write_bytes(b'existing boot\n')
            master,slave=pty.openpty();tty.setraw(slave)
            alias=root/'tty-alias';alias.symlink_to(os.ttyname(slave))
            children=[];wire=bytearray()
            def command(kind,name,interval,timeout,path):
                common=['--uart',str(path),'--raw-log',str(raw),'--timeout',str(timeout),'--tx-interval',str(interval)]
                if kind=='status':return [sys.executable,str(TOOLS/'uart_client.py'),*common,'--retries','0','--log',str(root/(name+'.jsonl')),'status']
                return [sys.executable,str(TOOLS/'a04-fb-retrieve.py'),*common,'--output',str(root/(name+'.png'))]
            try:
                a=subprocess.Popen(command(first,'a',.01,1.8,os.ttyname(slave)),stdout=subprocess.PIPE,stderr=subprocess.PIPE);children.append(a)
                end=time.monotonic()+1
                while len(wire)<8:
                    self.assertTrue(select.select([master],[],[],max(0,end-time.monotonic()))[0],'first process did not send')
                    wire.extend(os.read(master,4096))
                started=time.monotonic()
                b=subprocess.Popen(command(second,'b',.001,.4,alias),stdout=subprocess.PIPE,stderr=subprocess.PIPE);children.append(b)
                out,err=b.communicate(timeout=1)
                elapsed=time.monotonic()-started
                if expect_busy:
                    self.assertNotEqual(b.returncode,0,out)
                    self.assertIn(b'writer busy',err)
                    self.assertLess(elapsed,.8)
                # Deliberately no ACK: first writer must keep the whole protocol
                # transaction, not just one paced write(), exclusively owned.
                a.communicate(timeout=3)
                while select.select([master],[],[],.05)[0]:wire.extend(os.read(master,4096))
                if expect_busy:
                    self.assertIsNotNone(LINE.fullmatch(wire),wire)
                    self.assertEqual(wire.count(b'@A04'),1)
                    if second=='capture':self.assertFalse((root/'b.png').exists())
                else:
                    self.assertIsNone(LINE.fullmatch(wire))
                    self.assertEqual(wire.count(b'\n'),2,wire)
                    self.assertFalse(all(LINE.fullmatch(line+b'\n') for line in wire.splitlines()),wire)
                return bytes(wire)
            finally:
                for process in children:
                    if process.poll() is None:process.kill()
                    process.communicate()
                os.close(master);os.close(slave)

    def test_two_cli_instances_busy_reject_without_interleaving(self):self.pair('status','status')
    def test_cli_holds_lock_against_framebuffer_wrapper(self):self.pair('status','capture')
    def test_framebuffer_wrapper_holds_lock_against_cli(self):self.pair('capture','status')

    def test_writer_process_death_releases_lock_and_collector_still_reads(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);master,slave=pty.openpty();tty.setraw(slave)
            code='import sys;sys.path.insert(0,sys.argv[1]);import uart_client as u;fd=u.open_writer(sys.argv[2]);print("LOCKED",flush=True);sys.stdin.read()'
            child=subprocess.Popen([sys.executable,'-c',code,str(TOOLS),os.ttyname(slave)],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline(),b'LOCKED\n')
                # Single collector is independent, read-side flow still works.
                os.write(master,b'collector data');self.assertEqual(os.read(slave,64),b'collector data')
                child.kill();child.communicate(timeout=1)
                sys.path.insert(0,str(TOOLS));import uart_client
                fd=uart_client.open_writer(os.ttyname(slave))
                try:os.write(fd,b'new owner\n');self.assertEqual(os.read(master,64),b'new owner\n')
                finally:os.close(fd)
            finally:
                if child.poll() is None:child.kill()
                child.communicate();os.close(master);os.close(slave)

if __name__=='__main__':unittest.main()
