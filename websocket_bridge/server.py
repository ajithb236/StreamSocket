import asyncio
import struct
import socket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import sys
import ssl
from dotenv import load_dotenv

# Add parent dir to resolve potential absolute paths
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.auth import DatabaseAdapter

env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(dotenv_path=env_path)

from contextlib import asynccontextmanager, suppress


class BridgeClient:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue = asyncio.Queue(maxsize=1)
        self.sender_task = None

    def enqueue_latest(self, payload: bytes):
        if self.sender_task and self.sender_task.done():
            return

        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with suppress(asyncio.QueueFull):
                self.queue.put_nowait(payload)

    async def sender_loop(self):
        try:
            while True:
                payload = await self.queue.get()
                if payload is None:
                    break
                await self.websocket.send_bytes(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as e:
            print(f"[WARN] WebSocket sender closed: {e}")
        finally:
            clients.discard(self)
            with suppress(Exception):
                await self.websocket.close()

    async def close(self):
        clients.discard(self)
        if self.sender_task and not self.sender_task.done():
            self.sender_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.sender_task
        with suppress(Exception):
            await self.websocket.close()


db = DatabaseAdapter(pool_size=32)

@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge_task = asyncio.create_task(connect_to_tcp_and_broadcast())
    yield
    bridge_task.cancel()
    with suppress(asyncio.CancelledError):
        await bridge_task

app = FastAPI(lifespan=lifespan)


client_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "client")
app.mount("/client", StaticFiles(directory=client_dir), name="client")

# Optional: Redirect root to /client/index.html for convenience
from fastapi.responses import RedirectResponse
@app.get("/")
async def root():
    return RedirectResponse(url="/client/index.html")

# Registration endpoint
from fastapi import Request
from fastapi.responses import JSONResponse
@app.post("/register")
async def register(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return JSONResponse({"success": False, "error": "Username and password required."}, status_code=400)
    if len(username) < 3 or len(password) < 6:
        return JSONResponse({"success": False, "error": "Username or password too short."}, status_code=400)
    # Check if user exists
    try:
        conn = db.get_connection()
        if conn is None:
            return JSONResponse({"success": False, "error": "Database unavailable."}, status_code=500)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return JSONResponse({"success": False, "error": "Username already exists."}, status_code=409)
        # Hash password
        import bcrypt
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, hashed))
        conn.commit()
        cursor.close()
        conn.close()
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

clients = set()

TCP_HOST = '127.0.0.1'
TCP_PORT = 9999
USE_TLS = True
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

def recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

async def connect_to_tcp_and_broadcast():
    # Connect to the TCP server, keep reading frames, and fan them out without waiting on each browser socket.
    while True:
        ssl_ctx = None
        writer = None
        if USE_TLS:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            reader, writer = await asyncio.open_connection(
                TCP_HOST, TCP_PORT, ssl=ssl_ctx
            )

            auth_msg = f"AUTH {os.environ.get('STREAM_USER', 'admin')} {os.environ.get('STREAM_PASSWORD', 'admin123')}\n"
            auth_bytes = auth_msg.encode('utf-8')
            if auth_bytes:
                if writer.is_closing():
                    raise ConnectionError("Writer closed before auth write.")
                writer.write(auth_bytes)
                await writer.drain()

            auth_resp = await reader.read(32)
            if b"AUTH_SUCCESS" not in auth_resp:
                raise ConnectionError(
                    f"Bridge failed to auth with TCP server. Server responded with: {auth_resp} using user: {os.environ.get('STREAM_USER')}"
                )

            print("[INFO] Bridge connected to TCP Backend. Ready to broadcast to WS clients.")

            while True:
                header = await reader.readexactly(4)
                if not header:
                    break
                (data_size,) = struct.unpack('>I', header)
                payload = await reader.readexactly(data_size)
                if not payload:
                    break

                if payload and clients:
                    for client in tuple(clients):
                        client.enqueue_latest(payload)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[ERROR] Bridge error: {e}")
        finally:
            if writer is not None:
                with suppress(Exception):
                    writer.close()
                    await writer.wait_closed()

        await asyncio.sleep(2)



@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    username: str = Query(None), 
    password: str = Query(None)
):
    # WebSocket handshake and upgrade handled by FastAPI/Starlette
    # Authenticate before accepting using the real Database
    if not db.authenticate_user(username, password):
        await websocket.close(code=1008)  # Policy Violation
        return

    # Accept the WebSocket connection
    await websocket.accept()
    
    # Add to broadcast pool
    client = BridgeClient(websocket)
    clients.add(client)
    client.sender_task = asyncio.create_task(client.sender_loop())
    try:
        while True:
            # Wait for any client message and exit as soon as Starlette reports disconnect.
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await client.close()
        
if __name__ == "__main__":
    cert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wss_cert.pem")
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wss_key.pem")
    browser_proto = "https"
    print(f"[INFO] Browser UI available at {browser_proto}://localhost:{WEB_PORT}/client/index.html")
    print("[INFO] If the cert is self-signed, accept the browser warning once before streaming.")
    uvicorn.run(
        "server:app",
        host=WEB_HOST,
        port=WEB_PORT,
        reload=True,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path
    )
