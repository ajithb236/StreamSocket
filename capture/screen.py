import mss
import cv2
import numpy as np
import time
import threading

class ScreenCapture:
    def __init__(self, fps=30, quality=50):
        self.fps = fps
        self.quality = quality
        self.running = False
        self._sct = mss.mss()
        self.monitor = self._sct.monitors[1]  # Primary monitor
        
        # Thread-safe frame sharing
        self._latest_jpeg = b""
        self._lock = threading.Lock()
        
    def get_latest_frame(self):
        with self._lock:
            return self._latest_jpeg
            
    def set_latest_frame(self, data):
        with self._lock:
            self._latest_jpeg = data

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        
    def stop(self):
        self.running = False
        if hasattr(self, '_thread'):
            self._thread.join()

    def _capture_loop(self):
        interval = 1.0 / self.fps
        
        # JPEG quality: 0-100
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        
        # In Windows with mss, the thread running grab() MUST be the one that created 
        # the mss context, otherwise HDC handles fail. We instantiate it per-thread.
        with mss.mss() as thread_sct:
            while self.running:
                start_time = time.time()

                # Grab raw screen
                sct_img = thread_sct.grab(self.monitor)

                # Convert mss object to numpy array via OpenCV (BGRA format)        
                img = np.array(sct_img)

                # Drop the Alpha channel for size reduction: BGRA -> BGR
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                # Compress to JPEG bytes quickly
                ret, buffer = cv2.imencode('.jpg', img, encode_param)

                if ret:
                    self.set_latest_frame(buffer.tobytes())

                elapsed = time.time() - start_time
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
