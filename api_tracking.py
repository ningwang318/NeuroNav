"""Viewer-side adapter for the isolated Motive API process, over stdio pipes."""
import json
from collections import deque
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np

from tracking import Body, Frame, Models


ROOT = Path(__file__).resolve().parent


def quaternion_from_matrix(values):
    matrix = np.asarray(values, dtype=float).reshape(3, 3)
    if not np.isfinite(matrix).all() or not np.allclose(matrix.T @ matrix, np.eye(3), atol=.01):
        raise ValueError('Invalid camera orientation matrix')
    if not np.isclose(np.linalg.det(matrix), 1, atol=.01):
        raise ValueError('Camera orientation is not a proper rotation')
    # Symmetric eigenproblem avoids singularities near rotations of 180 degrees.
    m = matrix
    k = np.array([
        [m[0,0]-m[1,1]-m[2,2], m[0,1]+m[1,0], m[0,2]+m[2,0], m[2,1]-m[1,2]],
        [m[0,1]+m[1,0], m[1,1]-m[0,0]-m[2,2], m[1,2]+m[2,1], m[0,2]-m[2,0]],
        [m[0,2]+m[2,0], m[1,2]+m[2,1], m[2,2]-m[0,0]-m[1,1], m[1,0]-m[0,1]],
        [m[2,1]-m[1,2], m[0,2]-m[2,0], m[1,0]-m[0,1], np.trace(m)],
    ]) / 3
    _, vectors = np.linalg.eigh(k)
    q = vectors[:, -1]
    if q[3] < 0:
        q = -q
    return tuple(q.tolist())


class MotiveTracking:
    def __init__(self, install, calibration, profile):
        self.install, self.calibration, self.profile = install, calibration, profile
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.frame = None
        self.history = deque(maxlen=600)
        self.models = Models()
        self.frames = 0
        self.parse_errors = 0
        self.error = ''
        self.status = 'Starting standalone tracking service'
        self.server_name = 'Motive API · local service'

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='Motive API pipe reader')
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(frame=self.frame, models=self.models, version=None,
                        server=self.server_name, error=self.error, frames=self.frames,
                        parse_errors=self.parse_errors, status=self.status, source='api')

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process and process.poll() is None:
            try:
                process.stdin.write('stop\n')
                process.stdin.flush()
                process.wait(timeout=15)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                # Only the child created by this adapter is ever terminated.
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        if self.thread:
            self.thread.join(timeout=2)

    def frames_since(self, received):
        with self.lock:
            return [frame for frame in self.history if frame.received > received]

    def accept(self, message):
        kind = message['type']
        with self.lock:
            if kind == 'status':
                self.status = message['message']
            elif kind == 'ready':
                self.status = f'Engine ready · {message["cameras"]} cameras'
                self.server_name = f'Motive API build {message["build"]} · local service'
            elif kind == 'error':
                self.error = message['message']
            elif kind == 'models':
                cameras = []
                for camera in message['cameras']:
                    cameras.append((camera['name'], tuple(camera['position']),
                                    quaternion_from_matrix(camera['matrix'])))
                self.models = Models({int(k): v for k, v in message['names'].items()}, cameras)
            elif kind == 'frame':
                bodies = []
                for body in message['bodies']:
                    pos, quat = tuple(body['position']), tuple(body['quaternion'])
                    error = float(body['mean_error_m'])
                    valid = (body['valid'] and len(pos) == 3 and len(quat) == 4
                             and all(math.isfinite(v) for v in (*pos, *quat, error))
                             and .9 < sum(v*v for v in quat) < 1.1)
                    bodies.append(Body(int(body['id']), pos, quat, error, bool(valid)))
                markers = tuple(tuple(p) for p in message['markers']
                                if len(p) == 3 and all(math.isfinite(v) for v in p))
                self.frame = Frame(int(message['number']), time.monotonic(), markers, tuple(bodies), 0)
                self.history.append(self.frame)
                self.frames += 1

    def run(self):
        try:
            # Use base Python for this stdlib-only host. Windows venv launchers
            # create another process, which would defeat ownership on timeout.
            executable = Path(getattr(sys, '_base_executable', sys.executable))
            if executable.name.lower() == 'pythonw.exe':
                executable = executable.with_name('python.exe')
            command = [str(executable), '-u', str(ROOT/'motive_service.py'),
                       '--install', self.install, '--calibration', self.calibration,
                       '--profile', self.profile]
            environment = os.environ.copy()
            environment.pop('QT_PLUGIN_PATH', None)
            environment.pop('QT_QPA_PLATFORM_PLUGIN_PATH', None)
            with (ROOT/'motive-service.log').open('a', encoding='utf-8') as log:
                log.write(f'\nStarting API service {time.ctime()}\n')
                log.flush()
                if self.stop_event.is_set():
                    return
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8',
                    errors='replace', bufsize=1, env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if self.stop_event.is_set():
                    self.process.stdin.write('stop\n')
                    self.process.stdin.flush()
                for line in self.process.stdout:
                    if not line.startswith('NEURONAV_JSON '):
                        log.write(line)
                        continue
                    try:
                        self.accept(json.loads(line[len('NEURONAV_JSON '):]))
                    except (ValueError, KeyError, TypeError) as exc:
                        with self.lock:
                            self.parse_errors += 1
                            self.error = f'Invalid API message: {exc}'
                code = self.process.wait()
                with self.lock:
                    if not self.stop_event.is_set() and not self.error:
                        self.error = f'Tracking service stopped (exit {code}). See motive-service.log.'
        except Exception as exc:
            with self.lock:
                self.error = f'{type(exc).__name__}: {exc}'
        finally:
            if self.process:
                for stream in [self.process.stdin, self.process.stdout]:
                    if stream:
                        stream.close()
