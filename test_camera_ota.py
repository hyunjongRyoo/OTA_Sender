import binascii
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ota_w5500 as ota
from ota_send import build_frame, extract_valid_frame, CHUNK_SIZE
from test_ota_w5500 import Peer


class DatagramPeer(Peer):
    def send(self, data):
        self.sent.append(data)
        return len(data)


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'app.bin'

    def test_camera_frames_and_size_boundaries(self):
        for size in (84402, 196608):
            with self.subTest(size=size):
                image = (bytes(range(256)) * ((size + 255) // 256))[:size]
                self.path.write_bytes(image)
                chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
                responses = [build_frame('FWU=', b'READ'), build_frame('FWH=', b'READ')]
                responses += [build_frame('FWD=', b'READ')] * (chunks - 1)
                responses += [build_frame('FWD=', b'SUCC')]
                peer = DatagramPeer(responses)
                with patch.object(ota.socket, 'socket', return_value=peer) as factory:
                    ota.send_camera_ota('192.168.0.231', '192.168.0.30', self.path, log=lambda _: None)
                factory.assert_called_once_with(ota.socket.AF_INET, ota.socket.SOCK_DGRAM)
                self.assertEqual(peer.remote, ('192.168.0.30', 9303))
                frames = [extract_valid_frame(bytearray(data)) for data in peer.sent]
                self.assertEqual(frames[0][:2], ('FWU!', b''))
                self.assertEqual(frames[1][:2], ('FWH!', struct.pack('<II', size, binascii.crc32(image))))
                self.assertTrue(all(frame[0] == 'FWD!' for frame in frames[2:]))
                self.assertEqual(b''.join(frame[1] for frame in frames[2:]), image)
                self.assertFalse(peer.responses)

    def test_camera_invalid_size_before_socket(self):
        for size in (0, 196609):
            self.path.write_bytes(b'x' * size)
            with patch.object(ota.socket, 'socket') as factory:
                with self.assertRaises(ValueError):
                    ota.send_camera_ota('127.0.0.1', '127.0.0.1', self.path)
                factory.assert_not_called()

    def test_camera_header_fail_stops_data(self):
        self.path.write_bytes(b'x' * 84402)
        peer = DatagramPeer([build_frame('FWU=', b'READ'), build_frame('FWH=', b'FAIL')])
        with patch.object(ota.socket, 'socket', return_value=peer):
            with self.assertRaisesRegex(RuntimeError, 'FAIL'):
                ota.send_camera_ota('127.0.0.1', '127.0.0.1', self.path, log=lambda _: None)
        self.assertEqual(len(peer.sent), 2)


if __name__ == '__main__':
    unittest.main()
