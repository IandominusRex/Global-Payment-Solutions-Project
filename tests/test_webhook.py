import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

from treasury.simulator.sinks.webhook import build_notifications, post_notifications


def _frames():
    payments = pd.DataFrame([{"payment_id": "P1", "end_to_end_id": "E2E1", "account_id": "A001",
                              "direction": "IN", "amount": 100.0, "currency_code": "SGD"}])
    events = pd.DataFrame([
        {"event_id": 1, "payment_id": "P1", "event_ts": pd.Timestamp("2026-10-01 01:00"), "status": "CREATED",
         "reason_code": None},
        {"event_id": 2, "payment_id": "P1", "event_ts": pd.Timestamp("2026-10-01 02:00"), "status": "SETTLED",
         "reason_code": None},
    ])
    return events, payments


def test_only_booking_events_become_notifications():
    events, payments = _frames()
    batch = build_notifications(events, payments)
    assert [n["notification"] for n in batch] == ["BOOKED"]
    assert batch[0]["credit_debit"] == "CRDT" and batch[0]["message_type"] == "camt.054"


def test_notifications_are_posted():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    events, payments = _frames()
    sent = post_notifications(f"http://127.0.0.1:{server.server_port}/hook", events, payments)
    server.server_close()
    assert sent == 1 and received[0][0]["payment_id"] == "P1"
