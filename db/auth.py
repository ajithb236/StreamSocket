import mysql.connector
import bcrypt
import os
from dotenv import load_dotenv

load_dotenv()

class DatabaseAdapter:
    def __init__(self):
        self.config = {
            'host': os.environ.get('DB_HOST', 'localhost'),
            'port': int(os.environ.get('DB_PORT', 3306)),
            'user': os.environ.get('DB_USER', 'root'),
            'password': os.environ.get('DB_PASSWORD', 'Admin'),
            'database': os.environ.get('DB_NAME', 'remote_stream')
        }
        
    def get_connection(self):
        return mysql.connector.connect(**self.config)
        
    def authenticate_user(self, username, password):
        # Check user credentials and log the attempt
        try:
            conn = self.get_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            
            if user:
                # Bcrypt verification requires bytes
                is_valid = bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8'))
                self.log_event("AUTH_SUCCESS" if is_valid else "AUTH_FAILURE", username=username)
                return is_valid
                
            self.log_event("AUTH_FAILURE_NO_USER", username=username)
            return False
            
        except mysql.connector.Error as err:
            print(f"Database error: {err}")
            return False
        finally:
            if 'conn' in locals() and conn.is_connected():
                cursor.close()
                conn.close()
                
    def log_event(self, event_type, username=None, ip_addr=None, message=None):
        # Log an event to the database
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            query = "INSERT INTO logs (event_type, username, ip_addr, message) VALUES (%s, %s, %s, %s)"
            cursor.execute(query, (event_type, username, ip_addr, message))
            conn.commit()
            cursor.close()
            conn.close()
        except mysql.connector.Error as err:
            print(f"Logging Error: {err}")
