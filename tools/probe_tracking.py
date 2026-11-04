"""Read-only local NatNet diagnostics; never enables a robot or ultrasound."""
import json
import socket
import struct
from pathlib import Path


def probe():
    out = Path(__file__).resolve().parents[1] / 'diagnostics'
    out.mkdir(exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as command:
        command.bind(('127.0.0.1', 0))
        command.settimeout(3)
        for message, name in [(0, 'server'), (4, 'models')]:
            command.sendto(struct.pack('<HH', message, 0), ('127.0.0.1', 1510))
            data, _ = command.recvfrom(65535)
            (out / (name + '.bin')).write_bytes(data)
            print(name, len(data), data[:350].hex())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        stream.bind(('', 1511))
        stream.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                          socket.inet_aton('239.255.42.99') + socket.inet_aton('127.0.0.1'))
        stream.settimeout(3)
        data, _ = stream.recvfrom(65535)
        (out / 'frame.bin').write_bytes(data)
        print('frame', len(data), data.hex())


if __name__ == '__main__':
    probe()
