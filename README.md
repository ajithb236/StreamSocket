# Secure Remote Desktop Streaming System

This project demonstrates a real-time secure stream, focusing on low-level TCP principles.

## Prerequisites

1. Create a virtual environment and install requirements:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   pip install mss opencv-python fastapi uvicorn websockets
   ```

*(Requires a dual-monitor setup or falls back to monitor 1 for `mss`).*

## Running the Architecture

You need two console windows.

### 1. Start the Raw TCP Server
This is the core streaming server utilizing bare `socket` logic.
```bash
cd tcp_server
python server.py
```
*(Note: It is set to `use_tls=False` by default in the `__main__` block to ease initial testing. To use TLS, you must generate `cert.pem` and `key.pem` and flip the boolean).*

### 2. Start the WebSocket Bridge & Frontend
This connects to the raw TCP server, parses frames, and hosts the FastAPI server.
```bash
cd websocket_bridge
python server.py
```

### 3. Connect as Client
1. Open a browser and go to `http://localhost:8000/client/index.html`
2. Enter the test credentials:
   - **Username:** `testuser`
   - **Password:** `password123`
3. Click "Connect". The WebSocket connection will upgrade, authenticate, and display real-time frames.

## Project Structure Highlights
* `tcp_server/protocol.py`: Demonstrates handling TCP fragmentation via 4-byte framing.
* `tcp_server/server.py`: Custom Socket implementations including `TCP_NODELAY` and `SO_REUSEADDR`.
* `capture/screen.py`: Handles high-speed raw extraction and JPEG compression.
* `websocket_bridge/server.py`: Serves as the HTTP->WebSocket Upgrader (`101 Switching Protocols`), effectively bridging Web clients to our custom TCP core.
* `experiments/ANALYSIS.md`: Documentation on network internals.
