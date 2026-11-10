"""假的工作台实例接口（只给 tests/e2e/model_config_openclaw.py 用）：model-config、回报、取 Key 与控制接口。"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"target": None, "reports": [], "keys": {}, "vault": {}, "resolves": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/agent-api/v1/model-config":
            if STATE["target"] is None:
                return self._json(409, {"error": {"code": "MODEL_CONFIGURATION_INCOMPLETE", "details": {"reason": "no_binding"}}})
            return self._json(200, STATE["target"])
        if self.path == "/control/state":
            return self._json(200, STATE)
        self._json(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/agent-api/v1/model-config/applied":
            STATE["reports"].append(body)
            if STATE["target"] and STATE["target"]["desired_revision"] == body["desired_revision"]:
                STATE["target"]["job_state"] = "succeeded" if body["result"] == "applied" else body["result"]
            return self._json(200, {"apply_state": body["result"], "desired_revision": body["desired_revision"], "applied_revision": None})
        if self.path in ("/agent-api/v1/model-credentials/resolve", "/agent-api/v1/secrets/resolve"):
            store = STATE["keys"] if "model-credentials" in self.path else STATE["vault"]
            STATE["resolves"].append([self.path.rsplit("/", 2)[-2], body.get("ids")])
            ids = body.get("ids") or []
            return self._json(200, {"values": {i: store[i] for i in ids if i in store},
                                    "errors": {i: {"code": "NOT_FOUND"} for i in ids if i not in store}})
        if self.path == "/control/set":
            STATE.update(body)
            return self._json(200, {})
        self._json(404, {})


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
