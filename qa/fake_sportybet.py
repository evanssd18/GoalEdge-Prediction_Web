"""Serve a canned SportyBet board on a local port so the UI panel can be
exercised without touching the real book.

Run from backend/:  python ../qa/fake_sportybet.py
Then point the app at it:
    SPORTYBET_MARKETS_ENABLED=true
    SPORTYBET_BASE_URL=http://127.0.0.1:8123/api
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Three markets the engine also prices, plus five it does not — so the panel's
# "display only, no model claim" separation is visible in the fixture.
MARKETS = [
    {"id": "1", "desc": "1X2", "outcomes": [
        {"desc": "Home", "odds": "1.53"}, {"desc": "Draw", "odds": "4.05"},
        {"desc": "Away", "odds": "5.84"}]},
    {"id": "18", "desc": "Total Goals Over/Under", "specifier": "total=2.5", "outcomes": [
        {"desc": "Over 2.5", "odds": "1.94"}, {"desc": "Under 2.5", "odds": "1.80"}]},
    {"id": "10", "desc": "Both Teams to Score", "outcomes": [
        {"desc": "Yes", "odds": "1.97"}, {"desc": "No", "odds": "1.77"}]},
    {"id": "11", "desc": "Double Chance", "outcomes": [
        {"desc": "Home or Draw", "odds": "1.12"}, {"desc": "Home or Away", "odds": "1.21"},
        {"desc": "Draw or Away", "odds": "2.40"}]},
    {"id": "14", "desc": "Total Goals Over/Under", "specifier": "total=1.5", "outcomes": [
        {"desc": "Over 1.5", "odds": "1.33"}, {"desc": "Under 1.5", "odds": "3.30"}]},
    {"id": "16", "desc": "Asian Handicap", "specifier": "hcp=-1", "outcomes": [
        {"desc": "Home -1", "odds": "2.05"}, {"desc": "Away +1", "odds": "1.78"}]},
    {"id": "45", "desc": "Correct Score", "outcomes": [
        {"desc": "1-0", "odds": "7.00"}, {"desc": "2-0", "odds": "9.50"},
        {"desc": "1-1", "odds": "7.50"}, {"desc": "2-1", "odds": "9.00"}]},
    {"id": "60", "desc": "Half Time / Full Time", "outcomes": [
        {"desc": "Home/Home", "odds": "2.40"}, {"desc": "Draw/Home", "odds": "4.20"}]},
]

EVENTS = [
    {"eventId": "SB9001", "homeTeam": "Roma", "awayTeam": "Parma",
     "estimateStartTime": None, "sport": {"category": {"tournament": {"name": "Italy - Serie A"}}}},
    {"eventId": "SB9002", "homeTeam": "Como 1907", "awayTeam": "Juventus",
     "estimateStartTime": None},
]


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/sport/football/events"):
            when = None
            for part in urlparse(self.path).query.split("&"):
                if part.startswith("date="):
                    when = part.split("=", 1)[1]
            events = []
            for offset, ev in enumerate(EVENTS):
                ev = dict(ev)
                # Stamp a real epoch-ms kickoff on the requested day so the
                # kickoff window in find_event_for_fixture matches.
                if when:
                    y, m, d = (int(x) for x in when.split("-"))
                    from datetime import datetime, timezone
                    ts = datetime(y, m, d, 12 + offset, 0, tzinfo=timezone.utc).timestamp()
                    ev["estimateStartTime"] = int(ts * 1000)
                    ev["startTime"] = int(ts * 1000)
                events.append(ev)
            self._send({"bCode": 10000, "data": {"tournaments": [{"events": events}]}})
        elif path.endswith("/sport/event"):
            self._send({"bCode": 10000, "data": {"event": {"markets": MARKETS}}})
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print(f"fake SportyBet on http://127.0.0.1:{port}/api")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
