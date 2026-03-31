import os
import bcrypt
import mysql.connector
from dotenv import load_dotenv

 # Ensure .env is found
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(sys_path, '.env')
load_dotenv(dotenv_path=env_path)

def setup_database():
    
        print("[*] Connecting to MySQL Server...")
        conn = mysql.connector.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            port=int(os.environ.get('DB_PORT', 3306)),
            user=os.environ.get('DB_USER', 'root'),
            password=os.environ.get('DB_PASSWORD', 'Admin')
        )
        cursor = conn.cursor()

        db_name = os.environ.get('DB_NAME', 'remote_stream')

        print(f"[*] Creating database '{db_name}' if it doesn't exist...")
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        cursor.execute(f"USE {db_name}")

        print("[*] Creating 'users' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL
            )
        """)

        print("[*] Creating 'logs' table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(50) NOT NULL,
                username VARCHAR(255),
                ip_addr VARCHAR(45),
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seed default user
        test_user = os.environ.get("STREAM_USER", "admin")
        test_pass = os.environ.get("STREAM_PASSWORD", "admin123")

        cursor.execute("SELECT id FROM users WHERE username = %s", (test_user,))
        if not cursor.fetchone():
            print(f"[*] Seeding default user: Username='{test_user}' | Password='{test_pass}'")
            
            # Generate bcrypt hash
            try:
                print("[INFO] Connecting to MySQL Server...")
                conn = mysql.connector.connect(
                    host=os.environ.get('DB_HOST', 'localhost'),
                    port=int(os.environ.get('DB_PORT', 3306)),
                    user=os.environ.get('DB_USER', 'root'),
                    password=os.environ.get('DB_PASSWORD', 'Admin')
                )
                cursor = conn.cursor()

                db_name = os.environ.get('DB_NAME', 'remote_stream')

                print(f"[INFO] Creating database '{db_name}' if it doesn't exist...")
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
                cursor.execute(f"USE {db_name}")

                print("[INFO] Creating 'users' table...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(255) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL
                    )
                """)

                print("[INFO] Creating 'logs' table...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        event_type VARCHAR(50) NOT NULL,
                        username VARCHAR(255),
                        ip_addr VARCHAR(45),
                        message TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Seed default user
                test_user = os.environ.get("STREAM_USER", "admin")
                test_pass = os.environ.get("STREAM_PASSWORD", "admin123")

                cursor.execute("SELECT id FROM users WHERE username = %s", (test_user,))
                if not cursor.fetchone():
                    print(f"[INFO] Seeding default user: Username='{test_user}' | Password='{test_pass}'")
                    # Generate bcrypt hash
                    salt = bcrypt.gensalt()
                    hashed = bcrypt.hashpw(test_pass.encode('utf-8'), salt).decode('utf-8')
                    cursor.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (test_user, hashed))
                    conn.commit()
                    print("[INFO] Default user seeded successfully into the database.")
                else:
                    print(f"[INFO] Default user '{test_user}' already exists in the database. Skipping injection.")

                print("[INFO] Database setup complete! You can now start the TCP server.")
            except Exception as err:
                print(f"[ERROR] MySQL Database Error: {err}")
                print("[ERROR] Please ensure your MySQL server is running and the credentials in .env are correct.")
