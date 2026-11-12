import binascii
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ota_w5500 as ota


class Peer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def bind(self, address): self.local = address
    def settimeout(self, timeout): pass
    def connect(self, address): self.remote = address
    def getsockname(self): return self.local
    def sendall(self, data): self.sent.append(data)
    def recv(self, size):
        value = self.responses.pop(0)
        if isinstance(value, Exception): raise value
        return value


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "app.bin"
        self.image = bytes(range(256)) * 5 + b"end"
        self.path.write_bytes(self.image)
        self.logs = []

    def transfer(self, peer):
        with patch.object(ota.socket, "socket", return_value=peer):
            ota.send_w5500_ota("192.168.0.231", "192.168.30.200", self.path,
                             log=self.logs.append)

    def test_wire_bytes_and_fragmented_tokens(self):
        peer = Peer([b"R", b"E", b"AD", b"SU", b"CC"])
        self.transfer(peer)
        self.assertEqual(peer.sent[0], b"FWU!")
        self.assertEqual(peer.sent[1], struct.pack("<II", len(self.image), binascii.crc32(self.image)))
        self.assertEqual(b"".join(peer.sent[2:]), self.image)
        self.assertEqual(peer.remote, ("192.168.30.200", 9301))
        self.assertTrue(any("SUCC:" in line for line in self.logs))

    def test_device_fail_in_ready_stage(self):
        peer = Peer([b"FAIL"])
        with self.assertRaisesRegex(RuntimeError, "FAIL"):
            self.transfer(peer)
        self.assertEqual(peer.sent, [b"FWU!"])
        self.assertTrue(any("ERROR stage=FWU! / READ" in line for line in self.logs))

    def test_device_fail_after_bin(self):
        with self.assertRaisesRegex(RuntimeError, "FAIL"):
            self.transfer(Peer([b"READ", b"FAIL"]))
        self.assertTrue(any("ERROR stage=SUCC / FAIL" in line for line in self.logs))

    def test_timeout_distinct_from_device_fail(self):
        with self.assertRaises(socket.timeout):
            self.transfer(Peer([socket.timeout("timed out")]))
        self.assertTrue(any("TimeoutError" in line for line in self.logs))
        self.assertFalse(any("device returned b'FAIL'" in line for line in self.logs))

    def test_close_mid_token(self):
        with self.assertRaisesRegex(ConnectionError, "partial=b'RE'"):
            self.transfer(Peer([b"RE", b""]))

    def test_invalid_size_before_connect(self):
        for size in (0, 65537):
            self.path.write_bytes(b"x" * size)
            with patch.object(ota.socket, "socket") as factory:
                with self.assertRaises(ValueError):
                    ota.send_w5500_ota("127.0.0.1", "127.0.0.1", self.path)
                factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
