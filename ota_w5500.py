"""Direct W5500 OTA: H523-zcu36/Common/Inc/ota_protocol.h."""
import binascii
import json
import socket
import struct
import time

from ota_send import SocketPort, send_ota, TIMEOUT_SEC

DISCOVERY_PORT = 9300
TRANSFER_PORT = 9301
DISCOVERY_SECONDS = 3.0
READY_SECONDS = 5.0
RESULT_SECONDS = 10.0
CHUNK_SIZE = 512
MAX_IMAGE_SIZE = 64 * 1024
CAMERA_MAX_IMAGE_SIZE = 192 * 1024
CAMERA_TRANSFER_PORT = 9303


class CameraDatagramPort(SocketPort):
    def write(self, data):
        count = self.sock.send(data)
        if self.tx_callback:
            self.tx_callback(data)
        return count


def send_camera_ota(local_ip, device_ip, bin_path, progress=lambda value: None,
                    log=print, tx=lambda data: None, rx=lambda data: None):
    size = bin_path.stat().st_size
    if not 0 < size <= CAMERA_MAX_IMAGE_SIZE:
        raise ValueError(f"Thermal camera APP BIN size must be 1..{CAMERA_MAX_IMAGE_SIZE} bytes; got {size}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((local_ip, 0))
        sock.connect((device_ip, CAMERA_TRANSFER_PORT))
        sock.settimeout(TIMEOUT_SEC)
        send_ota(CameraDatagramPort(sock, tx, rx), bin_path, progress, log)


def scan_devices(local_ip, log=print, tx=lambda data: None, rx=lambda data: None,
                 target="255.255.255.255"):
    devices = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((local_ip, 0))
        log(f"SCAN       : {sock.getsockname()} -> {target}:{DISCOVERY_PORT} DISC")
        sock.sendto(b"DISC", (target, DISCOVERY_PORT))
        tx(b"DISC")
        deadline = time.monotonic() + DISCOVERY_SECONDS
        while time.monotonic() < deadline:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                data, peer = sock.recvfrom(2048)
            except socket.timeout:
                break
            rx(data)
            log(f"SCAN RX    : {peer[0]}:{peer[1]} {data!r}")
            try:
                info = json.loads(data.decode("ascii"))
                ip = info["ip"]
                socket.inet_pton(socket.AF_INET, ip)
                version = str(info["fw"])
            except (ValueError, KeyError, TypeError, UnicodeError, OSError) as exc:
                log(f"SCAN ERROR : invalid discovery response: {exc}")
                continue
            devices[ip] = version
    log(f"SCAN DONE  : {len(devices)} devices")
    return devices


def send_w5500_ota(local_ip, device_ip, bin_path, progress=lambda value: None,
                    log=print, tx=lambda data: None, rx=lambda data: None):
    image = bin_path.read_bytes()
    if not 0 < len(image) <= MAX_IMAGE_SIZE:
        raise ValueError(f"H523 APP BIN size must be 1..{MAX_IMAGE_SIZE} bytes; got {len(image)}")
    crc = binascii.crc32(image) & 0xFFFFFFFF
    started = time.monotonic()
    stage = "TCP connect"
    sent = 0

    def report(message):
        log(f"[{device_ip} +{time.monotonic() - started:.3f}s] {message}")

    def token(sock, timeout):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while len(data) < 4:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"response timeout; partial={bytes(data)!r}")
            sock.settimeout(remaining)
            part = sock.recv(4 - len(data))
            if not part:
                raise ConnectionError(f"peer closed; partial={bytes(data)!r}")
            rx(part)
            data.extend(part)
        report(f"RX token: {bytes(data)!r}")
        return bytes(data)

    report(f"BIN={bin_path} SIZE={len(image)} CRC32=0x{crc:08X}")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((local_ip, 0))
            sock.settimeout(READY_SECONDS)
            report(f"CONNECT {local_ip} -> {device_ip}:{TRANSFER_PORT}")
            sock.connect((device_ip, TRANSFER_PORT))
            report(f"CONNECTED local={sock.getsockname()}")
            stage = "FWU! / READ"
            sock.sendall(b"FWU!")
            tx(b"FWU!")
            report("TX token: b'FWU!'; waiting READ (5s)")
            response = token(sock, READY_SECONDS)
            if response != b"READ":
                raise RuntimeError(f"device returned {response!r}; expected b'READ'")
            stage = "header"
            header = struct.pack("<II", len(image), crc)
            sock.settimeout(RESULT_SECONDS)
            sock.sendall(header)
            tx(header)
            report(f"TX header: {header.hex(' ')} (size/CRC32 little-endian)")
            stage = "BIN transfer"
            for offset in range(0, len(image), CHUNK_SIZE):
                chunk = image[offset:offset + CHUNK_SIZE]
                sock.sendall(chunk)
                sent += len(chunk)
                tx(chunk)
                progress(sent * 100.0 / len(image))
                report(f"TX BIN: {sent}/{len(image)} bytes")
            stage = "SUCC / FAIL"
            report("All BIN bytes sent to TCP; waiting device result (10s)")
            response = token(sock, RESULT_SECONDS)
            if response != b"SUCC":
                raise RuntimeError(f"device returned {response!r}; expected b'SUCC'")
            report("SUCC: device accepted download/CRC/update flag; BOOT install follows reset")
    except Exception as exc:
        report(f"ERROR stage={stage}; TCP-sent={sent}/{len(image)}; {type(exc).__name__}: {exc}")
        raise
