"""TCP byte stream adapter; EOF fails immediately instead of spinning until timeout."""
import socket

class TcpTransport:
    def __init__(self, connection):
        self.connection = connection
        self.connection.settimeout(0.1)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def read(self, size):
        try:
            data = self.connection.recv(size)
        except socket.timeout:
            return b""
        if not data:
            raise ConnectionError("Transmitter closed the TCP connection")
        return data

    def write(self, data):
        # Bound sendall separately; reads retain a short poll for protocol deadlines.
        self.connection.settimeout(2)
        try:
            self.connection.sendall(data)
        finally:
            self.connection.settimeout(0.1)
        return len(data)
