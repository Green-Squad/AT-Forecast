#!/usr/bin/env python3
"""Read-only source inspection/export. No source DDL/DML or destination access.

Snapshots deliberately fail for nontransactional MySQL tables; an InnoDB
transaction cannot promise consistency for MyISAM. Never quietly skip a table.
Credentials come from a protected JSON file, never command-line values.
"""
import argparse
import base64
import datetime as dt
import decimal
import gzip
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid


def encode(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return {"$decimal": str(v)}
    if isinstance(v, dt.datetime):
        return {"$datetime": v.isoformat(timespec="microseconds"), "aware": v.tzinfo is not None}
    if isinstance(v, dt.date):
        return {"$date": v.isoformat()}
    if isinstance(v, dt.time):
        return {"$time": v.isoformat(timespec="microseconds")}
    if isinstance(v, dt.timedelta):
        return {"$duration_microseconds": (v.days * 86400 + v.seconds) * 1000000 + v.microseconds}
    if isinstance(v, (bytes, memoryview, bytearray)):
        return {"$binary": base64.b64encode(bytes(v)).decode("ascii")}
    if isinstance(v, uuid.UUID):
        return {"$uuid": str(v)}
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address,
                      ipaddress.IPv4Interface, ipaddress.IPv6Interface,
                      ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return {"$inet": str(v)}
    if isinstance(v, float):
        return {"$float": v.hex() if math.isfinite(v) else str(v)}
    if isinstance(v, (tuple, list)):
        return {"$array": [encode(x) for x in v]}
    if isinstance(v, dict):
        return {"$object": [[k, encode(x)] for k, x in sorted(v.items())]}
    raise ValueError("Unsupported source value type: " + type(v).__name__)


def canonical(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def quote(name, engine):
    q = '`' if engine == 'mysql' else '"'
    return q + name.replace(q, q + q) + q


def private_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(text)


def query(conn, sql, args=()):
    with conn.cursor() as c:
        if args:
            c.execute(sql, args)
        else:
            c.execute(sql)
        keys = [d[0] for d in c.description]
        return [dict(zip(keys, row)) for row in c.fetchall()]


def connect(cfg):
    engine = cfg["engine"]
    opts = {k: cfg[k] for k in ("host", "port", "user", "password") if k in cfg}
    if engine == "mysql":
        import pymysql
        return pymysql.connect(**opts, database=cfg["database"], charset="utf8mb4",
                               connect_timeout=5, read_timeout=60, write_timeout=10,
                               autocommit=False)
    if engine == "postgres":
        import psycopg
        opts["dbname"] = cfg["database"]
        opts["connect_timeout"] = 5
        opts["options"] = "-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=1000 -c timezone=UTC"
        return psycopg.connect(**opts)
    raise ValueError("engine must be mysql or postgres")


def metadata(conn, cfg):
    if cfg["engine"] == "mysql":
        db = cfg["database"]
        return {
            "tables": query(conn, "SELECT TABLE_NAME AS name, ENGINE AS engine, TABLE_TYPE AS kind, TABLE_COLLATION AS collation, DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (db,)),
            "columns": query(conn, "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, ORDINAL_POSITION AS position, COLUMN_TYPE AS source_type, IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value, EXTRA AS extra, CHARACTER_SET_NAME AS charset, COLLATION_NAME AS collation FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION", (db,)),
            "indexes": query(conn, "SELECT TABLE_NAME AS table_name, INDEX_NAME AS name, NON_UNIQUE AS non_unique, SEQ_IN_INDEX AS position, COLUMN_NAME AS column_name, SUB_PART AS prefix_length, COLLATION AS direction, INDEX_TYPE AS index_type FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX", (db,)),
            "constraints": query(conn, "SELECT tc.TABLE_NAME AS table_name,tc.CONSTRAINT_NAME AS name,tc.CONSTRAINT_TYPE AS kind,k.COLUMN_NAME AS column_name,k.ORDINAL_POSITION AS position,k.REFERENCED_TABLE_NAME AS referenced_table,k.REFERENCED_COLUMN_NAME AS referenced_column FROM information_schema.TABLE_CONSTRAINTS tc LEFT JOIN information_schema.KEY_COLUMN_USAGE k ON k.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND k.TABLE_NAME=tc.TABLE_NAME AND k.CONSTRAINT_NAME=tc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA=%s ORDER BY tc.TABLE_NAME,tc.CONSTRAINT_NAME,k.ORDINAL_POSITION", (db,)),
            "foreign_key_rules": query(conn, "SELECT TABLE_NAME AS table_name,CONSTRAINT_NAME AS name,UPDATE_RULE AS update_rule,DELETE_RULE AS delete_rule FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=%s", (db,)),
            "triggers": query(conn, "SELECT TRIGGER_NAME AS name,EVENT_OBJECT_TABLE AS table_name,ACTION_TIMING AS timing,EVENT_MANIPULATION AS event,ACTION_STATEMENT AS definition FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s", (db,)),
            "views": query(conn, "SELECT TABLE_NAME AS name,VIEW_DEFINITION AS definition FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s", (db,)),
        }
    return {
        "additional_schemas": query(conn, "SELECT n.nspname AS schema_name,c.relname AS table_name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname<>'public' AND n.nspname<>'information_schema' AND n.nspname NOT LIKE 'pg_%' AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname"),
        "tables": query(conn, "SELECT c.relname AS name,'postgres' AS engine,CASE c.relkind WHEN 'r' THEN 'BASE TABLE' WHEN 'p' THEN 'PARTITIONED TABLE' ELSE c.relkind::text END AS kind,pg_total_relation_size(c.oid) AS total_bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition ORDER BY c.relname"),
        "columns": query(conn, "SELECT c.relname AS table_name,a.attname AS name,a.attnum AS position,format_type(a.atttypid,a.atttypmod) AS source_type,NOT a.attnotnull AS nullable,pg_get_expr(d.adbin,d.adrelid) AS default_value,a.attidentity AS identity,a.attgenerated AS generated,coll.collname AS collation FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum LEFT JOIN pg_collation coll ON coll.oid=a.attcollation WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT c.relispartition AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"),
        "indexes": query(conn, "SELECT tablename AS table_name,indexname AS name,indexdef AS definition FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"),
        "constraints": query(conn, "SELECT r.relname AS table_name,c.conname AS name,c.contype AS kind,pg_get_constraintdef(c.oid) AS definition,c.convalidated AS validated FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' ORDER BY r.relname,c.conname"),
        "sequences": query(conn, "SELECT sequencename AS name,data_type,increment_by,min_value,max_value,start_value,cycle,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"),
        "triggers": query(conn, "SELECT c.relname AS table_name,t.tgname AS name,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal"),
        "views": query(conn, "SELECT viewname AS name,definition FROM pg_views WHERE schemaname='public'"),
        "enums": query(conn, "SELECT t.typname AS name,e.enumlabel AS value,e.enumsortorder AS position FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' ORDER BY t.typname,e.enumsortorder"),
    }


def run(cfg, destination, export, lock_nontransactional=False):
    started = time.monotonic()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    engine = cfg["engine"]
    conn = connect(cfg)
    lock_conn = None
    lock_started = None
    try:
        if engine == "mysql":
            with conn.cursor() as c:
                c.execute("SELECT VERSION()")
                version = c.fetchone()[0]
                c.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                c.execute("SET SESSION TRANSACTION READ ONLY")
                c.execute("SET SESSION time_zone = '+00:00'")
                c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                c.execute("SELECT @@tx_read_only" if "MariaDB" in version else "SELECT @@transaction_read_only")
                if c.fetchone()[0] != 1:
                    raise ValueError("Read-only transaction guard failed")
        else:
            with conn.cursor() as c:
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                c.execute("SHOW transaction_read_only")
                if c.fetchone()[0] != "on":
                    raise ValueError("Read-only transaction guard failed")
        manifest = metadata(conn, cfg)
        if export and manifest.get("additional_schemas"):
            raise ValueError("Additional application schemas require explicit qualified-table export")
        tables = [t for t in manifest["tables"] if t["kind"] in ("BASE TABLE", "PARTITIONED TABLE")]
        bad = [t["name"] for t in tables if engine == "mysql" and t["engine"] != "InnoDB"]
        private_file(destination / "schema.json", canonical({k: encode(v) for k, v in manifest.items()}))
        if export and bad:
            if not lock_nontransactional:
                raise ValueError("Consistent snapshot requires --lock-nontransactional for small MyISAM tables: " + ", ".join(bad))
            if any(t["engine"] != "MyISAM" for t in tables if t["name"] in bad):
                raise ValueError("Unsupported nontransactional engine")
            for name in bad:
                if query(conn, "SELECT COUNT(*) AS count FROM " + quote(name, engine))[0]["count"] > 1000:
                    raise ValueError("Nontransactional table exceeds brief-lock row budget")
            conn.rollback()
            lock_conn = connect(cfg)
            with lock_conn.cursor() as c:
                c.execute("SET SESSION lock_wait_timeout=2")
                # Separate connection retains MyISAM read locks while the primary
                # connection establishes its InnoDB MVCC snapshot. No global lock.
                c.execute("LOCK TABLES " + ",".join(quote(n, engine) + " READ" for n in bad))
            lock_started = time.monotonic()
            with conn.cursor() as c:
                c.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            tables.sort(key=lambda t: (t["name"] not in bad, t["name"]))
        report = {"engine": engine, "snapshot_started_utc": dt.datetime.now(dt.UTC).isoformat(),
                  "source_read_only": True, "nontransactional_tables": bad, "tables": []}
        for t in tables:
            name = t["name"]
            if lock_conn is not None and time.monotonic() - lock_started > 5:
                raise ValueError("Brief-lock time budget exceeded; snapshot abandoned")
            ident = quote(name, engine)
            if not export:
                count = query(conn, "SELECT COUNT(*) AS count FROM " + ident)[0]["count"]
                report["tables"].append({"table": name, "source_rows": count, "engine": t["engine"]})
                continue
            # Named PostgreSQL cursor / MySQL SSCursor keep record memory bounded.
            if engine == "postgres":
                cur = conn.cursor(name="export_" + uuid.uuid4().hex)
            else:
                import pymysql
                cur = conn.cursor(pymysql.cursors.SSCursor)
            count, total_hash, xor_hash = 0, 0, 0
            filename = hashlib.sha256(name.encode()).hexdigest() + ".jsonl.gz"
            fd = os.open(destination / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with cur, os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
                cur.execute("SELECT * FROM " + ident)
                columns = [d[0] for d in cur.description]
                out.write((canonical({"table": name, "columns": columns}) + "\n").encode())
                while True:
                    batch = cur.fetchmany(1000)
                    if not batch:
                        break
                    for row in batch:
                        text = canonical([encode(v) for v in row])
                        h = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
                        total_hash = (total_hash + h) % (1 << 256)
                        xor_hash ^= h
                        out.write((text + "\n").encode())
                        count += 1
            report["tables"].append({"table": name, "source_rows": count, "file": filename,
                                     "row_hash_sum": f"{total_hash:064x}", "row_hash_xor": f"{xor_hash:064x}",
                                     "compressed_bytes": (destination / filename).stat().st_size})
            if lock_conn is not None and all(n in {r["table"] for r in report["tables"]} for n in bad):
                with lock_conn.cursor() as c:
                    c.execute("UNLOCK TABLES")
                report["nontransactional_read_lock_seconds"] = round(time.monotonic() - lock_started, 3)
                lock_conn.close()
                lock_conn = None
        conn.rollback()  # Explicitly end the read-only snapshot; never commit to source.
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["exported"] = export
        report["status"] = "snapshot-complete" if export else "inspection-complete"
        private_file(destination / "report.json", canonical(report))
        # Counts and freshness only. Schema, connection details and rows stay private.
        print(canonical(report))
        return report
    finally:
        if lock_conn is not None:
            # Closing releases only the scoped read locks, including failure paths.
            lock_conn.close()
        conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--export", action="store_true")
    p.add_argument("--lock-nontransactional", action="store_true",
                   help="Brief read locks for <=1000-row MyISAM tables only; no global lock")
    args = p.parse_args()
    if args.config.stat().st_mode & 0o077:
        p.error("Config must not be accessible to group or other users")
    os.umask(0o077)
    try:
        run(json.loads(args.config.read_text()), args.destination, args.export, args.lock_nontransactional)
    except Exception as e:
        # Driver errors may contain credentials or row values: withhold raw errors.
        if isinstance(e, ValueError):
            print(str(e), file=sys.stderr)
        else:
            print("Source operation failed: " + type(e).__name__ + "; details withheld", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
