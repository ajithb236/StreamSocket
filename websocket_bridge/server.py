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

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(connect_to_tcp_and_broadcast())
    yield

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
    db = DatabaseAdapter(pool_size=32)
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

def recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf

async def connect_to_tcp_and_broadcast():
    # Connects to the TCP server using asyncio streams with SSL, receives frames, and broadcasts to WebSocket clients.
    ssl_ctx = None
    if USE_TLS:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        reader, writer = await asyncio.open_connection(
            TCP_HOST, TCP_PORT, ssl=ssl_ctx
        )

        # Authenticate with the TCP server securely via .env credentials
        auth_msg = f"AUTH {os.environ.get('STREAM_USER', 'admin')} {os.environ.get('STREAM_PASSWORD', 'admin123')}\n"
        auth_bytes = auth_msg.encode('utf-8')
        if auth_bytes:
            if not writer.is_closing():
                try:
                    writer.write(auth_bytes)
                    await writer.drain()
                except AssertionError as ae:
                    print(f"[ERROR] AssertionError during writer.write(auth_bytes): {ae}")
                    writer.close()
                    await writer.wait_closed()
                    return
            else:
                print("[ERROR] Writer is already closing before auth write.")
                return

        auth_resp = await reader.read(32)
        if b"AUTH_SUCCESS" not in auth_resp:
            print(f"[ERROR] Bridge failed to auth with TCP server. Server responded with: {auth_resp} using user: {os.environ.get('STREAM_USER')}")
            writer.close()
            await writer.wait_closed()
            return

        print("[INFO] Bridge connected to TCP Backend. Ready to broadcast to WS clients.")

        while True:
            # Read the 4-byte frame header
            header = await reader.readexactly(4)
            if not header:
                break
            (data_size,) = struct.unpack('>I', header)

            # Read the entire frame payload based on the parsed size
            payload = await reader.readexactly(data_size)
            if not payload:
                break

            # Broadcast to web clients
            if payload and clients:
                if not writer.is_closing():
                    try:
                        coros = [client.send_bytes(payload) for client in clients if payload]
                        await asyncio.gather(*coros, return_exceptions=True)
                    except AssertionError as ae:
                        print(f"[ERROR] AssertionError during broadcast: {ae}")
                        writer.close()
                        await writer.wait_closed()
                        break
                else:
                    print("[ERROR] Writer is closing during broadcast, breaking loop.")
                    break

    except Exception as e:
        print(f"[ERROR] Bridge error: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass



@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, 
    username: str = Query(None), 
    password: str = Query(None)
):
    # WebSocket handshake and upgrade handled by FastAPI/Starlette
    # Authenticate before accepting using the real Database
    db = DatabaseAdapter(pool_size=32)
    if not db.authenticate_user(username, password):
        await websocket.close(code=1008)  # Policy Violation
        return

    # Accept the WebSocket connection
    await websocket.accept()
    
    # Add to broadcast pool
    clients.add(websocket)
    try:
        while True:
            # Wait for any client message (ping, disconnect)
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        clients.remove(websocket)
        
if __name__ == "__main__":
    cert_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wss_cert.pem")
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wss_key.pem")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        ssl_certfile=cert_path,
        ssl_keyfile=key_path
    )
