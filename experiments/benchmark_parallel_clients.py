import socket
import ssl
import threading
import time
import struct
import statistics
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
STREAM_USER = os.environ.get("STREAM_USER", "admin")
STREAM_PASSWORD = os.environ.get("STREAM_PASSWORD", "admin123")

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 9999
NUM_CLIENTS = 50  # You can increase this if your server can handle more
USE_TLS = True
TEST_DURATION = 10  # seconds

def recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

class ClientStats:
    def __init__(self, client_id):
        self.client_id = client_id
        self.frames_received = 0
        self.total_bytes = 0
        self.arrival_times = []
        self.error = None

def client_task(client_id, stats_list):
    stats = ClientStats(client_id)
    try:
        import random
        time.sleep(random.uniform(0.01, 0.1))  # Stagger connections by 10–100 ms
        print(f"[Client {client_id}] Connecting...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if USE_TLS:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            sock = context.wrap_socket(sock, server_hostname=SERVER_HOST)
        sock.connect((SERVER_HOST, SERVER_PORT))
        print(f"[Client {client_id}] Connected.")
        auth_msg = f"AUTH {STREAM_USER} {STREAM_PASSWORD}\n".encode('utf-8')
        sock.sendall(auth_msg)
        auth_resp = sock.recv(1024)
        if b"AUTH_SUCCESS" not in auth_resp:
            print(f"[Client {client_id}] Authentication failed.")
            stats.error = "Authentication failed"
            stats_list[client_id] = stats
            sock.close()
            return
        print(f"[Client {client_id}] Authenticated.")
        start_time = time.time()
        last_frame_time = start_time
        while time.time() - start_time < TEST_DURATION:
            header = recv_exact(sock, 4)
            if not header:
                break
            (data_size,) = struct.unpack('>I', header)
            payload = recv_exact(sock, data_size)
            if not payload:
                break
            now = time.time()
            inter_frame_time = now - last_frame_time
            stats.arrival_times.append(inter_frame_time * 1000)  # ms
            last_frame_time = now
            stats.frames_received += 1
            stats.total_bytes += (4 + data_size)
        print(f"[Client {client_id}] Finished. Frames: {stats.frames_received}, Bytes: {stats.total_bytes}")
        sock.close()
    except Exception as e:
        print(f"[Client {client_id}] Error: {e}")
        stats.error = str(e)
    stats_list[client_id] = stats

def main():
    threads = []
    stats_list = [None] * NUM_CLIENTS
    start = time.time()
    for i in range(NUM_CLIENTS):
        t = threading.Thread(target=client_task, args=(i, stats_list))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    end = time.time()

    # Aggregate stats
    total_frames = 0
    total_bytes = 0
    all_latencies = []
    errors = 0
    for stats in stats_list:
        if stats is None or stats.error:
            errors += 1
            continue
        total_frames += stats.frames_received
        total_bytes += stats.total_bytes
        if len(stats.arrival_times) > 1:
            all_latencies.extend(stats.arrival_times[1:])  # skip first

    elapsed = end - start
    throughput_mbps = (total_bytes * 8 / 1000000) / elapsed if elapsed > 0 else 0
    fps = total_frames / elapsed if elapsed > 0 else 0
    if all_latencies:
        avg_latency = statistics.mean(all_latencies)
        jitter = statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0
        max_latency = max(all_latencies)
    else:
        avg_latency = jitter = max_latency = 0

    print("=" * 50)
    print(f"PARALLEL BENCHMARK: {NUM_CLIENTS} clients, {TEST_DURATION}s each, TLS={USE_TLS}")
    print("=" * 50)
    print(f"Test Duration        : {elapsed:.2f} seconds")
    print(f"Total Frames         : {total_frames}")
    print(f"Total Data Transferred: {total_bytes / (1024*1024):.2f} MB")
    print(f"Errors/Failures      : {errors}")
    print("-" * 50)
    print("THROUGHPUT & FRAMERATE")
    print("-" * 50)
    print(f"Avg FPS              : {fps:.2f} Frames/sec")
    print(f"Network Throughput   : {throughput_mbps:.2f} Mbps")
    print("-" * 50)
    print("LATENCY & JITTER (Frame-to-Frame Arrival)")
    print("-" * 50)
    print(f"Avg Inter-frame Time : {avg_latency:.2f} ms")
    print(f"Max Spike (Lag)      : {max_latency:.2f} ms")
    print(f"Jitter (Variance)    : {jitter:.2f} ms")
    print("=" * 50)
    if errors:
        print(f"Some clients failed to connect or authenticate. See error count above.")

if __name__ == "__main__":
    main()
