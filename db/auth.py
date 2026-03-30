
import mysql.connector
import mysql.connector.pooling
import bcrypt
import os
import threading
from dotenv import load_dotenv
import time

load_dotenv()

class DatabaseAdapter:
    def __init__(self, pool_size=16):
        self.config = {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'port': int(os.environ.get('DB_PORT', 3306)),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', 'Admin'),
            'database': os.environ.get('DB_NAME', 'remote_stream')
        }
        self.pool_size = pool_size
        self._init_pool()
        self._auth_cache = {}  # username: password_hash
        self._cache_lock = threading.Lock()
        self._log_queue = []
        self._log_lock = threading.Lock()
        self._start_log_worker()

    def _init_pool(self):
        try:
            self.pool = mysql.connector.pooling.MySQLConnectionPool(pool_name="mypool", pool_size=self.pool_size, **self.config)
            self.db_available = True
        except Exception as e:
            print(f"[DB] Connection pool init failed: {e}")
            self.pool = None
            self.db_available = False

    def get_connection(self):
        if self.pool:
            try:
                return self.pool.get_connection()
            except Exception as e:
                print(f"[DB] Pool get_connection failed: {e}")
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
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def log_event(self, event_type, username=None, ip_addr=None, message=None):
        # Try to log to DB, else queue for retry
        try:
            conn = self.get_connection()
            if conn is None:
                raise Exception("DB unavailable")
            cursor = conn.cursor()
            query = "INSERT INTO logs (event_type, username, ip_addr, message) VALUES (%s, %s, %s, %s)"
            cursor.execute(query, (event_type, username, ip_addr, message))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as err:
            print(f"[DB] log_event error: {err}. Queuing log.")
            with self._log_lock:
                self._log_queue.append((event_type, username, ip_addr, message, time.time()))

    def _start_log_worker(self):
        def worker():
            while True:
                time.sleep(2)
                if not self._log_queue:
                    continue
                with self._log_lock:
                    queue_copy = self._log_queue[:]
                    self._log_queue.clear()
                for event in queue_copy:
                    try:
                        self.log_event(event[0], event[1], event[2], event[3])
                    except Exception as e:
                        print(f"[DB] log_worker failed to flush log: {e}")
                        with self._log_lock:
                            self._log_queue.append(event)
        t = threading.Thread(target=worker, daemon=True)
        t.start()
