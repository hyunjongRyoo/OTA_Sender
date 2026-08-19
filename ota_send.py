import argparse
import binascii
import socket
import struct
import sys
import time
from pathlib import Path


STX = 0x5B
ETX = 0x5D
COMMA = 0x2C
CHANNEL = 0x00
CHUNK_SIZE = 244
TIMEOUT_SEC = 10.0
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9001


class SocketPort:
    """Expose the small serial-like interface used by the OTA protocol."""

    def __init__(self, sock, tx_callback=None, rx_callback=None):
        self.sock = sock
        self.rx_buffer = bytearray()
        self.tx_callback = tx_callback
        self.rx_callback = rx_callback

    def read(self, size):
        return self.sock.recv(size)

    def write(self, data):
        self.sock.sendall(data)
        if self.tx_callback:
            self.tx_callback(data)
        return len(data)

    def flush(self):
        pass


def get_local_ipv4_addresses():
    addresses = []

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address not in addresses and not address.startswith("127."):
                addresses.insert(0, address)
        finally:
            probe.close()
    except OSError:
        pass

    if not addresses:
        addresses.append("127.0.0.1")

    return addresses


def xor_checksum(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


def build_frame(cmd, data=b""):
    if len(cmd) != 4:
        raise ValueError("cmd must be 4 ASCII bytes")

    length = 12 + len(data)
    frame = bytearray()
    frame.append(STX)
    frame.extend(struct.pack("<H", length))
    frame.append(CHANNEL)
    frame.extend(cmd.encode("ascii"))
    frame.extend(data)

    checksum = xor_checksum(frame)
    frame.append(COMMA)
    frame.extend(f"{checksum:02X}".encode("ascii"))
    frame.append(ETX)
    return bytes(frame)


def read_exact(port, size):
    data = bytearray()
    while len(data) < size:
        part = port.read(size - len(data))
        if not part:
            raise ConnectionError("connection closed while receiving data")
        data.extend(part)
    return bytes(data)


def read_frame(port):
    if hasattr(port, "rx_buffer"):
        return read_buffered_frame(port)

    deadline = time.monotonic() + TIMEOUT_SEC

    while time.monotonic() < deadline:
        byte = port.read(1)
        if not byte:
            raise ConnectionError("connection closed while waiting for STX")
        if byte[0] == STX:
            break
    else:
        raise TimeoutError("timeout waiting for STX")

    length_bytes = read_exact(port, 2)
    length = struct.unpack("<H", length_bytes)[0]
    if length < 12:
        raise ValueError(f"invalid response length: {length}")

    rest = read_exact(port, length - 3)
    frame = bytes([STX]) + length_bytes + rest

    if frame[-1] != ETX:
        raise ValueError("invalid ETX in response")
    if frame[-4] != COMMA:
        raise ValueError("invalid comma position in response")

    got_checksum = int(frame[-3:-1].decode("ascii"), 16)
    calc_checksum = xor_checksum(frame[:-4])
    if got_checksum != calc_checksum:
        raise ValueError(
            f"checksum mismatch: got 0x{got_checksum:02X}, calc 0x{calc_checksum:02X}"
        )

    cmd = frame[4:8].decode("ascii")
    payload = frame[8:-4]
    return cmd, payload, frame


def extract_valid_frame(buffer):
    search_from = 0

    while True:
        start = buffer.find(STX, search_from)
        if start < 0:
            return None
        if len(buffer) - start < 3:
            return None

        length = struct.unpack("<H", buffer[start + 1 : start + 3])[0]
        if length < 12:
            search_from = start + 1
            continue
        if len(buffer) - start < length:
            search_from = start + 1
            continue

        frame = bytes(buffer[start : start + length])
        if frame[-1] != ETX or frame[-4] != COMMA:
            search_from = start + 1
            continue

        try:
            got_checksum = int(frame[-3:-1].decode("ascii"), 16)
            cmd = frame[4:8].decode("ascii")
        except (UnicodeDecodeError, ValueError):
            search_from = start + 1
            continue

        if got_checksum != xor_checksum(frame[:-4]):
            search_from = start + 1
            continue

        del buffer[: start + length]
        return cmd, frame[8:-4], frame


def read_buffered_frame(port):
    deadline = time.monotonic() + TIMEOUT_SEC

    while time.monotonic() < deadline:
        parsed = extract_valid_frame(port.rx_buffer)
        if parsed is not None:
            return parsed

        data = port.sock.recv(4096)
        if not data:
            raise ConnectionError("connection closed while waiting for frame")
        if port.rx_callback:
            port.rx_callback(data)
        port.rx_buffer.extend(data)

    raise TimeoutError("timeout waiting for valid frame")


def send_and_wait(port, cmd, payload, expected_cmd, expected_payloads):
    frame = build_frame(cmd, payload)
    port.write(frame)
    port.flush()

    deadline = time.monotonic() + TIMEOUT_SEC
    while True:
        res_cmd, res_payload, _ = read_frame(port)
        if res_cmd == expected_cmd:
            break
        if res_cmd == "DAT=" and time.monotonic() < deadline:
            continue
        raise RuntimeError(f"unexpected response cmd: {res_cmd}, expected {expected_cmd}")

    if res_payload not in expected_payloads:
        text = res_payload.decode("ascii", errors="replace")
        expected = ", ".join(p.decode("ascii") for p in expected_payloads)
        raise RuntimeError(f"unexpected response payload: {text}, expected {expected}")
    return res_payload


def send_ota(port, bin_path, progress_callback=None, log_callback=print):
    image = bin_path.read_bytes()
    size = len(image)
    crc32 = binascii.crc32(image) & 0xFFFFFFFF

    log_callback(f"BIN        : {bin_path}")
    log_callback(f"SIZE       : {size} bytes")
    log_callback(f"CRC32      : 0x{crc32:08X}")
    log_callback("")

    log_callback("[1/3] FWU! start")
    send_and_wait(port, "FWU!", b"", "FWU=", [b"READ"])
    log_callback("      -> FWU=READ")

    log_callback("[2/3] FWH! header")
    header = struct.pack("<II", size, crc32)
    send_and_wait(port, "FWH!", header, "FWH=", [b"READ"])
    log_callback("      -> FWH=READ")

    log_callback("[3/3] FWD! data")
    offset = 0
    chunk_index = 0
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE

    while offset < size:
        chunk_index += 1
        chunk = image[offset : offset + CHUNK_SIZE]
        offset += len(chunk)

        is_last = offset >= size
        expected = [b"SUCC"] if is_last else [b"READ"]
        response = send_and_wait(port, "FWD!", chunk, "FWD=", expected)
        percent = (offset * 100.0) / size

        if progress_callback:
            progress_callback(percent)
        log_callback(
            f"      chunk {chunk_index}/{total_chunks}: "
            f"{offset}/{size} bytes ({percent:5.1f}%) -> "
            f"{response.decode('ascii')}"
        )

    log_callback("")
    log_callback("DONE       : FWD=SUCC")
    log_callback("NEXT       : board will reset after about 3 seconds")


def main():
    parser = argparse.ArgumentParser(description="Serve APP firmware BIN by TCP OTA bridge.")
    parser.add_argument("bin", type=Path, help="APP firmware .bin path")
    parser.add_argument("--host", default=DEFAULT_HOST, help="local bind IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="local TCP port")
    args = parser.parse_args()

    if not args.bin.is_file():
        print(f"BIN file not found: {args.bin}", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        print("PORT must be between 1 and 65535", file=sys.stderr)
        return 1

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((args.host, args.port))
            server.listen(1)
            print(f"LISTEN     : {args.host}:{args.port}")
            print("WAIT       : waiting for SoC connection")

            conn, peer = server.accept()
            with conn:
                conn.settimeout(TIMEOUT_SEC)
                print(f"CONNECTED  : {peer[0]}:{peer[1]}")
                send_ota(SocketPort(conn), args.bin)
    except Exception as exc:
        print("")
        print(f"ERROR      : {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
