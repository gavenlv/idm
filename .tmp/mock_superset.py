"""Superset Mock Server: 给 parse_superset_dashboard Skill 提供确定性测试源."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {
    "dashboards": [
        {
            "id": 1, "dashboard_title": "用户增长", "slug": "user_growth",
            "description": "用户增长 dashboard",
            "json_metadata": json.dumps({"chartId": [10, 11]}),
            "owner_id": 100, "published": True,
        },
        {
            "id": 2, "dashboard_title": "订单概览", "slug": "orders",
            "description": "订单概览",
            "json_metadata": json.dumps({"chartId": [11, 12]}),
            "owner_id": 101, "published": True,
        },
    ],
    "charts": {
        10: {"id": 10, "slice_name": "DAU 折线", "viz_type": "line",
             "datasource_id": 100, "datasource_type": "dataset", "description": "DAU 折线图"},
        11: {"id": 11, "slice_name": "订单数柱状", "viz_type": "bar",
             "datasource_id": 101, "datasource_type": "dataset", "description": "订单数柱状图"},
        12: {"id": 12, "slice_name": "GMV 总数", "viz_type": "big_number",
             "datasource_id": 101, "datasource_type": "dataset", "description": "GMV KPI"},
    },
    "datasets": {
        100: {"id": 100, "table_name": "fct_user_daily",
              "schema": "shop", "description": "用户日活事实表",
              "database": {"database_name": "shop_dw", "name": "shop_dw"}},
        101: {"id": 101, "table_name": "fct_orders_daily",
              "schema": "shop", "description": "订单日聚合",
              "database": {"database_name": "shop_dw", "name": "shop_dw"}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence

    def _ok(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "session=mock")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_POST(self):
        body = self._read_body()
        if "/security/login" in self.path:
            self._ok({"access_token": "mock-token"})
        else:
            self._ok({"result": "ok"})

    def do_GET(self):
        u = urlparse(self.path)
        if "/security/csrf_token" in u.path:
            self._ok({"result": "csrf-mock"})
            return
        parts = [p for p in u.path.split("/") if p]
        # /api/v1/dashboard/
        if u.path == "/api/v1/dashboard/":
            self._ok({"result": STATE["dashboards"]})
            return
        if u.path.startswith("/api/v1/dashboard/") and parts[-1].isdigit():
            d = next((x for x in STATE["dashboards"] if x["id"] == int(parts[-1])), None)
            if d is None:
                d = STATE["dashboards"][0]
            self._ok({"result": d})
            return
        if u.path == "/api/v1/chart/":
            self._ok({"result": list(STATE["charts"].values())})
            return
        if u.path.startswith("/api/v1/chart/") and parts[-1].isdigit():
            self._ok({"result": STATE["charts"].get(int(parts[-1]), {})})
            return
        if u.path == "/api/v1/dataset/":
            self._ok({"result": list(STATE["datasets"].values())})
            return
        if u.path.startswith("/api/v1/dataset/") and parts[-1].isdigit():
            self._ok({"result": STATE["datasets"].get(int(parts[-1]), {})})
            return
        self._ok({"result": []})


def start(port: int = 9088):
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", 9088))
    start(port)
    print(f"mock superset on :{port}")
    import time
    while True:
        time.sleep(60)
