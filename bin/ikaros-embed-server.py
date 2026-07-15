#!/usr/bin/env python3
"""Temporary embedding server replacing llama-server :8587.

Uses chromadb DefaultEmbeddingFunction (ONNX MiniLM L6 v2, 384-dim).
Provides the same /embedding API as llama-server for Ikaros memory search.

NOTE: 384-dim vs nomic-embed-text 768-dim — existing chroma vectors
are incompatible. FTS5 search still works; vector search needs rebuild
after switching back to llama-server.
"""
import json
import sys
import os
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="[embed-srv] %(message)s")
log = logging.getLogger("embed-srv")

# Init embedding function
_ef = None
def _get_ef():
    global _ef
    if _ef is None:
        from chromadb.utils import embedding_functions
        _ef = embedding_functions.DefaultEmbeddingFunction()
        log.info("DefaultEmbeddingFunction (ONNX MiniLM L6 v2, 384-dim) loaded")
    return _ef

class EmbedHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/embedding", "/v1/embeddings"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            req = json.loads(body)
            # Ikaros sends {"content": "text"}, OpenAI sends {"input": "text"}
            text = req.get("content") or req.get("input") or ""
            if isinstance(text, list):
                text = text[0] if text else ""
            text = text[:500]  # match Ikaros truncation

            ef = _get_ef()
            vec = ef([text])[0]  # list[float32], 384-dim
            vec = [float(x) for x in vec]  # convert float32 -> float for JSON

            # Return in llama-server format: [{"index":0,"embedding":[[...]]}]
            resp = [{"index": 0, "embedding": [vec]}]

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        except Exception as e:
            log.error("embedding error: %s", e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        elif self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"object":"list","data":[{"id":"onnx-minilm-l6-v2","object":"model"}]}')
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.client_address[0], fmt % args)

if __name__ == "__main__":
    port = int(os.environ.get("IKAROS_PORT_EMBEDDING", "8587"))
    # Warm up the model
    log.info("Warming up ONNX MiniLM model...")
    _get_ef()
    log.info("Model ready, starting server on :%d", port)
    server = HTTPServer(("127.0.0.1", port), EmbedHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()
