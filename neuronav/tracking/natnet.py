"""Bounded NatNet reader for navigation visualization (SI units, xyzw quaternions).

Supports frame prefixes and model descriptions from NatNet 3.1, 4.0, 4.1,
and 4.2. Sections outside this viewer's scope are skipped using their
4.1+ byte lengths. No commands change tracking, robot, or ultrasound state.
"""
from collections import deque
import math
import select
import socket
import struct
import threading
import time

from neuronav.tracking.datatypes import Body, Frame, Models


class ProtocolError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ProtocolError('Truncated NatNet packet')
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def unpack(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))

    def count(self):
        count, = self.unpack('i')
        if not 0 <= count <= 100000:
            raise ProtocolError('Invalid NatNet element count')
        return count

    def string(self):
        end = self.data.find(b'\0', self.offset)
        if end < 0:
            raise ProtocolError('Unterminated NatNet name')
        return self.take(end - self.offset + 1)[:-1].decode('utf-8', errors='replace')


def payload(packet, expected):
    r = Reader(packet)
    kind, length = r.unpack('HH')
    if kind != expected:
        raise ProtocolError(f'Expected message {expected}, received {kind}')
    return Reader(r.take(length))


def version_supported(version):
    if tuple(version[:2]) not in ((3, 1), (4, 0), (4, 1), (4, 2)):
        raise ProtocolError(f'Unsupported NatNet version {version}; supported: 3.1 and 4.0–4.2')


def section(r, version):
    count = r.count()
    if version >= (4, 1):
        length, = r.unpack('i')
        return count, Reader(r.take(length))
    return count, r


def body(r):
    ident, = r.unpack('i')
    pos = r.unpack('fff')
    quat = r.unpack('ffff')
    error, flags = r.unpack('fH')
    finite = all(math.isfinite(x) for x in (*pos, *quat, error))
    norm = sum(x*x for x in quat)
    return Body(ident, pos, quat, error, bool(flags & 1) and finite and 0.9 < norm < 1.1)


def decode_frame(packet, version):
    version = tuple(version[:2])
    version_supported(version)
    r = payload(packet, 7)
    number, = r.unpack('i')
    count, s = section(r, version)
    # Marker sets may contain model-local/default coordinates for untracked
    # assets. Never display them as observed world-space points.
    for _ in range(count):
        s.string()
        s.take(s.count() * 12)
    count, s = section(r, version)
    unlabeled = [s.unpack('fff') for _ in range(count)]
    count, s = section(r, version)
    bodies = tuple(body(s) for _ in range(count))
    count, s = section(r, version)
    for _ in range(count):
        s.unpack('i')
        for _ in range(s.count()):
            body(s)
    if version >= (4, 1):
        # Assets section (byte-counted). Not used for navigation here.
        section(r, version)
    count, s = section(r, version)
    markers = []
    for _ in range(count):
        s.unpack('i')
        pos = s.unpack('fff')
        s.unpack('f')  # marker diameter
        flags, = s.unpack('H')
        s.unpack('f')  # residual
        # Exclude occluded/model-filled points. Only observed/reconstructed
        # markers enter the scene. Unlabeled markers also appear in this list.
        if flags & 2 and not flags & 1 and all(math.isfinite(x) for x in pos):
            markers.append(pos)
    # Deduplicate observations which also occur in the unlabeled list.
    unique = {tuple(round(v, 6) for v in p): p for p in markers + unlabeled
              if all(math.isfinite(v) for v in p)}
    return Frame(number, time.monotonic(), tuple(unique.values()), bodies, len(unlabeled))


def decode_models(packet, version):
    version = tuple(version[:2])
    version_supported(version)
    r = payload(packet, 5)
    result = Models()
    for _ in range(r.count()):
        kind, = r.unpack('i')
        if version >= (4, 1):
            length, = r.unpack('i')
            s = Reader(r.take(length))
        else:
            s = r
        if kind == 0:
            s.string()
            for _ in range(s.count()):
                s.string()
        elif kind == 1:
            name = s.string()
            ident, = s.unpack('i')
            result.names[ident] = name
            if version < (4, 1):
                s.take(16)  # parent ID and position
                if version >= (4, 0):
                    s.take(16)  # default orientation
                count = s.count()
                s.take(count * 16)  # positions and required labels
                for _ in range(count):
                    s.string()
        elif kind == 5:
            name = s.string()
            pos, quat = s.unpack('fff'), s.unpack('ffff')
            if all(math.isfinite(v) for v in (*pos, *quat)):
                result.cameras.append((name, pos, quat))
        elif version < (4, 1):
            # Older descriptions have no lengths; never guess the next offset.
            break
    return result


class NatNet:
    def __init__(self, server='127.0.0.1', interface='127.0.0.1',
                 group='239.255.42.99', data_port=1511, command_port=1510):
        self.server, self.interface, self.group = server, interface, group
        self.data_port, self.command_port = data_port, command_port
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.frame = None
        self.history = deque(maxlen=600)
        self.models = Models()
        self.version = None
        self.server_name = ''
        self.error = ''
        self.frames = 0
        self.parse_errors = 0
        self.started = time.monotonic()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='NatNet receiver', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def snapshot(self):
        with self.lock:
            return dict(frame=self.frame, models=self.models, version=self.version,
                        server=self.server_name, error=self.error, frames=self.frames,
                        parse_errors=self.parse_errors)

    def frames_since(self, received):
        with self.lock:
            return [frame for frame in self.history if frame.received > received]

    def run(self):
        sockets = []
        try:
            socket.inet_aton(self.server)
            socket.inet_aton(self.interface)
            command = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(command)
            command.bind((self.interface, 0))
            command.setblocking(False)
            data = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(data)
            data.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            data.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)
            data.bind(('', self.data_port))
            data.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(self.group) + socket.inet_aton(self.interface))
            data.setblocking(False)
            next_ping, next_models = 0, 0
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_ping:
                    command.sendto(struct.pack('<HH', 0, 0), (self.server, self.command_port))
                    next_ping = now + 3
                if self.version and now >= next_models:
                    command.sendto(struct.pack('<HH', 4, 0), (self.server, self.command_port))
                    next_models = now + 5
                readable, _, _ = select.select(sockets, [], [], .1)
                for source in readable:
                    packet, address = source.recvfrom(65535)
                    if address[0] != self.server or len(packet) < 4:
                        continue
                    try:
                        kind, = struct.unpack_from('<H', packet)
                        with self.lock:
                            if kind == 1:
                                s = payload(packet, 1)
                                name = s.take(256).split(b'\0')[0].decode(errors='replace')
                                appver, ver = tuple(s.take(4)), tuple(s.take(4))
                                version_supported(ver)
                                self.version = ver
                                self.server_name = f'{name} {".".join(map(str, appver))}'
                            elif kind == 5 and self.version:
                                self.models = decode_models(packet, self.version)
                            elif kind == 7 and self.version:
                                self.frame = decode_frame(packet, self.version)
                                self.history.append(self.frame)
                                self.frames += 1
                                self.error = ''
                    except (ProtocolError, struct.error) as exc:
                        with self.lock:
                            self.parse_errors += 1
                            self.error = str(exc)
        except (OSError, ValueError) as exc:
            with self.lock:
                self.error = f'{type(exc).__name__}: {exc}'
        finally:
            for sock in sockets:
                sock.close()
