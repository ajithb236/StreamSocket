let ws;
let frameCount = 0;
let lastTime = performance.now();

const canvas = document.getElementById('screen-canvas');
const ctx = canvas.getContext('2d');

// Registration modal logic
function showRegister() {
    document.getElementById('register-modal').style.display = 'flex';
}
function hideRegister() {
    document.getElementById('register-modal').style.display = 'none';
    document.getElementById('register-error').style.display = 'none';
    document.getElementById('register-success').style.display = 'none';
}

async function registerUser() {
    const username = document.getElementById('reg-username').value.trim();
    const password = document.getElementById('reg-password').value;
    const password2 = document.getElementById('reg-password2').value;
    const errorDiv = document.getElementById('register-error');
    const successDiv = document.getElementById('register-success');
    errorDiv.style.display = 'none';
    successDiv.style.display = 'none';
    if (!username || !password || !password2) {
        errorDiv.innerText = 'All fields are required.';
        errorDiv.style.display = 'block';
        return;
    }
    if (password !== password2) {
        errorDiv.innerText = 'Passwords do not match.';
        errorDiv.style.display = 'block';
        return;
    }
    try {
        const resp = await fetch('/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
            successDiv.style.display = 'block';
            errorDiv.style.display = 'none';
        } else {
            errorDiv.innerText = data.error || 'Registration failed.';
            errorDiv.style.display = 'block';
        }
    } catch (e) {
        errorDiv.innerText = 'Registration failed.';
        errorDiv.style.display = 'block';
    }
}

function login() {
    const user = document.getElementById('username').value;
    const pass = document.getElementById('password').value;
    const errObj = document.getElementById('login-error');
    errObj.style.display = 'none';

    // Use wss:// if page is loaded over https, else ws://
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${wsProto}://${location.host}/ws?username=${user}&password=${pass}`;
    ws = new WebSocket(wsUrl);
    ws.binaryType = "blob";

    ws.onopen = () => {
        document.getElementById('login-view').style.display = 'none';
        document.getElementById('streaming-view').style.display = 'flex';
        // Show connected status
        document.getElementById('status').innerText = "Connected";
        document.getElementById('status').style.color = "#00ff00";
        requestAnimationFrame(updateFPS);
    };

    ws.onmessage = (event) => {
        const blob = event.data;
        const img = new Image();
        img.onload = () => {
            canvas.width = img.width;
            canvas.height = img.height;
            ctx.drawImage(img, 0, 0);
            URL.revokeObjectURL(img.src);
            frameCount++;
        };
        img.src = URL.createObjectURL(blob);
    };

    ws.onerror = (err) => {
        errObj.style.display = 'block';
    };

    ws.onclose = () => {
        document.getElementById('streaming-view').style.display = 'none';
        document.getElementById('login-view').style.display = 'flex';
        document.getElementById('status').innerText = "Disconnected";
        document.getElementById('status').style.color = "red";
    };
}

function updateFPS() {
    const now = performance.now();
    if (now - lastTime >= 1000) {
        document.getElementById('fps').innerText = frameCount;
        frameCount = 0;
        lastTime = now;
    }
    requestAnimationFrame(updateFPS);
}
