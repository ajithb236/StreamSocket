import argparse
import asyncio
import os
import socket
import ssl
import statistics
import struct
import threading
import time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlencode

import websockets
from dotenv import load_dotenv


load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

DEFAULT_USER = os.environ.get("STREAM_USER", "admin")
DEFAULT_PASSWORD = os.environ.get("STREAM_PASSWORD", "admin123")


@dataclass
class ClientResult:
    client_id: int
    mode: str
    connected: bool = False
    frames: int = 0
    bytes_received: int = 0
    elapsed: float = 0.0
    connect_error: Optional[str] = None
    runtime_error: Optional[str] = None
    avg_inter_frame_ms: float = 0.0
    max_inter_frame_ms: float = 0.0

    @property
    def fps(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return self.frames / self.elapsed

    @property
    def throughput_mbps(self) -> float:
        if self.elapsed <= 0:
            return 0.0
        return (self.bytes_received * 8 / 1_000_000) / self.elapsed


def recv_exact(sock: socket.socket, size: int) -> Optional[bytes]:
    buffer = b""
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer


def summarize_results(results: List[ClientResult]) -> None:
    total_clients = len(results)
    connected = [result for result in results if result.connected]
    failed = [result for result in results if not result.connected]
    runtime_failed = [result for result in connected if result.runtime_error]

    print("=" * 64)
    print("LOAD TEST SUMMARY")
    print("=" * 64)
    print(f"Clients attempted        : {total_clients}")
    print(f"Clients connected        : {len(connected)}")
    print(f"Connect failures         : {len(failed)}")
    print(f"Runtime failures         : {len(runtime_failed)}")

    if connected:
        fps_values = [result.fps for result in connected]
        mbps_values = [result.throughput_mbps for result in connected]
        frame_values = [result.frames for result in connected]
        latency_values = [result.avg_inter_frame_ms for result in connected if result.avg_inter_frame_ms > 0]

        print(f"Avg FPS per client       : {statistics.mean(fps_values):.2f}")
        print(f"Median FPS per client    : {statistics.median(fps_values):.2f}")
        print(f"Min / Max FPS            : {min(fps_values):.2f} / {max(fps_values):.2f}")
        print(f"Avg throughput per client: {statistics.mean(mbps_values):.2f} Mbps")
        print(f"Total frames received    : {sum(frame_values)}")
        print(f"Total data received      : {sum(result.bytes_received for result in connected) / (1024 * 1024):.2f} MB")
        if latency_values:
            print(f"Avg inter-frame time     : {statistics.mean(latency_values):.2f} ms")

    if failed:
        print("\nConnect failures:")
        for result in failed[:10]:
            print(f"  client {result.client_id}: {result.connect_error}")

    if runtime_failed:
        print("\nRuntime failures:")
        for result in runtime_failed[:10]:
            print(f"  client {result.client_id}: {result.runtime_error}")


def run_tcp_client(
    client_id: int,
    host: str,
    port: int,
    use_tls: bool,
    username: str,
    password: str,
    duration: float,
    start_delay: float,
    results: List[ClientResult],
) -> None:
    result = ClientResult(client_id=client_id, mode="tcp")
    results[client_id] = result

    if start_delay > 0:
        time.sleep(start_delay)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if use_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=host)

    arrival_times = []
    last_frame_time = None
    start_time = None

    try:
        sock.connect((host, port))
        auth_msg = f"AUTH {username} {password}\n".encode("utf-8")
        sock.sendall(auth_msg)
        auth_resp = sock.recv(1024)
        if b"AUTH_SUCCESS" not in auth_resp:
            result.connect_error = f"auth failed: {auth_resp!r}"
            return

        result.connected = True
        start_time = time.time()

        while time.time() - start_time < duration:
            header = recv_exact(sock, 4)
            if not header:
                break

            (payload_size,) = struct.unpack(">I", header)
            payload = recv_exact(sock, payload_size)
            if not payload:
                break

            now = time.time()
            if last_frame_time is not None:
                arrival_times.append((now - last_frame_time) * 1000)
            last_frame_time = now

            result.frames += 1
            result.bytes_received += 4 + payload_size
    except Exception as exc:
        if result.connected:
            result.runtime_error = str(exc)
        else:
            result.connect_error = str(exc)
    finally:
        if start_time is not None:
            result.elapsed = time.time() - start_time
        try:
            sock.close()
        except OSError:
            pass

    if arrival_times:
        result.avg_inter_frame_ms = statistics.mean(arrival_times)
        result.max_inter_frame_ms = max(arrival_times)


def run_tcp_load(args: argparse.Namespace) -> None:
    results = [ClientResult(client_id=i, mode="tcp") for i in range(args.clients)]
    threads = []

    print(f"[INFO] Starting TCP load test with {args.clients} clients for {args.duration}s")
    print(f"[INFO] Target: {'tls' if args.tls else 'tcp'}://{args.host}:{args.port}")

    for client_id in range(args.clients):
        thread = threading.Thread(
            target=run_tcp_client,
            args=(
                client_id,
                args.host,
                args.port,
                args.tls,
                args.username,
                args.password,
                args.duration,
                client_id * args.stagger,
                results,
            ),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    summarize_results(results)


async def run_ws_client(
    client_id: int,
    uri: str,
    duration: float,
    connect_timeout: float,
    ssl_context: Optional[ssl.SSLContext],
    results: List[ClientResult],
    start_delay: float,
) -> None:
    result = ClientResult(client_id=client_id, mode="ws")
    results[client_id] = result

    if start_delay > 0:
        await asyncio.sleep(start_delay)

    start_time = None
    arrival_times = []
    last_frame_time = None

    try:
        async with websockets.connect(
            uri,
            ssl=ssl_context,
            open_timeout=connect_timeout,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            result.connected = True
            start_time = time.time()

            while time.time() - start_time < duration:
                remaining = max(0.1, duration - (time.time() - start_time))
                payload = await asyncio.wait_for(websocket.recv(), timeout=min(2.0, remaining + 0.5))
                now = time.time()
                if last_frame_time is not None:
                    arrival_times.append((now - last_frame_time) * 1000)
                last_frame_time = now

                if isinstance(payload, str):
                    continue

                result.frames += 1
                result.bytes_received += len(payload)
    except Exception as exc:
        if result.connected:
            result.runtime_error = str(exc)
        else:
            result.connect_error = str(exc)
    finally:
        if start_time is not None:
            result.elapsed = time.time() - start_time

    if arrival_times:
        result.avg_inter_frame_ms = statistics.mean(arrival_times)
        result.max_inter_frame_ms = max(arrival_times)


async def run_ws_load_async(args: argparse.Namespace) -> None:
    query = urlencode({"username": args.username, "password": args.password})
    uri = f"{args.uri}?{query}"
    results = [ClientResult(client_id=i, mode="ws") for i in range(args.clients)]

    ssl_context = None
    if uri.startswith("wss://"):
        ssl_context = ssl.create_default_context()
        if args.insecure:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

    print(f"[INFO] Starting WebSocket load test with {args.clients} clients for {args.duration}s")
    print(f"[INFO] Target: {uri}")

    tasks = [
        run_ws_client(
            client_id=i,
            uri=uri,
            duration=args.duration,
            connect_timeout=args.connect_timeout,
            ssl_context=ssl_context,
            results=results,
            start_delay=i * args.stagger,
        )
        for i in range(args.clients)
    ]
    await asyncio.gather(*tasks)
    summarize_results(results)


def run_ws_load(args: argparse.Namespace) -> None:
    asyncio.run(run_ws_load_async(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-client load testing for the streaming system.")
    subparsers = parser.add_subparsers(dest="mode")

    tcp_parser = subparsers.add_parser("tcp", help="Load test the raw TCP/TLS server.")
    tcp_parser.add_argument("--host", default="127.0.0.1")
    tcp_parser.add_argument("--port", type=int, default=9999)
    tcp_parser.add_argument("--clients", type=int, default=5)
    tcp_parser.add_argument("--duration", type=float, default=15.0)
    tcp_parser.add_argument("--stagger", type=float, default=0.2, help="Seconds to wait between client starts.")
    tcp_parser.add_argument("--tls", action="store_true", help="Use TLS when connecting to the TCP server.")
    tcp_parser.add_argument("--username", default=DEFAULT_USER)
    tcp_parser.add_argument("--password", default=DEFAULT_PASSWORD)
    tcp_parser.set_defaults(func=run_tcp_load)

    ws_parser = subparsers.add_parser("ws", help="Load test the WebSocket/WSS bridge.")
    ws_parser.add_argument("--uri", default="wss://localhost:8000/ws")
    ws_parser.add_argument("--clients", type=int, default=5)
    ws_parser.add_argument("--duration", type=float, default=15.0)
    ws_parser.add_argument("--stagger", type=float, default=0.2, help="Seconds to wait between client starts.")
    ws_parser.add_argument("--connect-timeout", type=float, default=10.0)
    ws_parser.add_argument("--username", default=DEFAULT_USER)
    ws_parser.add_argument("--password", default=DEFAULT_PASSWORD)
    ws_parser.add_argument("--insecure", action="store_true", help="Skip certificate validation for self-signed WSS certs.")
    ws_parser.set_defaults(func=run_ws_load)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    if len(os.sys.argv) == 1:
        parser.print_help()
        print("\nExamples:")
        print("  python load_test.py tcp --tls --clients 10 --duration 20")
        print("  python load_test.py ws --uri wss://localhost:8000/ws --insecure --clients 10 --duration 20")
        raise SystemExit(2)
    arguments = parser.parse_args()
    if not hasattr(arguments, "func"):
        parser.print_help()
        raise SystemExit(2)
    arguments.func(arguments)
