"""Local development server for HeatShield API emulating AWS Lambda HttpApi."""
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qsl

# Ensure backend directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import handler

class LambdaHttpHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        body = None
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body = self.rfile.read(content_length).decode("utf-8")

        event = {
            "rawPath": path,
            "path": path,
            "queryStringParameters": dict(parse_qsl(parsed.query)),
            "requestContext": {
                "http": {
                    "method": method,
                    "path": path,
                }
            },
            "httpMethod": method,
            "body": body,
            "headers": {k.lower(): v for k, v in self.headers.items()}
        }

        try:
            response = handler(event, None)
            status_code = response.get("statusCode", 200)
            headers = response.get("headers", {})
            body_out = response.get("body", "")

            self.send_response(status_code)
            for k, v in headers.items():
                self.send_header(k, v)
            if "Access-Control-Allow-Origin" not in headers:
                self._send_cors_headers()
            self.end_headers()

            if isinstance(body_out, str):
                self.wfile.write(body_out.encode("utf-8"))
            elif isinstance(body_out, bytes):
                self.wfile.write(body_out)
            else:
                self.wfile.write(json.dumps(body_out).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

def run(port=3000):
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, LambdaHttpHandler)
    print(f"HeatShield API running locally on http://127.0.0.1:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    run(port)
