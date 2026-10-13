"""Bounded Hrana v2 HTTP access, for private migration/sync tooling only."""
import base64
import json
import time
import requests


class DatabaseError(Exception):
    """Withholds server error messages, SQL and values from logs."""


def value(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, (bool, int)):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    if isinstance(v, str):
        return {"type": "text", "value": v}
    raise TypeError("Unsupported destination parameter type")


def decode(v):
    if v["type"] == "null": return None
    if v["type"] == "integer": return int(v["value"])
    if v["type"] == "float": return float(v["value"])
    if v["type"] == "blob":
        encoded = v["base64"]
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    if v["type"] == "text": return v["value"]
    raise DatabaseError("Unsupported destination value type")


def statement(sql, args=(), rows=True):
    return {"sql": sql, "args": [value(v) for v in args], "want_rows": rows}


class Remote:
    def __init__(self, url, token):
        self.url = url.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + token.strip()})
        self.baton = None
        self.requests = self.read = self.written = 0
        self.duration = 0

    def pipeline(self, operations, close=True):
        started = time.monotonic()
        req = {"requests": operations + ([{"type": "close"}] if close else [])}
        if self.baton is not None:
            req["baton"] = self.baton
        payload = json.dumps(req, separators=(",", ":"))
        if len(payload.encode()) > 2 * 1024 * 1024:
            raise DatabaseError("Request exceeds 2 MiB payload budget")
        # Never automatically replay an uncertain write. The sync generation /
        # operation identifier must be checked by its caller before a retry.
        try:
            resp = self.session.post(self.url, data=payload, headers={"Content-Type": "application/json"}, timeout=(3, 20))
            self.requests += 1
            if resp.status_code != 200:
                raise DatabaseError("Destination HTTP status " + str(resp.status_code))
            result = resp.json()
            self.baton = result.get("baton")
            out = []
            for item in result["results"]:
                if item["type"] == "error":
                    raise DatabaseError("Destination request rejected: " + item["error"].get("code", "UNKNOWN"))
                response = item["response"]
                if response["type"] == "execute":
                    row = response["result"]
                    self.read += row.get("rows_read", 0)
                    self.written += row.get("rows_written", row.get("affected_row_count", 0))
                    out.append(row)
                elif response["type"] == "batch":
                    for error in response["result"]["step_errors"]:
                        if error:
                            raise DatabaseError("Destination batch rejected: " + error.get("code", "UNKNOWN"))
                    for row in response["result"]["step_results"]:
                        if row is not None:
                            self.read += row.get("rows_read", 0)
                            self.written += row.get("rows_written", row.get("affected_row_count", 0))
                    out.extend(response["result"]["step_results"])
            return out
        except (requests.RequestException, ValueError, KeyError) as e:
            raise DatabaseError("Destination transport/protocol failure: " + type(e).__name__) from None
        finally:
            self.duration += time.monotonic() - started

    def execute(self, sql, args=(), close=True):
        return self.pipeline([{"type": "execute", "stmt": statement(sql, args)}], close=close)[0]

    def query(self, sql, args=(), close=True):
        result = self.execute(sql, args, close=close)
        names = [c["name"] for c in result["cols"]]
        return [dict(zip(names, (decode(v) for v in row))) for row in result["rows"]]

    def batch(self, statements, atomic=True, close=True):
        statements = list(statements)
        if len(statements) > 250:
            raise DatabaseError("Batch exceeds statement-count budget")
        if atomic:
            statements = [("PRAGMA foreign_keys=ON", ()), ("BEGIN IMMEDIATE", ())] + statements + [("COMMIT", ())]
        steps = []
        for i, (sql, args) in enumerate(statements):
            condition = {"type": "ok", "step": i - 1} if i else None
            step = {"stmt": statement(sql, args, rows=False)}
            if condition: step["condition"] = condition
            steps.append(step)
        result = self.pipeline([{"type": "batch", "batch": {"steps": steps}}], close=close)
        return result[2:-1] if atomic else result
