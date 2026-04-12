
import socket
import ssl
import threading
import time
import select
import signal
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tcp_server.protocol import build_frame_packet, send_packet
from capture.screen import ScreenCapture
from db.auth import DatabaseAdapter

class TCPStreamingServer:
    def __init__(self, host='0.0.0.0', port=9999, use_tls=True):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.running = False
        self.capture_fps = max(1, int(os.environ.get("STREAM_FPS", "30")))
        self.capture_quality = max(10, min(95, int(os.environ.get("STREAM_JPEG_QUALITY", "40"))))
        self.capture_scale = max(0.1, min(1.0, float(os.environ.get("STREAM_SCALE", "0.75"))))
        self.send_timeout = max(0.01, float(os.environ.get("STREAM_SEND_TIMEOUT", "0.05")))
        self.adaptive_streaming = os.environ.get("STREAM_ADAPTIVE", "1") != "0"
        self.current_profile = None
        
        self.clients = []
        self.clients_lock = threading.Lock()
        self.stop_lock = threading.Lock()
        self.db = DatabaseAdapter(pool_size=32)
        self.screencap = ScreenCapture(
            fps=self.capture_fps,
            quality=self.capture_quality,
            scale=self.capture_scale,
        )
        self.bytes_sent = 0
        self.frames_sent = 0
        self.start_time = time.time()

    def start(self):
        self.screencap.start()
        
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(100)
        self.server_socket.settimeout(1.0)
        print(f"[INFO] TCP Server Listening on {self.host}:{self.port}")
        print(
            f"[INFO] Capture settings | fps={self.capture_fps} quality={self.capture_quality} scale={self.capture_scale:.2f}"
        )
        if self.use_tls:
            context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            cert_path = os.path.join(base_dir, 'cert.pem')
            key_path = os.path.join(base_dir, 'key.pem')
            context.load_cert_chain(certfile=cert_path, keyfile=key_path)
            self.server_socket = context.wrap_socket(self.server_socket, server_side=True)
            self.server_socket.settimeout(1.0)
            print("[INFO] TLS Encryption Enabled")
        self.running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()
        try:
            while self.running:
                try:
                    client_sock, addr = self.server_socket.accept()
                except socket.timeout:
                    continue
                except ssl.SSLError as exc:
                    print(f"[WARN] Rejected non-TLS or invalid TLS client during handshake: {exc}")
                    continue
                except OSError as exc:
                    if not self.running:
                        break
                    print(f"[WARN] Server accept failed: {exc}")
                    continue

                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                threading.Thread(target=self._handle_client, args=(client_sock, addr), daemon=True).start()
        except KeyboardInterrupt:
            self.stop()
            
    def stop(self):
        with self.stop_lock:
            if not self.running:
                return

            self.running = False
            self.screencap.stop()

            if hasattr(self, 'server_socket'):
                try:
                    self.server_socket.close()
                except OSError:
                    pass

            with self.clients_lock:
                client_sockets = list(self.clients)
                self.clients.clear()

            for client_sock in client_sockets:
                try:
                    client_sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    client_sock.close()
                except OSError:
                    pass

            if hasattr(self, 'broadcast_thread') and self.broadcast_thread.is_alive():
                self.broadcast_thread.join(timeout=2.0)
            
    def _handle_client(self, client_sock, addr):
        print(f"[INFO] Client {addr} connected.")
        
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
                print(f"[ERROR] Client {addr} failed authentication.")
                self.db.log_event("DISCONNECT_UNAUTHORIZED", username, addr[0], "Failed login attempt")
                return
            
            client_sock.sendall(b"AUTH_SUCCESS")
            client_sock.settimeout(self.send_timeout)
            
            # Add to broadcast pool
            with self.clients_lock:
                self.clients.append(client_sock)
                
            print(f"[INFO] Client {addr} authenticated successfully.")
            self.db.log_event("CONNECT_SUCCESS", username, addr[0], "Client started streaming session")

            # Wait for the client socket to become readable so we can detect disconnects
            # without treating an idle streaming client as an error.
            while self.running:
                ready, _, _ = select.select([client_sock], [], [], 1.0)
                if not ready:
                    continue
                try:
                    data = client_sock.recv(1024)
                except (socket.timeout, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    continue
                except OSError as exc:
                    if getattr(exc, "winerror", None) == 10035:
                        continue
                    raise
                if not data:
                    break
        except Exception as e:
            print(f"[ERROR] Client {addr} exception: {e}")
            self.db.log_event("ERROR", username, addr[0], str(e))
        finally:
            with self.clients_lock:
                if client_sock in self.clients:
                    self.clients.remove(client_sock)
            client_sock.close()
            print(f"[INFO] Client {addr} disconnected.")
            self.db.log_event("DISCONNECT", username, addr[0], "Client ended session")

    def _broadcast_loop(self):
        last_log_time = time.time()
        
        while self.running:
            frame = self.screencap.get_latest_frame()
            if not frame:
                time.sleep(0.01)
                continue
                
            packet = build_frame_packet(frame)
            packet_size = len(packet)
            
            with self.clients_lock:
                client_sockets = list(self.clients)
                num_clients = len(client_sockets)

            self._update_stream_profile(num_clients)

            stale_clients = []
            for sock in client_sockets:
                try:
                    send_packet(sock, packet)
                    self.bytes_sent += packet_size
                    self.frames_sent += 1
                except socket.timeout:
                    # sendall() may partially write before timing out, which would corrupt framing.
                    # Disconnect the slow client rather than stalling or desynchronizing the stream.
                    stale_clients.append(sock)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError, OSError):
                    stale_clients.append(sock)

            if stale_clients:
                with self.clients_lock:
                    for sock in stale_clients:
                        if sock in self.clients:
                            self.clients.remove(sock)
                        try:
                            sock.close()
                        except OSError:
                            pass
            
            # Log performance metrics every 5 seconds
            current_time = time.time()
            elapsed = current_time - last_log_time
            if elapsed >= 5.0 and self.frames_sent > 0:
                mbps = (self.bytes_sent * 8 / 1000000) / elapsed
                fps = self.frames_sent / elapsed / max(1, num_clients) # Average FPS per client
                
                if num_clients > 0:
                    print(f"[PERF] {num_clients} Clients | {fps:.1f} FPS | {mbps:.2f} Mbps Throughput")
                
                # Reset counters
                self.bytes_sent = 0
                self.frames_sent = 0
                last_log_time = current_time
                
            with self.screencap._settings_lock:
                current_fps = self.screencap.fps
            time.sleep(1.0 / max(1, current_fps))

    def _update_stream_profile(self, num_clients):
        if not self.adaptive_streaming:
            return

        if num_clients <= 10:
            profile = ("premium", min(self.capture_fps, 30), self.capture_quality, self.capture_scale)
        elif num_clients >= 180:
            profile = ("ultra", self.capture_fps, 16, self.capture_scale)
        elif num_clients >= 120:
            profile = ("high", self.capture_fps, 20, self.capture_scale)
        elif num_clients >= 70:
            profile = ("medium", self.capture_fps, 25, self.capture_scale)
        elif num_clients >= 30:
            profile = ("light", self.capture_fps, 30, self.capture_scale)
        else:
            profile = ("base", self.capture_fps, self.capture_quality, self.capture_scale)

        if self.current_profile == profile[0]:
            return

        _, fps, quality, scale = profile
        self.screencap.update_settings(
            fps=fps,
            quality=min(self.capture_quality, quality),
            scale=scale,
        )
        self.current_profile = profile[0]
        print(
            f"[INFO] Adaptive stream profile -> {profile[0]} | clients={num_clients} "
            f"fps={fps} quality={min(self.capture_quality, quality)} scale={scale:.2f}"
        )

if __name__ == "__main__":
    server = TCPStreamingServer(use_tls=True)

    def handle_shutdown(signum, frame):
        print(f"\n[WARN] Received signal {signum}. Shutting down server...")
        server.stop()

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_shutdown)

    try:
        server.start()
    except KeyboardInterrupt:
        print("\n[WARN] Shutting down server (Ctrl+C)")
        server.stop()
        print("[INFO] Server stopped cleanly.")
