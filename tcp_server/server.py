
import socket
import ssl
import threading
import time
import struct
import base64
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tcp_server.protocol import send_frame, recv_frame
from capture.screen import ScreenCapture
from db.auth import DatabaseAdapter

class TCPStreamingServer:
    def __init__(self, host='0.0.0.0', port=9999, use_tls=True):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.running = False
        
        self.clients = []
        self.clients_lock = threading.Lock()
        self.db = DatabaseAdapter()
        self.screencap = ScreenCapture(fps=30, quality=50)
        self.bytes_sent = 0
        self.frames_sent = 0
        self.start_time = time.time()

    def start(self):
        self.screencap.start()
        
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        print(f"[*] TCP Server Listening on {self.host}:{self.port}")
        if self.use_tls:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cert_path = os.path.join(base_dir, 'cert.pem')
            key_path = os.path.join(base_dir, 'key.pem')
            context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            self.server_socket = context.wrap_socket(self.server_socket, server_side=True)
            print("[*] TLS Encryption Enabled")
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        try:
            while self.running:
                client_sock, addr = self.server_socket.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                threading.Thread(target=self._handle_client, args=(client_sock, addr), daemon=True).start()
        except KeyboardInterrupt:
            self.stop()
            
    def stop(self):
        self.running = False
        self.screencap.stop()
        if hasattr(self, 'server_socket'):
            self.server_socket.close()
            
    def _handle_client(self, client_sock, addr):
        print(f"[+] Client {addr} connected.")
        
        # Authentication Phase
        username = None
        try:
            auth_msg = client_sock.recv(1024).decode('utf-8').strip()
            if not auth_msg.startswith("AUTH "):
                raise ValueError("Invalid auth format")
                
            _, parsed_username, password = auth_msg.split(' ')
            username = parsed_username
            
            # Use real database authentication
            if not self.db.authenticate_user(username, password):
                client_sock.sendall(b"AUTH_FAILED")
                client_sock.close()
                print(f"[-] Client {addr} failed authentication.")
                self.db.log_event("DISCONNECT_UNAUTHORIZED", username, addr[0], "Failed login attempt")
                return
            
            client_sock.sendall(b"AUTH_SUCCESS")
            
            # Add to broadcast pool
            with self.clients_lock:
                # Only set send timeout for congestion protection; do not set receive timeout for bridge client
                client_sock.settimeout(None)  # No timeout for recv
                self.clients.append(client_sock)
                
            print(f"[+] Client {addr} authenticated successfully.")
            self.db.log_event("CONNECT_SUCCESS", username, addr[0], "Client started streaming session")

            # Keep connection alive till client disconnects
            while True:
                try:
                    data = client_sock.recv(1024)
                    if not data:
                        break
                except socket.timeout:
                    # Timeout on recv is expected because we set a 0.5s timeout for send congestion.
                    continue
        except Exception as e:
            print(f"[-] Client {addr} exception: {e}")
            self.db.log_event("ERROR", username, addr[0], str(e))
        finally:
            with self.clients_lock:
                if client_sock in self.clients:
                    self.clients.remove(client_sock)
            client_sock.close()
            print(f"[-] Client {addr} disconnected.")
            self.db.log_event("DISCONNECT", username, addr[0], "Client ended session")

    def _broadcast_loop(self):
        last_log_time = time.time()
        
        while self.running:
            frame = self.screencap.get_latest_frame()
            if not frame:
                time.sleep(0.01)
                continue
                
            frame_size = len(frame)
            
            with self.clients_lock:
                stale_clients = []
                for sock in self.clients:
                    try:
                        send_frame(sock, frame)
                        self.bytes_sent += frame_size + 4
                        self.frames_sent += 1
                    except socket.timeout:
                        # Congestion Protection: Client is too slow to absorb frames.
                        # Drop this frame for this client to prevent blocking the whole server.
                        pass
                    except (BrokenPipeError, ConnectionResetError):
                        stale_clients.append(sock)
                
                # Cleanup
                for sock in stale_clients:
                    self.clients.remove(sock)
            
            # Log performance metrics every 5 seconds
            current_time = time.time()
            elapsed = current_time - last_log_time
            if elapsed >= 5.0 and self.frames_sent > 0:
                mbps = (self.bytes_sent * 8 / 1000000) / elapsed
                fps = self.frames_sent / elapsed / max(1, len(self.clients)) # Average FPS per client
                num_clients = len(self.clients)
                
                if num_clients > 0:
                    print(f"[PERF] {num_clients} Clients | {fps:.1f} FPS | {mbps:.2f} Mbps Throughput")
                
                # Reset counters
                self.bytes_sent = 0
                self.frames_sent = 0
                last_log_time = current_time
                
            time.sleep(1/30) # Rate limit broadcast loop to 30fps max

if __name__ == "__main__":
    import ssl
    server = TCPStreamingServer(use_tls=True)
    try:
        server.start()
    except KeyboardInterrupt:
        print("\n[!] Shutting down server (Ctrl+C)")
        server.stop()
        print("[!] Server stopped cleanly.")
