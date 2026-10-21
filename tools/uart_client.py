#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Write canonical ASCII requests; read replies ONLY from the existing UART log.

No termios access and no UART reads. boot-data OK detail must acknowledge the
NEXT byte offset: decimal, next=N, offset=N, next N, or offset N.
The device is responsible for full SHA/header/bounds/write/readback/flush checks
before acknowledging boot-commit. Upload does not reboot by default.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import stat
import sys
import threading
import time
import uuid
import zlib

BLOCK_BYTES = 384
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG = ROOT / 'gsi/logs/ios-feasibility/self-update/uart-client.jsonl'
ID_RE = re.compile(r'[A-Za-z0-9]{1,32}\Z')
REPLY_RE = re.compile(rb'!A04 ([A-Za-z0-9]{1,32}) (OK|ERR)(?: ([\x20-\x7e]*))?\r*\n\Z')
NEXT_RE = re.compile(r'(?:(?:next|offset)[= ])?([0-9]+)\Z')
STATUS_RE = re.compile(r'version=(0|[1-9][0-9]{0,19}) flags=(0|[1-9][0-9]{0,19}) partition=(0|[1-9][0-9]{0,19}) staged=(0|[1-9][0-9]{0,19}) image=(0|[1-9][0-9]{0,19})\Z')
DATA_RECONCILE_RETRIES = 2
GZIP_ACK_RE = re.compile(r'offset=(0|[1-9][0-9]{0,19}) raw=(0|[1-9][0-9]{0,19})\Z')
GZIP_RAW_MAX = 128 * 1024 * 1024
GZIP_INPUT_MAX = GZIP_RAW_MAX + 1024 * 1024
COMMANDS = {'status', 'sample', 'boot-begin', 'boot-data', 'boot-commit', 'boot-abort', 'reboot', 'exec', 'boot-zbegin', 'boot-zdata', 'boot-zfinish'}


class ProtocolError(RuntimeError):
    pass


class AckTimeout(ProtocolError):
    pass


class TransmissionTimeout(AckTimeout):
    """TX may be partial; never follow it with reconciliation commands."""
    pass


class RemoteError(ProtocolError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class ReplyParser:
    """Bound memory, require a genuine line prefix; ignore echo and noise."""
    def __init__(self, limit=4096):
        self.pending = bytearray()
        self.discard = False
        self.limit = limit

    def feed(self, data):
        replies = []
        for byte in data:
            if self.discard:
                if byte == 10:
                    self.discard = False
                continue
            self.pending.append(byte)
            if len(self.pending) > self.limit:
                self.pending.clear()
                self.discard = byte != 10
            elif byte == 10:
                match = REPLY_RE.fullmatch(self.pending)
                if match:
                    replies.append(tuple((v or b'').decode('ascii') for v in match.groups()))
                self.pending.clear()
        return replies


class RawLog:
    """Follow a single collector file from a baseline, never open the UART."""
    def __init__(self, path, baseline=None):
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise ValueError('raw log must be a regular file, never a UART')
        self.file = open(path, 'rb', buffering=0)
        if not stat.S_ISREG(os.fstat(self.file.fileno()).st_mode):
            self.file.close()
            raise ValueError('raw log changed to a nonregular file')
        size = os.fstat(self.file.fileno()).st_size
        if baseline is None:
            baseline = size
        if baseline < 0 or baseline > size:
            self.file.close()
            raise ValueError('baseline outside existing raw log')
        self.file.seek(baseline)

    def read(self):
        if os.fstat(self.file.fileno()).st_size < self.file.tell():
            raise ProtocolError('collector log truncated during request')
        return self.file.read(65536)

    def close(self):
        self.file.close()


def request_line(request_id, command, args):
    if not ID_RE.fullmatch(request_id):
        raise ValueError('id must be 1..32 ASCII alphanumeric characters')
    if command not in COMMANDS:
        raise ValueError('unknown command')
    for arg in args:
        if not arg or any(ord(c) < 33 or ord(c) > 126 or c in '\"\'\\' for c in arg):
            raise ValueError('arguments must be unquoted printable ASCII tokens')
    if command in {'status', 'sample', 'boot-commit', 'boot-abort', 'reboot', 'boot-zfinish'} and args:
        raise ValueError('command takes no arguments')
    if command == 'exec' and (not args or not args[0].startswith('/')):
        raise ValueError('exec requires an absolute path')
    if command == 'boot-begin':
        if len(args) != 2 or not re.fullmatch(r'[0-9]+', args[0]) or int(args[0]) <= 0 or not re.fullmatch(r'[a-fA-F0-9]{64}', args[1]):
            raise ValueError('boot-begin requires positive bytes and SHA256 hex')
    if command == 'boot-data':
        if len(args) != 2 or not re.fullmatch(r'[0-9]+', args[0]):
            raise ValueError('boot-data requires nonnegative offset and BASE64')
        try:
            decoded = base64.b64decode(args[1], validate=True)
        except ValueError as error:
            raise ValueError('invalid BASE64') from error
        if not 1 <= len(decoded) <= BLOCK_BYTES:
            raise ValueError('boot-data decoded size must be 1..384')
    if command == 'boot-zbegin':
        if (len(args) != 3 or not re.fullmatch(r'[0-9]+', args[0]) or
                not re.fullmatch(r'[0-9]+', args[1]) or
                not 18 <= int(args[0]) <= GZIP_INPUT_MAX or
                not 6144 <= int(args[1]) <= GZIP_RAW_MAX or int(args[1]) % 2048 or
                not re.fullmatch(r'[a-fA-F0-9]{64}', args[2])):
            raise ValueError('boot-zbegin requires bounded compressed/raw lengths and raw SHA256')
    if command == 'boot-zdata':
        if len(args) != 3 or not re.fullmatch(r'[0-9]+', args[0]) or not re.fullmatch(r'[0-9a-f]{8}', args[1]):
            raise ValueError('boot-zdata requires offset, CRC32 hex and BASE64')
        try:
            decoded = base64.b64decode(args[2], validate=True)
        except ValueError as error:
            raise ValueError('invalid BASE64') from error
        if not 1 <= len(decoded) <= BLOCK_BYTES or zlib.crc32(decoded) != int(args[1], 16):
            raise ValueError('boot-zdata size/CRC mismatch')
    return ('@A04 ' + request_id + ' ' + ' '.join([command, *args]) + '\n').encode('ascii')


class Client:
    def __init__(self, write, read, *, timeout=5.0, retries=2, log=None,
                 clock=time.monotonic, sleep=time.sleep, write_deadline=None):
        if not math.isfinite(timeout) or timeout <= 0 or retries < 0:
            raise ValueError('invalid timeout/retries')
        self.write, self.read = write, read
        self.timeout, self.retries, self.log = timeout, retries, log
        self.clock, self.sleep = clock, sleep
        self.write_deadline = write_deadline
        self.parser = ReplyParser()
        self.lock = threading.Lock()
        # Reject caller reuse, which could trigger a cached response to a new command.
        self.used_ids = set()

    def event(self, **fields):
        if self.log:
            self.log.write(json.dumps({'time': time.time(), **fields}) + '\n')
            self.log.flush()

    def request(self, command, *args, request_id=None):
        request_id = request_id or uuid.uuid4().hex
        line = request_line(request_id, command, args)
        if not self.lock.acquire(blocking=False):
            raise ProtocolError('one request is already outstanding')
        try:
            if request_id in self.used_ids:
                raise ValueError('request id already used by this client')
            self.used_ids.add(request_id)
            # Reboot is never retried automatically, even if its ACK is lost.
            retries = 0 if command == 'reboot' else self.retries
            for attempt in range(retries + 1):
                self.event(event='send', id=request_id, command=command, attempt=attempt)
                # One attempt budget covers TX pacing/backpressure AND ACK.
                deadline = self.clock() + self.timeout
                try:
                    if self.write_deadline is None:
                        self.write(line)  # Injected transports must bound their own writes.
                    else:
                        self.write_deadline(line, deadline)
                except (OSError, ProtocolError) as error:
                    # A partially transmitted canonical line must never be
                    # retried/concatenated automatically after a TX failure.
                    self.event(event='tx-failed', id=request_id, command=command)
                    if isinstance(error, AckTimeout):
                        raise TransmissionTimeout(str(error)) from error
                    raise
                while True:
                    for reply_id, status, detail in self.parser.feed(self.read()):
                        if reply_id != request_id:
                            continue
                        self.event(event='reply', id=request_id, command=command, status=status)
                        if status == 'ERR':
                            raise RemoteError(f'{command}: {detail}', code=int(detail) if re.fullmatch(r'[0-9]{1,5}', detail) else None)
                        return detail
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        break
                    self.sleep(min(0.02, remaining))
            self.event(event='timeout', id=request_id, command=command)
            raise AckTimeout(f'{command}: no ACK after {retries + 1} transmission(s)')
        finally:
            self.lock.release()

    def upload_block(self, block, offset, total):
        """Reconcile bounded stage progress, never claim byte integrity here.

        Only the kernel whole-image SHA/header/GPT checks before COMMIT writes
        can verify the actual staged bytes, not an offset or a lost data ACK.
        """
        next_offset = offset + len(block)
        encoded = base64.b64encode(block).decode('ascii')
        for attempt in range(DATA_RECONCILE_RETRIES + 1):
            try:
                detail = self.request('boot-data', str(offset), encoded)
            except TransmissionTimeout:
                raise  # Sending status could concatenate a partially sent line.
            except (AckTimeout, RemoteError) as error:
                if isinstance(error, RemoteError) and error.code != 22:
                    raise
                detail = self.request('status')  # Read-only; fresh request ID.
                match = STATUS_RE.fullmatch(detail)
                if not match:
                    raise ProtocolError('unrecognized staging status; upload not committed')
                version, flags, partition, staged, image = map(int, match.groups())
                if (version != 1 or flags != 1 or image != total or
                        not total <= partition <= 0xffffffffffffffff or staged > total):
                    raise ProtocolError('staging state/size mismatch; upload not committed')
                if staged == next_offset:
                    self.event(event='data-reconciled', offset=offset, next=next_offset,
                               outcome='staged-unverified')
                    return
                if staged != offset:
                    raise ProtocolError('ambiguous staging offset; upload not committed')
                if attempt == DATA_RECONCILE_RETRIES:
                    raise ProtocolError('staging retry limit; upload not committed')
                self.event(event='data-reconcile-retry', offset=offset, attempt=attempt+1)
                continue  # New request ID, identical block; bounded retries.
            ack = NEXT_RE.fullmatch(detail)
            if not ack or int(ack[1]) != next_offset:
                raise ProtocolError('boot-data ACK does not match next byte offset')
            return

    def upload(self, path, *, reboot=False, compression='raw'):
        if compression == 'gzip':
            return self.upload_gzip(path, reboot=reboot)
        if compression != 'raw':
            raise ValueError('unsupported upload compression')
        with open(path, 'rb') as source:
            digest = hashlib.sha256()
            total = 0
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                total += len(block)
            if not total:
                raise ValueError('empty boot image')
            expected = digest.hexdigest()
            source.seek(0)
            self.request('boot-begin', str(total), expected)
            sent = hashlib.sha256()
            offset = 0
            while True:
                block = source.read(BLOCK_BYTES)
                if not block:
                    break
                if offset + len(block) > total:
                    raise ProtocolError('source image grew; upload not committed')
                self.upload_block(block, offset, total)
                sent.update(block)
                offset += len(block)
            if offset != total or sent.hexdigest() != expected:
                raise ProtocolError('source image changed; upload not committed')
            detail = self.request('boot-commit')
            if reboot:
                self.request('reboot')
            return detail

    def upload_gzip(self, path, *, reboot=False):
        """Explicit optional gzip; no fallback, raw-offset guessing or new-ID replay.

        Frozen native status exposes raw bytes only, not inflater/compressed
        progress. Raw progress cannot disambiguate acceptance of a compressed
        chunk, so exhausted cached ACK retries/ERR22 always stop before COMMIT.
        """
        import gzip
        import tempfile
        with open(path, 'rb') as source, tempfile.TemporaryFile() as packed:
            initial = os.fstat(source.fileno())
            if not 6144 <= initial.st_size <= GZIP_RAW_MAX or initial.st_size % 2048:
                raise ValueError('gzip upload requires bounded aligned boot image')
            digest = hashlib.sha256()
            total = 0
            with gzip.GzipFile(filename='', mode='wb', fileobj=packed, mtime=0) as encoder:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > initial.st_size:
                        raise ProtocolError('source image grew before gzip BEGIN')
                    digest.update(block)
                    encoder.write(block)
            final = os.fstat(source.fileno())
            if (total != initial.st_size or
                    (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) !=
                    (final.st_size, final.st_mtime_ns, final.st_ctime_ns)):
                raise ProtocolError('source image changed before gzip BEGIN')
            compressed = packed.tell()
            if not 18 <= compressed <= GZIP_INPUT_MAX:
                raise ValueError('compressed image exceeds native bounds')
            packed.seek(0)
            detail = self.request('boot-zbegin', str(compressed), str(total), digest.hexdigest())
            if detail != 'offset=0 raw=0':
                raise ProtocolError('gzip BEGIN ACK mismatch; upload not committed')
            offset = raw_staged = 0
            while True:
                block = packed.read(BLOCK_BYTES)
                if not block:
                    break
                next_offset = offset + len(block)
                detail = self.request('boot-zdata', str(offset), f'{zlib.crc32(block):08x}',
                                      base64.b64encode(block).decode('ascii'))
                match = GZIP_ACK_RE.fullmatch(detail)
                if not match or int(match[1]) != next_offset or not raw_staged <= int(match[2]) <= total:
                    raise ProtocolError('gzip data ACK mismatch; upload not committed')
                raw_staged, offset = int(match[2]), next_offset
            if offset != compressed:
                raise ProtocolError('compressed length changed; upload not committed')
            detail = self.request('boot-zfinish')
            if detail != f'offset={compressed} raw={total}':
                raise ProtocolError('gzip FINISH ACK mismatch; upload not committed')
            detail = self.request('boot-commit')  # Actual kernel SHA/header/GPT/readback gate.
            if reboot:
                self.request('reboot')
            return detail


def _remaining(deadline, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise AckTimeout('UART TX deadline expired; request was not retried')
    return remaining


def write_all(fd, data, *, deadline=None, clock=time.monotonic):
    view = memoryview(data)
    while view:
        if deadline is not None:
            _remaining(deadline, clock)
        try:
            count = os.write(fd, view)
        except InterruptedError:
            continue
        except BlockingIOError:
            if deadline is None:
                raise
            # CLI owns a nonblocking, write-only descriptor. Waiting for TX
            # readiness neither reads the UART nor changes shared termios.
            try:
                select.select([], [fd], [], min(0.05, _remaining(deadline, clock)))
            except InterruptedError:
                pass
            continue
        if count <= 0:
            raise ProtocolError('UART write made no progress')
        view = view[count:]


def write_paced(fd, data, *, burst=1, interval=0.005, sleep=time.sleep,
                deadline=None, clock=time.monotonic):
    """The board currently polls its small UART RX FIFO every16ms."""
    if burst < 1 or not math.isfinite(interval) or interval < 0:
        raise ValueError('invalid UART pacing')
    for at in range(0, len(data), burst):
        if deadline is None:
            write_all(fd, data[at:at+burst])
        else:
            write_all(fd, data[at:at+burst], deadline=deadline, clock=clock)
        if interval:
            sleep(interval if deadline is None else min(interval, _remaining(deadline, clock)))
        if deadline is not None:
            _remaining(deadline, clock)


def open_writer(path):
    """Advisory, cross-process TX ownership; readers/collector are unaffected.

    Lock the opened TTY inode itself, so symlink spellings share ownership.
    Never wait for a busy writer and never transmit before acquiring the lock.
    Hold the returned fd through the whole upload/exec+ACK transaction. Closing
    it (also on process exit) releases ownership without a stale lockfile.
    All participating host writers must use this API; old/raw writers do not.
    """
    fd = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProtocolError('UART writer busy; no request was sent') from error
        return fd
    except BaseException:
        os.close(fd)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uart', required=True, help='UART device (write-only; no termios changes)')
    parser.add_argument('--raw-log', required=True, help='existing single collector raw log')
    parser.add_argument('--baseline', type=int, help='raw log byte offset (default: size before first send)')
    parser.add_argument('--log', type=Path, default=DEFAULT_LOG, help='host metadata JSONL log')
    parser.add_argument('--timeout', type=float, default=5.0)
    parser.add_argument('--retries', type=int, default=2)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'sample', 'boot-abort', 'reboot'):
        sub.add_parser(name)
    execute = sub.add_parser('exec')
    execute.add_argument('path')
    execute.add_argument('args', nargs=argparse.REMAINDER)
    upload = sub.add_parser('upload')
    upload.add_argument('image', type=Path)
    upload.add_argument('--gzip', action='store_true', help='explicit optional native gzip; no automatic fallback')
    upload.add_argument('--reboot', action='store_true', help='reboot only after successful commit ACK')
    parser.add_argument('--tx-burst',type=int,default=1,help='UART bytes per paced write')
    parser.add_argument('--tx-interval',type=float,default=0.005,help='seconds between UART writes')
    options = parser.parse_args(argv)
    if not math.isfinite(options.timeout) or options.timeout <= 0 or options.retries < 0 or options.tx_burst < 1 or not math.isfinite(options.tx_interval) or options.tx_interval < 0:
        parser.error('timeout must be positive; retries must be nonnegative')
    if options.command == 'exec':
        if not options.path.startswith('/'):
            parser.error('exec requires an absolute path')
        try:
            request_line('validate', 'exec', (options.path, *options.args))
        except ValueError as error:
            parser.error(str(error))
    for protected in (options.raw_log, options.uart):
        if options.log.resolve() == Path(protected).resolve() or (options.log.exists() and Path(protected).exists() and os.path.samefile(options.log, protected)):
            parser.error('host metadata log must differ from raw log and UART')
    options.log.parent.mkdir(parents=True, exist_ok=True)
    raw = None
    fd = None
    try:
        # Baseline is established before opening/writing the UART.
        raw = RawLog(options.raw_log, options.baseline)
        with options.log.open('a', encoding='utf-8') as log:
            fd = open_writer(options.uart)
            client = Client(lambda data: write_paced(fd, data, burst=options.tx_burst, interval=options.tx_interval), raw.read,
                            timeout=options.timeout, retries=options.retries, log=log,
                            write_deadline=lambda data, deadline: write_paced(fd, data,
                                burst=options.tx_burst, interval=options.tx_interval, deadline=deadline))
            if options.command == 'upload':
                result = client.upload(options.image, reboot=options.reboot, compression='gzip' if options.gzip else 'raw')
            elif options.command == 'exec':
                result = client.request('exec', options.path, *options.args)
            else:
                result = client.request(options.command)
            print(result or 'OK')
        return 0
    except (OSError, ValueError, ProtocolError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if fd is not None:
            os.close(fd)
        if raw is not None:
            raw.close()


if __name__ == '__main__':
    sys.exit(main())
