import mss
import cv2
import numpy as np
import time
import threading

class ScreenCapture:
    def __init__(self, fps=30, quality=50, scale=1.0):
        self.fps = max(1, int(fps))
        self.quality = max(10, min(95, int(quality)))
        self.scale = max(0.1, min(1.0, float(scale)))
        self.running = False
        self._sct = mss.mss()
        self.monitor = self._sct.monitors[1]  # Primary monitor
        
        # Thread-safe frame sharing
        self._latest_jpeg = b""
        self._lock = threading.Lock()
        self._settings_lock = threading.Lock()
        
    def get_latest_frame(self):
        with self._lock:
            return self._latest_jpeg
            
    def set_latest_frame(self, data):
        with self._lock:
            self._latest_jpeg = data

    def update_settings(self, fps=None, quality=None, scale=None):
        with self._settings_lock:
            if fps is not None:
                self.fps = max(1, int(fps))
            if quality is not None:
                self.quality = max(10, min(95, int(quality)))
            if scale is not None:
                self.scale = max(0.1, min(1.0, float(scale)))

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
    def stop(self):
        self.running = False
        if hasattr(self, '_thread'):
            self._thread.join()

    def _capture_loop(self):
        # In Windows with mss, the thread running grab() MUST be the one that created 
        # the mss context, otherwise HDC handles fail. We instantiate it per-thread.
        with mss.mss() as thread_sct:
            while self.running:
                start_time = time.time()
                with self._settings_lock:
                    fps = self.fps
                    quality = self.quality
                    scale = self.scale

                interval = 1.0 / fps
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]

                # Grab raw screen
                sct_img = thread_sct.grab(self.monitor)

                # Convert mss object to numpy array via OpenCV (BGRA format)        
                img = np.array(sct_img)

                # Drop the Alpha channel for size reduction: BGRA -> BGR
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                if scale < 1.0:
                    resized_width = max(1, int(img.shape[1] * scale))
                    resized_height = max(1, int(img.shape[0] * scale))
                    img = cv2.resize(img, (resized_width, resized_height), interpolation=cv2.INTER_AREA)

                # Compress to JPEG bytes quickly
                ret, buffer = cv2.imencode('.jpg', img, encode_param)

                if ret:
                    self.set_latest_frame(buffer.tobytes())

                elapsed = time.time() - start_time
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
