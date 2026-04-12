import struct
import socket



def build_frame_packet(data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + data


def send_packet(sock, packet: bytes):
    sock.sendall(packet)


def send_frame(sock, data: bytes):
    sock.sendall(build_frame_packet(data))

def recv_exact(sock, size: int) -> bytes:
    buffer = b""
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer

def recv_frame(sock):
    header = recv_exact(sock, 4)
    if not header:
        return None
    
    (data_size,) = struct.unpack('>I', header)
    payload = recv_exact(sock, data_size)
    return payload
