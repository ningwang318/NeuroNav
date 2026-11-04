"""Protocol regression tests; synthetic packets do not contact hardware."""
import math
import struct
import unittest

from neuronav.tracking.natnet import ProtocolError, decode_frame, decode_models


def pack(fmt, *values):
    return struct.pack('<' + fmt, *values)


def packet(kind, data):
    return pack('HH', kind, len(data)) + data


def section(count, data):
    return pack('ii', count, len(data)) + data


def frame_packet(valid=True, quaternion=(0, 0, 0, 1), observed=True):
    # Deliberately place a stored model marker far away. It must never be
    # mistaken for an observation or used to fit the tracking scene.
    sets = b'Head\0' + pack('i3f', 1, 999, 999, 999)
    rb = pack('i3f4ffH', 7, .1, .2, .3, *quaternion, .0002, int(valid))
    marker = pack('i3ffHf', 1, .4, .5, .6, .01, 2 if observed else 4, .001)
    data = pack('i', 123) + section(1, sets) + section(0, b'')
    data += section(1, rb) + section(0, b'') + section(0, b'') + section(1, marker)
    return packet(7, data)


class NatNetTests(unittest.TestCase):
    def test_world_observations_exclude_stored_model_marker(self):
        frame = decode_frame(frame_packet(), (4, 2))
        self.assertEqual(frame.number, 123)
        self.assertEqual(len(frame.markers), 1)
        self.assertAlmostEqual(frame.markers[0][0], .4)
        self.assertTrue(frame.bodies[0].valid)
        self.assertAlmostEqual(frame.bodies[0].mean_error_m, .0002)

    def test_invalid_body_and_model_filled_marker(self):
        frame = decode_frame(frame_packet(valid=False, observed=False), (4, 2))
        self.assertFalse(frame.bodies[0].valid)
        self.assertEqual(frame.markers, ())

    def test_invalid_orientation_is_rejected(self):
        for quat in [(0, 0, 0, 0), (math.nan, 0, 0, 1), (0, 0, 0, 5)]:
            self.assertFalse(decode_frame(frame_packet(quaternion=quat), (4, 2)).bodies[0].valid)

    def test_truncation_is_rejected(self):
        data = frame_packet()
        for i in range(len(data)):
            with self.assertRaises(ProtocolError):
                decode_frame(data[:i], (4, 2))

    def test_unknown_protocol_is_rejected(self):
        with self.assertRaises(ProtocolError):
            decode_frame(frame_packet(), (5, 0))

    def test_sized_unknown_model_does_not_hide_camera(self):
        rb = b'Head\0' + pack('i', 7)
        camera = b'Trio A\0' + pack('3f4f', 1, 2, 3, 0, 0, 0, 1)
        content = pack('i', 3)
        for kind, data in [(1, rb), (99, b'arbitrary'), (5, camera)]:
            content += pack('ii', kind, len(data)) + data
        models = decode_models(packet(5, content), (4, 2))
        self.assertEqual(models.names, {7: 'Head'})
        self.assertEqual(models.cameras[0][1], (1., 2., 3.))

    def test_31_frame_prefix(self):
        rb = pack('i3f4ffH', 7, .1, .2, .3, 0, 0, 0, 1, .0002, 1)
        data = pack('iii', 10, 0, 0) + pack('i', 1) + rb + pack('ii', 0, 0)
        frame = decode_frame(packet(7, data), (3, 1))
        self.assertTrue(frame.bodies[0].valid)
        self.assertEqual(frame.markers, ())


if __name__ == '__main__':
    unittest.main()
