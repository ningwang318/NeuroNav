"""Isolated Windows x64 Motive API host. No NatNet and no actuator commands.

Only scalar/POD SDK signatures are bound. The MSVC C++ ABI is resolved from
the installed DLL's exports; no STL objects cross the ctypes boundary.
All native API calls, including Initialize/Shutdown, run on the main thread.
"""
import argparse
import ctypes as C
import json
import math
import os
from pathlib import Path
import struct
import sys
import threading
import time


RESULTS = ['Success', 'Failed', 'FileNotFound', 'LoadFailed', 'SaveFailed',
           'InvalidFile', 'InvalidLicense', 'NoFrameAvailable', 'TooFewMarkers',
           'TooManyMarkers', 'UnableToFindGroundPlane', 'UnableToLoadCalibration',
           'RemoveCalibrationSquare', 'PartialSave', 'PartialLoad']


def exports(path):
    """Read PE export names without an additional dependency or compiler."""
    data = Path(path).read_bytes()
    pe, = struct.unpack_from('<I', data, 0x3c)
    if data[pe:pe+4] != b'PE\0\0':
        raise RuntimeError('Motive API DLL is not a PE image')
    machine, count = struct.unpack_from('<HH', data, pe+4)
    if machine != 0x8664 or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError('This bridge requires Windows x64 Python and Motive API')
    opt_size, = struct.unpack_from('<H', data, pe+20)
    opt = pe + 24
    magic, = struct.unpack_from('<H', data, opt)
    if magic != 0x20b:
        raise RuntimeError('Expected a PE32+ DLL')
    sections = []
    for i in range(count):
        start = opt + opt_size + i*40
        virtual_size, rva, raw_size, raw = struct.unpack_from('<IIII', data, start+8)
        sections.append((rva, max(virtual_size, raw_size), raw))

    def offset(rva):
        for base, size, raw in sections:
            if base <= rva < base + size:
                return raw + rva - base
        raise RuntimeError('Invalid PE RVA')

    export_rva, = struct.unpack_from('<I', data, opt+112)
    directory = offset(export_rva)
    names_count, = struct.unpack_from('<I', data, directory+24)
    names_rva, = struct.unpack_from('<I', data, directory+32)
    names = []
    for i in range(names_count):
        name_rva, = struct.unpack_from('<I', data, offset(names_rva)+i*4)
        name_offset = offset(name_rva)
        end = data.index(b'\0', name_offset)
        names.append(data[name_offset:end].decode('ascii'))
    return names


class API:
    def __init__(self, install):
        root = Path(install).resolve()
        dll = root / 'lib' / 'MotiveAPI.dll'
        self.directory_handles = [os.add_dll_directory(str(p)) for p in
                                  [root, root/'lib', root/'assemblies'] if p.is_dir()]
        # Motive uses Qt5 internally. This process must never import PySide6.
        platforms = root/'assemblies'/'platforms'
        if not platforms.is_dir():
            platforms = root/'plugins'/'platforms'
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(platforms)
        os.environ.pop('QT_PLUGIN_PATH', None)
        self.dll = C.CDLL(str(dll))
        names = exports(dll)
        fptr = C.POINTER(C.c_float)
        signatures = {
            'Initialize': (C.c_int, []), 'Shutdown': (None, []),
            'IsInitialized': (C.c_bool, []), 'CanConnectToDevices': (C.c_bool, []),
            'BuildNumber': (C.c_int, []), 'Update': (C.c_int, []),
            'StreamNP': (C.c_int, [C.c_bool]),
            'StreamVRPN': (C.c_int, [C.c_bool, C.c_int]),
            'LoadProfile': (C.c_int, [C.c_wchar_p]),
            'LoadCalibration': (C.c_int, [C.c_wchar_p, C.POINTER(C.c_int)]),
            'CameraCount': (C.c_int, []), 'CameraSerial': (C.c_int, [C.c_int]),
            'CameraPositionOrientation': (C.c_bool, [C.c_int, fptr, fptr]),
            'MarkerCount': (C.c_int, []), 'MarkerXYZ': (C.c_bool, [C.c_int, fptr, fptr, fptr]),
            'MarkerContributingRaysCount': (C.c_int, [C.c_int]),
            'RigidBodyCount': (C.c_int, []),
            'RigidBodyName': (C.c_bool, [C.c_int, C.POINTER(C.c_wchar), C.c_int]),
            'RigidBodyTransform': (C.c_bool, [C.c_int] + [fptr]*10),
            'IsRigidBodyTracked': (C.c_bool, [C.c_int]),
            'RigidBodyMeanError': (C.c_float, [C.c_int]),
        }
        self.symbols = {}
        for name, (returns, args) in signatures.items():
            candidates = [n for n in names if n.startswith(f'?{name}@MotiveAPI@@')]
            if len(candidates) != 1:
                raise RuntimeError(f'Expected one SDK export for {name}, found {len(candidates)}')
            function = getattr(self.dll, candidates[0])
            function.restype, function.argtypes = returns, args
            setattr(self, name, function)
            self.symbols[name] = candidates[0]


def send(message):
    # Prefix protects the protocol from native DLL diagnostic output on stdout.
    print('NEURONAV_JSON ' + json.dumps(message, ensure_ascii=True, allow_nan=False), flush=True)


def check(result, operation):
    if result != 0:
        name = RESULTS[result] if 0 <= result < len(RESULTS) else str(result)
        raise RuntimeError(f'{operation}: {name} ({result})')


def pump_windows():
    from ctypes import wintypes
    message = wintypes.MSG()
    user32 = C.windll.user32
    while user32.PeekMessageW(C.byref(message), None, 0, 0, 1):
        user32.TranslateMessage(C.byref(message))
        user32.DispatchMessageW(C.byref(message))


def models(api):
    names, cameras = {}, []
    for i in range(api.RigidBodyCount()):
        buffer = C.create_unicode_buffer(512)
        if api.RigidBodyName(i, buffer, len(buffer)):
            names[str(i)] = buffer.value
    for i in range(api.CameraCount()):
        pos, matrix = (C.c_float*3)(), (C.c_float*9)()
        if api.CameraPositionOrientation(i, pos, matrix):
            cameras.append(dict(name=f'Trio {i+1}', serial=api.CameraSerial(i),
                                position=list(pos), matrix=list(matrix)))
    return dict(type='models', names=names, cameras=cameras,
                id_convention='SDK rigid-body index (not NatNet streaming ID)')


def frame(api, number):
    markers, bodies = [], []
    for i in range(api.MarkerCount()):
        xyz = [C.c_float() for _ in range(3)]
        if api.MarkerXYZ(i, *(C.byref(v) for v in xyz)) and api.MarkerContributingRaysCount(i) >= 2:
            pos = [v.value for v in xyz]
            if all(math.isfinite(v) for v in pos):
                markers.append(pos)
    for i in range(api.RigidBodyCount()):
        values = [C.c_float() for _ in range(10)]
        transformed = api.RigidBodyTransform(i, *(C.byref(v) for v in values))
        valid = transformed and api.IsRigidBodyTracked(i)
        error = api.RigidBodyMeanError(i) if valid else 0
        raw = [v.value for v in values[:7]]
        valid = bool(valid and all(math.isfinite(v) for v in [*raw, error])
                     and .9 < sum(v*v for v in raw[3:]) < 1.1)
        bodies.append(dict(id=i, valid=valid,
                           position=raw[:3] if valid else [0, 0, 0],
                           quaternion=raw[3:] if valid else [0, 0, 0, 1],
                           mean_error_m=error if valid else 0))
    return dict(type='frame', number=number, markers=markers, bodies=bodies)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', default=r'C:\Program Files\OptiTrack\Motive')
    parser.add_argument('--calibration', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    api = None
    stop = threading.Event()

    def commands():
        for line in sys.stdin:
            if line.strip() == 'stop':
                stop.set()
                return
        if not args.seconds:
            stop.set()  # parent closed its pipe

    threading.Thread(target=commands, daemon=True).start()
    try:
        for path in [args.calibration, args.profile]:
            if not Path(path).is_file():
                raise RuntimeError(f'Required configuration missing: {path}')
        send(dict(type='status', message='Loading Motive API'))
        api = API(args.install)
        if not api.CanConnectToDevices():
            raise RuntimeError('Cameras are in use. Close Motive and other tracking hosts, then reconnect.')
        send(dict(type='status', message='Initializing licensed tracking engine'))
        check(api.Initialize(), 'Initialize')
        count = C.c_int()
        check(api.LoadCalibration(args.calibration, C.byref(count)), 'LoadCalibration')
        check(api.LoadProfile(args.profile), 'LoadProfile')
        # Profile may have network streaming enabled. This host exclusively
        # transports data through its parent pipe; do not start NatNet/VRPN.
        check(api.StreamNP(False), 'Disable NatNet streaming')
        check(api.StreamVRPN(False, 3883), 'Disable VRPN streaming')
        send(dict(type='status', message=f'Discovering cameras; expected {count.value}'))
        deadline = time.monotonic() + 30
        while api.CameraCount() < max(1, count.value):
            if stop.is_set():
                return 0
            if time.monotonic() >= deadline:
                raise RuntimeError(f'Camera discovery timed out: {api.CameraCount()} of {count.value}')
            api.Update()
            pump_windows()
            time.sleep(.01)
        send(dict(type='ready', build=api.BuildNumber(), cameras=api.CameraCount(),
                  calibration=args.calibration, profile=args.profile))
        send(models(api))
        started, number = time.monotonic(), 0
        next_models = started + 5
        while not stop.is_set():
            if args.seconds and time.monotonic() - started >= args.seconds:
                break
            result = api.Update()
            pump_windows()
            if result == 7:  # no new camera frame, not a tracking failure
                time.sleep(.001)
                continue
            check(result, 'Update')
            if time.monotonic() >= next_models:
                send(models(api))
                next_models = time.monotonic() + 5
            number += 1
            send(frame(api, number))
        return 0
    except Exception as exc:
        send(dict(type='error', message=f'{type(exc).__name__}: {exc}'))
        return 1
    finally:
        if api and api.IsInitialized():
            api.Shutdown()
        send(dict(type='stopped'))


if __name__ == '__main__':
    sys.exit(main())
