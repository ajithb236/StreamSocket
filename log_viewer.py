import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
import sys
import os

 # Ensure db module is found
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db.auth import DatabaseAdapter

app = FastAPI()
db = DatabaseAdapter()

@app.get("/", response_class=HTMLResponse)
async def view_logs(request: Request):
    # Only allow requests from localhost
    if request.client.host not in ["127.0.0.1", "::1", "localhost"]:
        raise HTTPException(status_code=403, detail="Forbidden: Logs can only be accessed from the server's localhost.")
    
    try:
        conn = db.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 200;")
        logs = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        return f"<h3>Database Connection Error:</h3><p>{e}</p>"

    # Simple HTML log viewer
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Server Log Viewer</title>
        <style>
            body { font-family: monospace; background: #1e1e1e; color: #ddd; padding: 20px; }
            h2 { color: #fff; border-bottom: 1px solid #444; padding-bottom: 10px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; background: #2d2d2d; }
            th, td { padding: 12px; border: 1px solid #444; text-align: left; }
            th { background: #111; color: #fff; position: sticky; top: 0; }
            tr:hover { background: #3a3a3a; }
            .SUCCESS { color: #4caf50; font-weight: bold; }
            .AUTH_FAILURE, .ERROR, .DISCONNECT_UNAUTHORIZED { color: #f44336; font-weight: bold; }
            .DISCONNECT { color: #ff9800; }
        </style>
    </head>
    <body>
        <h2>System Logs (Localhost Only)</h2>
        <table>
            <tr>
                <th>ID</th>
                <th>Timestamp</th>
                <th>Event Type</th>
                <th>Username</th>
                <th>IP Address</th>
                <th>Message</th>
            </tr>
    """
    
    for row in logs:
        event_type = row.get("event_type", "")
        css_class = event_type if event_type else ""
        
        # Fallback stylings for sub-types
        if "SUCCESS" in event_type: css_class = "SUCCESS"
        if "FAIL" in event_type: css_class = "ERROR"
        
        html_content += f"""
            <tr>
                <td>{row.get('id')}</td>
                <td>{row.get('timestamp')}</td>
                <td class="{css_class}">{event_type}</td>
                <td>{row.get('username') or '-'}</td>
                <td>{row.get('ip_addr') or '-'}</td>
                <td>{row.get('message') or '-'}</td>
            </tr>
        """
        
    html_content += """
        </table>
    </body>
    </html>
    """
    return html_content

if __name__ == "__main__":
    # BIND TO 127.0.0.1 ONLY! 
    # This prevents anyone on the network (e.g. mobile phones or outside PCs) from reaching this port.
    uvicorn.run("log_viewer:app", host="127.0.0.1", port=8080, reload=True)
