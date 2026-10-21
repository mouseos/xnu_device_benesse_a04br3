# SPDX-License-Identifier: MIT
"""Actual host CLI with real saturated PTYs; no board or disk-update access."""
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'


def saturate(fd):
    os.set_blocking(fd,False)
    total=0;stable=0
    # PTYs move bytes asynchronously between two queues. Fill even one-byte
    # writes and require 200ms of stable EAGAIN, not just a short large write.
    until=time.monotonic()+2
    while time.monotonic()<until:
        try:
            total+=os.write(fd,b'x'*4096);stable=0
        except BlockingIOError:
            try:
                total+=os.write(fd,b'x');stable=0
            except BlockingIOError:
                stable+=1
                if stable>=20:return total
                time.sleep(.01)
    raise AssertionError('PTY did not become stably backpressured')



class TXDeadline(unittest.TestCase):
    def run_stall(self,after_begin):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);raw=p/'raw';raw.write_bytes(b'old boot\n');meta=p/'metadata';image=p/'image';image.write_bytes(b'image')
            master,slave=pty.openpty();tty.setraw(slave)
            errors=[];seen=[]
            thread=None
            try:
                if after_begin:
                    def remote():
                        try:
                            line=b''
                            while not line.endswith(b'\n'):
                                if not select.select([master],[],[],2)[0]:raise AssertionError('missing BEGIN')
                                line+=os.read(master,2048)
                            seen.append(line)
                            self.assertIn(b' boot-begin ',line)
                            self.assertGreater(saturate(slave),0)
                            with raw.open('ab') as stream:
                                stream.write(b'\n!A04 '+line.split()[1]+b' OK offset=0\n')
                        except BaseException as error:errors.append(error)
                    thread=threading.Thread(target=remote,daemon=True);thread.start()
                else:self.assertGreater(saturate(slave),0)
                args=[sys.executable,str(TOOLS/'uart_client.py'),'--uart',os.ttyname(slave),
                      '--raw-log',str(raw),'--log',str(meta),'--timeout',('.4' if after_begin else '.15'),'--retries','2',
                      '--tx-interval','0']
                args+=['upload',str(image),'--reboot'] if after_begin else ['status']
                started=time.monotonic()
                try:result=subprocess.run(args,capture_output=True,timeout=1.5)
                except subprocess.TimeoutExpired:self.fail('actual CLI hung in TX beyond configured timeout')
                self.assertLess(time.monotonic()-started,1)
                self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertIn(b'deadline',result.stderr.lower())
                if thread:thread.join(1);self.assertFalse(thread.is_alive())
                self.assertFalse(errors,errors)
                events=[json.loads(line) for line in meta.read_text().splitlines()]
                sends=[event['command'] for event in events if event['event']=='send']
                self.assertEqual(sends,['boot-begin','boot-data'] if after_begin else ['status'])
                self.assertNotIn('boot-commit',sends);self.assertNotIn('reboot',sends)
            finally:
                os.close(master);os.close(slave)

    def test_cli_stalled_first_tx_has_finite_deadline(self):self.run_stall(False)
    def test_cli_stalled_upload_data_never_commits_reboots_or_retries_partial_line(self):self.run_stall(True)

if __name__=='__main__':unittest.main()
