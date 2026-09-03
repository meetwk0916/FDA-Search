from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .search import index_status, search_documents

STATIC_DIR = Path(__file__).with_name("static")


class SearchHandler(BaseHTTPRequestHandler):
    database = Path("data/fda483.sqlite3")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            self.handle_search(parse_qs(parsed.query))
            return
        if parsed.path == "/api/status":
            self.send_json(index_status(self.database))
            return
        self.serve_static(parsed.path)

    def handle_search(self, params: dict[str, list[str]]) -> None:
        try:
            payload = search_documents(
                self.database,
                params.get("q", [""])[0],
                state=params.get("state", [""])[0],
                year=params.get("year", [""])[0],
                record_type=params.get("record_type", [""])[0],
                limit=int(params.get("limit", ["20"])[0]),
                offset=int(params.get("offset", ["0"])[0]),
            )
            self.send_json(payload)
        except ValueError:
            self.send_json(
                {"error": "limit and offset must be integers"},
                HTTPStatus.BAD_REQUEST,
            )

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in candidate.parents or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the FDA global search app")
    parser.add_argument("--database", default="data/fda483.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    SearchHandler.database = Path(args.database)
    server = ThreadingHTTPServer((args.host, args.port), SearchHandler)
    print(f"FDA global search: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
