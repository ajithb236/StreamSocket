CREATE DATABASE IF NOT EXISTS remote_stream;
USE remote_stream;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    username VARCHAR(255),
    ip_addr VARCHAR(45),
    message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert a test user (password: "password123")
-- INSERT INTO users (username, password_hash) VALUES ('testuser', '$2b$12$D8B0sT5XwK/X7y70sC2K9uzc8T2D0oK30b.z9O1Zq8b/1KzHzR5e6');
