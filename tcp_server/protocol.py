import struct
import socket



def send_frame(sock, data: bytes):
    header = struct.pack('>I', len(data))
    sock.sendall(header + data)

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
