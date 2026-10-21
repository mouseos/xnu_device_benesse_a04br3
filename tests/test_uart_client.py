# SPDX-License-Identifier: MIT
import base64
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import uart_client as uart


class Sim:
    """Pipe request transport, append-only collector log, simulated kernel ACKs."""
    def __init__(self, directory, *, drop=False, bad_offset=False, commit_error=False, drop_command=None):
        self.raw_path = Path(directory) / 'raw'
        self.raw_path.write_bytes(b'old noise\n!A04 old OK stale\n')
        self.reader = uart.RawLog(self.raw_path)
        self.pipe_r, self.pipe_w = os.pipe()
        self.sent = []
        self.cache = {}
        self.data = bytearray()
        self.commits = 0
        self.drop = drop
        self.drop_command = drop_command
        self.bad_offset = bad_offset
        self.commit_error = commit_error
        self.sha = None
        self.total = None
        self.now = 0.0
        self.metadata = io.StringIO()
        self.client = uart.Client(self.write, self.reader.read, timeout=0.02,
                                  retries=2, clock=lambda: self.now,
                                  sleep=self.sleep, log=self.metadata)

    def sleep(self, amount):
        self.now += amount

    def write(self, line):
        uart.write_all(self.pipe_w, line)
        received = os.read(self.pipe_r, 2048)
        assert received == line
        self.sent.append(line)
        fields = line.decode().split()
        _, rid, command, *args = fields
        if rid in self.cache:
            previous, reply = self.cache[rid]
            assert previous == line, 'retry changed the cached request'
        else:
            detail = 'ready'
            status = 'OK'
            if command == 'boot-begin':
                self.total, self.sha = int(args[0]), args[1]
            elif command == 'boot-data':
                assert int(args[0]) == len(self.data)
                self.data.extend(base64.b64decode(args[1], validate=True))
                detail = 'next=' + str(len(self.data) + int(self.bad_offset))
            elif command == 'boot-commit':
                self.commits += 1
                assert len(self.data) == self.total
                assert hashlib.sha256(self.data).hexdigest() == self.sha
                if self.commit_error:
                    status, detail = 'ERR', '5 verification-failed'
                else:
                    detail = 'verified-flushed'
            reply = f'!A04 {rid} {status} {detail}\r\n'.encode()
            self.cache[rid] = (line, reply)
            if self.drop and (self.drop_command is None or command == self.drop_command):
                self.drop = False
                return
        with self.raw_path.open('ab') as log:
            log.write(b'noise\n' + line + b'!A04 foreign OK other\n' + reply)

    def close(self):
        self.reader.close()
        os.close(self.pipe_r)
        os.close(self.pipe_w)


class UARTClientTests(unittest.TestCase):
    def make_sim(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        sim = Sim(temp.name, **kwargs)
        self.addCleanup(sim.close)
        return sim, Path(temp.name)

    def test_noise_echo_foreign_id_and_split_records(self):
        parser = uart.ReplyParser(limit=80)
        chunks = [b'noise !A04 id OK fake\n@A04 id status\n!A04 oth',
                  b'er OK foreign\n!A04 id O', b'K ready\r', b'\n']
        result = []
        for chunk in chunks:
            result.extend(parser.feed(chunk))
        self.assertEqual(result, [('other', 'OK', 'foreign'), ('id', 'OK', 'ready')])
        self.assertEqual(parser.feed(b'x' * 100 + b'!A04 id OK fake\n!A04 id ERR 5\n'), [('id', 'ERR', '5')])

    def test_append_log_baseline_and_pipe_retry_idempotence(self):
        sim, _ = self.make_sim(drop=True)
        self.assertEqual(sim.client.request('status', request_id='A1'), 'ready')
        self.assertEqual(sim.sent, [b'@A04 A1 status\n'] * 2)
        with self.assertRaises(ValueError):
            sim.client.request('status', request_id='A1')
        self.assertNotIn('ready', sim.metadata.getvalue())

    def test_upload_stream_hash_chunks_offsets_no_default_reboot(self):
        sim, directory = self.make_sim(drop=True)
        image = directory / 'boot.img'
        content = bytes(range(256)) * 4
        image.write_bytes(content)
        self.assertEqual(sim.client.upload(image), 'verified-flushed')
        self.assertEqual(sim.data, content)
        self.assertEqual(sim.sha, hashlib.sha256(content).hexdigest())
        self.assertEqual(sim.commits, 1)
        data_lines = [line.split() for line in sim.sent if b' boot-data ' in line]
        self.assertEqual([int(line[3]) for line in data_lines], [0, 384, 768])
        self.assertEqual([len(base64.b64decode(line[4])) for line in data_lines], [384, 384, 256])
        self.assertFalse(any(b' reboot' in line for line in sim.sent))
        self.assertNotIn(base64.b64encode(content[:384]).decode(), sim.metadata.getvalue())

    def test_data_ack_lost_replays_identical_line_without_duplicate_bytes(self):
        sim, directory = self.make_sim(drop=True, drop_command='boot-data')
        image = directory / 'boot'; image.write_bytes(b'z' * 500)
        sim.client.upload(image)
        data = [line for line in sim.sent if b' boot-data ' in line]
        self.assertEqual(data[0], data[1])
        self.assertNotEqual(data[1], data[2])
        self.assertEqual(sim.data, b'z' * 500)
        self.assertEqual(sim.commits, 1)

    def test_changed_source_prevents_commit(self):
        sim, directory = self.make_sim()
        image = directory / 'boot'; image.write_bytes(b'original')
        original = sim.client.request
        def request(command, *args, **kwargs):
            result = original(command, *args, **kwargs)
            if command == 'boot-begin':
                image.write_bytes(b'modified')
            return result
        sim.client.request = request
        with self.assertRaises(uart.ProtocolError):
            sim.client.upload(image, reboot=True)
        self.assertEqual(sim.commits, 0)
        self.assertFalse(any(b' reboot' in line for line in sim.sent))

    def test_remote_error_not_retried_and_split_ack_reassembled(self):
        sim, _ = self.make_sim()
        sim.client.timeout = 0.1
        reads = iter([b'@A04 test status\n!A04 foreign OK x\n!A04 te', b'st ERR 22 bad', b'\n'])
        sim.client.read = lambda: next(reads, b'')
        with self.assertRaises(uart.RemoteError):
            sim.client.request('status', request_id='test')
        self.assertEqual(len(sim.sent), 1)

    def test_write_all_handles_short_write_and_interruption(self):
        remaining = []
        def write(fd, data):
            remaining.append(bytes(data))
            if len(remaining) == 1:
                raise InterruptedError()
            return min(2, len(data))
        with mock.patch.object(uart.os, 'write', side_effect=write):
            uart.write_all(42, b'abcde')
        self.assertEqual(remaining, [b'abcde', b'abcde', b'cde', b'e'])

    def test_failed_commit_never_reboots(self):
        sim, directory = self.make_sim(commit_error=True)
        image = directory / 'boot'; image.write_bytes(b'image')
        with self.assertRaises(uart.RemoteError):
            sim.client.upload(image, reboot=True)
        self.assertEqual(sim.commits, 1)
        self.assertFalse(any(b' reboot' in line for line in sim.sent))

    def test_wrong_offset_prevents_commit_and_reboot(self):
        sim, directory = self.make_sim(bad_offset=True)
        image = directory / 'boot'; image.write_bytes(b'image')
        with self.assertRaises(uart.ProtocolError):
            sim.client.upload(image, reboot=True)
        self.assertEqual(sim.commits, 0)
        self.assertFalse(any(b' reboot' in line for line in sim.sent))

    def test_explicit_reboot_after_commit_once(self):
        sim, directory = self.make_sim()
        image = directory / 'boot'; image.write_bytes(b'image')
        sim.client.upload(image, reboot=True)
        self.assertIn(b' boot-commit', sim.sent[-2])
        self.assertIn(b' reboot', sim.sent[-1])
        self.assertEqual(sum(b' reboot' in line for line in sim.sent), 1)

    def test_timeouts_and_reboot_ack_loss_not_retried(self):
        sim, _ = self.make_sim()
        sim.client.read = lambda: b''
        with self.assertRaises(uart.AckTimeout):
            sim.client.request('status')
        self.assertEqual(len(sim.sent), 3)
        self.assertEqual(len(set(sim.sent)), 1)
        sim.sent.clear()
        with self.assertRaises(uart.AckTimeout):
            sim.client.request('reboot')
        self.assertEqual(len(sim.sent), 1)

    def test_single_outstanding_and_validation(self):
        sim, _ = self.make_sim()
        sim.client.lock.acquire()
        with self.assertRaises(uart.ProtocolError):
            sim.client.request('status')
        sim.client.lock.release()
        self.assertEqual(sim.sent, [])
        for rid in ('', 'a' * 33, 'x-y', '日本語'):
            with self.assertRaises(ValueError):
                uart.request_line(rid, 'status', ())
        for command, args in [('exec', ('relative',)), ('exec', ('/bin/sh', 'a b')),
                              ('exec', ('/bin/sh', '"arg"')), ('status', ('x',)),
                              ('boot-data', ('0', base64.b64encode(b'x' * 385).decode())),
                              ('boot-begin', ('1', 'bad')), ('boot-data', ('-1', 'eA=='))]:
            with self.assertRaises(ValueError):
                uart.request_line('x', command, args)

    def test_log_truncation_and_nonregular_rejected(self):
        sim, _ = self.make_sim()
        sim.raw_path.write_bytes(b'')
        with self.assertRaises(uart.ProtocolError):
            sim.reader.read()
        with mock.patch.object(uart, 'open', create=True) as opened:
            with self.assertRaises(ValueError):
                uart.RawLog('/dev/null')
            opened.assert_not_called()

    def test_cli_opens_uart_write_only_and_never_reads_it(self):
        sim, directory = self.make_sim()
        log = directory / 'host.jsonl'
        uart_path = directory / 'uart'; uart_path.write_bytes(b'')
        real_open = os.open
        flags_seen = []
        def open_fd(path, flags, *args, **kwargs):
            if str(path) == str(uart_path):
                flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)
        def write_fd(fd, line):
            fields = line.split()
            with sim.raw_path.open('ab') as output:
                output.write(b'\n!A04 ' + fields[1] + b' OK ready\n')
        with mock.patch.object(uart.os, 'open', side_effect=open_fd), \
             mock.patch.object(uart, 'write_all', side_effect=write_fd), \
             mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(uart.main(['--uart', str(uart_path), '--raw-log', str(sim.raw_path),
                                        '--log', str(log), 'status']), 0)
        self.assertEqual(flags_seen, [os.O_WRONLY | os.O_NOCTTY])
        self.assertEqual(uart_path.read_bytes(), b'')
        self.assertTrue(log.read_text())


if __name__ == '__main__':
    unittest.main()
