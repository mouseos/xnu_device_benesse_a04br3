# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Host PTY/collector simulation only; no board/display/hardware proof."""
import importlib.util
import json
import os
from pathlib import Path
import pty
import sys
import tempfile
import threading
import tty
import time
import unittest
from unittest import mock

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
retrieve=load('a04_fb_retrieve',TOOLS/'a04-fb-retrieve.py')
oracle=load('fb_frame_oracle',Path(__file__).with_name('test_fb_capture_decode.py'))

class Retrieve(unittest.TestCase):
    def exchange(self,root,mode):
        master,slave=pty.openpty();tty.setraw(slave) # Fixture owns PTY configuration; production does not.
        log=root/'collector.log';log.write_bytes(b'old boot\n'+b''.join(oracle.records()))
        output=root/'screen.png';record=root/'capture.jsonl';commands=[];errors=[]
        def remote():
            try:
                line=b''
                while not line.endswith(b'\n'):line+=os.read(master,2048)
                commands.append(line)
                fields=line.decode().split();request_id=fields[1];frame_id=fields[-1]
                self.assertEqual(fields[2:5],['exec',retrieve.HELPER,'--uart-preview'])
                body=b''.join(oracle.records(frame_id=frame_id))
                # All pixel byte values survive because only Base64 ASCII is transmitted.
                body=body.replace(b'\n',b'\r\r\n')
                if mode=='partial':body=body[:len(body)//2]
                if mode=='corrupt':body=body.replace(b'DATA 0 384',b'DATA 1 384',1)
                if mode in ('noack','ackonly'):body=b''
                if mode=='replaced':log.unlink();log.write_bytes(b'new collector\n')
                if mode=='race-output':output.write_bytes(b'keep competing output')
                with log.open('ab') as stream:
                    stream.write(body)
                    if mode!='noack':stream.write(f'\n!A04 {request_id} OK wait-status={256 if mode=="badexit" else 0}\r\n'.encode())
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=remote,daemon=True);thread.start()
        try:
            if mode=='ok':
                result=retrieve.retrieve(os.ttyname(slave),log,output,record,timeout=2,tx_interval=0)
                self.assertEqual(output.read_bytes(),retrieve.decoder.png_from_ppm(oracle.PPM))
                self.assertGreater(result['baseline'],0)
                self.assertEqual((result['width'],result['height']),(200,320))
            else:
                with self.assertRaises((ValueError,OSError,retrieve.uart_client.ProtocolError)):
                    retrieve.retrieve(os.ttyname(slave),log,output,record,timeout=.25,tx_interval=0)
                if mode=='race-output':self.assertEqual(output.read_bytes(),b'keep competing output')
                else:self.assertFalse(output.exists())
            thread.join(2);self.assertFalse(thread.is_alive());self.assertFalse(errors,errors)
            self.assertEqual(len(commands),1) # No automatic retransmission of exec.
            events=[json.loads(line) for line in record.read_text().splitlines()]
            start=events[0];self.assertEqual(start['event'],'capture-start')
            self.assertIn(start['frame_id'].encode(),commands[0]);self.assertIn(start['request_id'].encode(),commands[0])
            self.assertEqual(events[-1]['event'],'capture-verified' if mode=='ok' else 'capture-failed')
            self.assertEqual(len([event for event in events if event['event']=='send']),1)
        finally:os.close(master);os.close(slave)

    def test_actual_client_pty_collector_to_verified_png(self):
        with tempfile.TemporaryDirectory() as d:self.exchange(Path(d),'ok')

    def test_ack_alone_bad_exit_partial_corrupt_rotated_timeout_and_racing_output_fail(self):
        for mode in ('partial','corrupt','badexit','noack','ackonly','replaced','race-output'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:self.exchange(Path(d),mode)

    def test_reject_existing_image_alias_and_invalid_timeout_before_send(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);image=p/'image';image.write_bytes(b'old');log=p/'log';log.write_bytes(b'log')
            for timeout in (0,-1,float('inf'),float('nan')):
                with self.assertRaises(ValueError):retrieve.retrieve(p/'uart',log,image,p/'record',timeout=timeout)
            with self.assertRaises(FileExistsError):retrieve.retrieve(p/'uart',log,image,p/'record')
            with self.assertRaises(ValueError):retrieve.retrieve(p/'uart',log,log,p/'record')
            self.assertEqual(image.read_bytes(),b'old');self.assertFalse((p/'record').exists())

    def test_decoder_checks_open_file_identity(self):
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'collector';log.write_bytes(b''.join(oracle.records()))
            info=log.stat()
            self.assertEqual(retrieve.decoder.decode_log(log,oracle.ID,0,(info.st_dev,info.st_ino)),oracle.PPM)
            with self.assertRaises(retrieve.decoder.CaptureError):
                retrieve.decoder.decode_log(log,oracle.ID,0,(info.st_dev,info.st_ino+1))

    def test_stalled_nonblocking_uart_tx_expires_without_retransmission_or_png(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);uart=p/'uart';uart.touch();log=p/'collector';log.write_bytes(b'old boot\n')
            def stalled(*args):
                time.sleep(.01)
                return ([],[],[])
            started=time.monotonic()
            with mock.patch.object(retrieve.os,'write',side_effect=BlockingIOError()),mock.patch.object(retrieve.select,'select',side_effect=stalled):
                with self.assertRaises(retrieve.uart_client.AckTimeout):
                    retrieve.retrieve(uart,log,p/'image',p/'record',timeout=.05,tx_interval=0)
            self.assertLess(time.monotonic()-started,1)
            self.assertFalse((p/'image').exists())
            events=[json.loads(line) for line in (p/'record').read_text().splitlines()]
            self.assertEqual(len([event for event in events if event['event']=='send']),1)
            self.assertEqual(events[-1]['event'],'capture-failed')

    def test_atomic_publish_invisible_until_fsync_no_overwrite_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);out=p/'screen.png';data=retrieve.decoder.png_from_ppm(oracle.PPM)
            real_fsync=os.fsync
            def check(fd):
                self.assertFalse(out.exists());real_fsync(fd)
            with mock.patch.object(retrieve.decoder.os,'fsync',side_effect=check):retrieve.decoder.publish_image(out,data)
            self.assertEqual(out.read_bytes(),data)
            with self.assertRaises(FileExistsError):retrieve.decoder.publish_image(out,b'bad')
            self.assertEqual(out.read_bytes(),data);out.unlink()
            with mock.patch.object(retrieve.decoder.os,'fsync',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):retrieve.decoder.publish_image(out,data)
            self.assertEqual(list(p.iterdir()),[])

if __name__=='__main__':unittest.main()
