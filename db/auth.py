
import mysql.connector
import mysql.connector.pooling
import bcrypt
import os
import threading
from dotenv import load_dotenv
import time

load_dotenv()

class DatabaseAdapter:
    def __init__(self, pool_size=32):
        self.config = {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', 'Admin'),
            'database': os.environ.get('DB_NAME', 'remote_stream')
        }
        self._log_lock = threading.Lock()  # Lock for log queue
        self.pool_size = pool_size
        self._init_pool()
        self._auth_cache = {}  # username: password_hash
        self._cache_lock = threading.Lock()
        self._log_queue = []
        self._max_log_queue = 500  # Circuit breaker: max logs to queue
        self._log_dropped = 0
        self._start_log_worker()

    def _init_pool(self):
        try:
            self.pool = mysql.connector.pooling.MySQLConnectionPool(pool_name="mypool", pool_size=self.pool_size, **self.config)
            self.db_available = True
        except Exception as e:
            print(f"[ERROR][DB] Connection pool init failed: {e}")
            self.pool = None
            self.db_available = False



    def get_connection(self):
        if self.pool:
            try:
                return self.pool.get_connection()
            except Exception as e:
                print(f"[ERROR][DB] Pool get_connection failed: {e}")
                self.db_available = False
        # fallback: try to re-init pool
        self._init_pool()
        if self.pool:
            try:
                return self.pool.get_connection()
            except Exception as e:
                print(f"[DB] Pool get_connection failed after re-init: {e}")
        return None

    def authenticate_user(self, username, password):
        # Try DB first, fallback to cache if DB is down
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            if conn is None:
                raise Exception("DB unavailable")
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if user:
                is_valid = bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8'))
                # Cache the hash for fallback
                with self._cache_lock:
                    self._auth_cache[username] = user['password_hash']
                self.log_event("AUTH_SUCCESS" if is_valid else "AUTH_FAILURE", username=username)
                return is_valid
            self.log_event("AUTH_FAILURE_NO_USER", username=username)
            return False
        except Exception as err:
            print(f"[DB] authenticate_user error: {err}")
            # Fallback: check cache
            with self._cache_lock:
                hash_ = self._auth_cache.get(username)
            if hash_:
                is_valid = bcrypt.checkpw(password.encode('utf-8'), hash_.encode('utf-8'))
                print(f"[DB] Fallback to cache for user {username}: {'success' if is_valid else 'fail'}")
                return is_valid
            return False
        finally:
            try:
                if cursor:
                    cursor.close()
            except Exception:
                pass
            try:
                if conn and conn.is_connected():
                    conn.close()
            except Exception:
                pass
                print(f"[WARN][DB] log queue full, dropping logs. Dropped so far: {self._log_dropped}")
    def log_event(self, event_type, username=None, ip_addr=None, message=None):
        # Try to log to DB, else queue for retry
        # Non-blocking: just queue the log, drop if queue is too large
        with self._log_lock:
            if len(self._log_queue) < self._max_log_queue:
                self._log_queue.append((event_type, username, ip_addr, message, time.time()))
            else:
                self._log_dropped += 1
                if self._log_dropped == 1 or self._log_dropped % 100 == 0:
                    print(f"[DB] log queue full, dropping logs. Dropped so far: {self._log_dropped}")
        # Actual DB write is handled by the background worker

    def _start_log_worker(self):
        def worker():
            while True:
                time.sleep(2)
                with self._log_lock:
                    if not self._log_queue:
                        continue
                    queue_copy = self._log_queue[:]
                    self._log_queue.clear()
                for event in queue_copy:
                    conn = None
                    cursor = None
                    try:
                        # Direct DB write here, not via log_event to avoid recursion
                        conn = self.get_connection()
                        if conn is None:
                            raise Exception("DB unavailable")
                        cursor = conn.cursor()
                        query = "INSERT INTO logs (event_type, username, ip_addr, message) VALUES (%s, %s, %s, %s)"
                        cursor.execute(query, (event[0], event[1], event[2], event[3]))
                        conn.commit()
                    except Exception as e:
                        print(f"[DB] log_worker failed to flush log: {e}")
                        with self._log_lock:
                            self._log_queue.append(event)
                    finally:
                        try:
                            if cursor:
                                cursor.close()
                        except Exception:
                            pass
                        try:
                            if conn and conn.is_connected():
                                conn.close()
                        except Exception:
                            pass
        t = threading.Thread(target=worker, daemon=True)
        t.start()
