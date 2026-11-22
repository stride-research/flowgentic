"""An OTLP/HTTP endpoint that accepts every export and keeps nothing, for
measuring what streaming costs the agents' process without a collector:

    python benchmarks/otlp_sink.py --port 4318 &
    python benchmarks/agent_loop.py --agents 5000 --otlp http://127.0.0.1:4318

It answers each POST as a collector does, with an empty protobuf reply, and
prints the requests and bytes it took for each path when it stops (Ctrl-C or
SIGTERM). It listens on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

requests: Counter[str] = Counter()
received: Counter[str] = Counter()


class Sink(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as a collector

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", 0))
        self.rfile.read(size)
        requests[self.path] += 1
        received[self.path] += size
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")  # an empty Export*ServiceResponse
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=4318)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Sink)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.server_close()
        print(json.dumps({"requests": dict(requests), "bytes": dict(received)}), flush=True)


if __name__ == "__main__":
    main()
