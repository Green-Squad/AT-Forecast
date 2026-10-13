#!/usr/bin/env python3
"""Explicit schema/value conversion and complete generation import to libSQL.

Only operates on protected exported files and an allowlisted Rust destination.
Never opens the original source database. Staging generations remain invisible
until validation succeeds and a small transaction publishes the pointer.
"""
import argparse
import base64
import collections
import datetime as dt
import decimal
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import time
from source_snapshot import canonical, encode, quote
from turso_http import Remote, DatabaseError


def metadata_decode(v):
    if isinstance(v, dict):
        if "$array" in v: return [metadata_decode(x) for x in v["$array"]]
        if "$object" in v: return {k: metadata_decode(x) for k, x in v["$object"]}
    return v


class Unicode4:
    def __init__(self, path):
        self.weights = {int(k): v for k, v in json.loads(Path(path).read_text()).items()}

    def key(self, text):
        out = []
        for ch in text.rstrip(" "):
            cp = ord(ch)
            if cp > 0xFFFF:
                raise ValueError("Legacy utf8mb3 collation cannot represent supplementary characters")
            if cp in self.weights:
                out.extend(self.weights[cp])
            else:
                base = 0xFB40 if 0x4E00 <= cp <= 0x9FFF else 0xFB80 if 0x3400 <= cp <= 0x4DBF else 0xFBC0
                out.extend([base + (cp >> 15), (cp & 0x7FFF) | 0x8000])
        return b"".join(w.to_bytes(2, "big") for w in out)


def sqlite_value(v):
    if isinstance(v, dict):
        if "$datetime" in v:
            return v["$datetime"].replace("T", " ", 1)
        for key in ("$decimal", "$date", "$time", "$inet", "$uuid"):
            if key in v: return v[key]
        if "$binary" in v: return base64.b64decode(v["$binary"])
        if "$float" in v:
            f = float.fromhex(v["$float"])
            if not __import__('math').isfinite(f):
                raise ValueError("Nonfinite floating point needs explicit SQLite policy")
            return f
        raise ValueError("Unsupported structured SQLite value")
    if isinstance(v, bool): return int(v)
    if isinstance(v, int) and not -(1 << 63) <= v < (1 << 63):
        raise ValueError("Integer exceeds SQLite signed 64-bit range; explicit text conversion required")
    return v


def source_normalized(value, column):
    if value is None: return None
    kind = column["source_type"]
    if kind == "numeric" or kind.startswith("numeric("):
        return {"$decimal": str(value)}
    if kind in ("timestamp", "datetime", "timestamp without time zone"):
        if str(value).startswith("0000-"): return str(value)
        return {"$datetime": dt.datetime.fromisoformat(str(value)).isoformat(timespec="microseconds"), "aware": False}
    if kind == "inet": return {"$inet": str(value)}
    if kind == "boolean": return bool(value)
    return encode(value)


def physical(generation, table):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", table):
        raise ValueError("Table identifier outside supported naming policy")
    return f"_g{generation}_{table}"


class Schema:
    def __init__(self, snapshot, uca_path):
        self.snapshot = Path(snapshot)
        self.report = json.loads((self.snapshot / "report.json").read_text())
        if self.report.get("status") != "snapshot-complete":
            raise ValueError("A completed consistent source snapshot is required")
        self.engine = self.report["engine"]
        self.meta = {k: metadata_decode(v) for k, v in json.loads((self.snapshot / "schema.json").read_text()).items()}
        if self.meta["triggers"] or self.meta["views"]:
            raise ValueError("Source triggers/views require explicit conversion")
        self.columns = collections.defaultdict(list)
        self.constraints = collections.defaultdict(list)
        self.tables = [t["table"] for t in self.report["tables"]]
        for c in self.meta["columns"]:
            self.columns[c["table_name"]].append(c)
        for c in self.meta["constraints"]:
            self.constraints[c["table_name"]].append(c)
        self.uca = Unicode4(uca_path)
        self.primary = {}
        self.foreign = collections.defaultdict(list)
        self.unique = collections.defaultdict(list)
        if self.engine == "postgres":
            for table, cons in self.constraints.items():
                for c in cons:
                    definition = c["definition"]
                    if c["kind"] == "p":
                        m = re.fullmatch(r"PRIMARY KEY \(([a-zA-Z_0-9, ]+)\)", definition)
                        if not m: raise ValueError("Unsupported PostgreSQL primary key")
                        self.primary[table] = [x.strip() for x in m[1].split(",")]
                    elif c["kind"] == "f":
                        m = re.fullmatch(r"FOREIGN KEY \(([a-zA-Z_0-9]+)\) REFERENCES ([a-zA-Z_0-9]+)\(([a-zA-Z_0-9]+)\)", definition)
                        if not m: raise ValueError("Unsupported PostgreSQL foreign key")
                        self.foreign[table].append((m[1], m[2], m[3], "NO ACTION", "NO ACTION"))
                    else:
                        raise ValueError("Unsupported PostgreSQL constraint kind")
        else:
            rules = {(r["table_name"], r["name"]): r for r in self.meta["foreign_key_rules"]}
            grouped = collections.defaultdict(list)
            for c in self.meta["constraints"]:
                grouped[(c["table_name"], c["name"], c["kind"])].append(c)
            for (table, name, kind), rows in grouped.items():
                rows.sort(key=lambda c: c["position"])
                if kind == "PRIMARY KEY": self.primary[table] = [c["column_name"] for c in rows]
                elif kind == "FOREIGN KEY":
                    if len(rows) != 1: raise ValueError("Composite MySQL foreign key needs explicit conversion")
                    c = rows[0]; r = rules[(table, name)]
                    self.foreign[table].append((c["column_name"], c["referenced_table"], c["referenced_column"], r["update_rule"], r["delete_rule"]))
                elif kind == "UNIQUE": self.unique[table].append([c["column_name"] for c in rows])
                else: raise ValueError("Unsupported MySQL constraint kind")
        stable = {k:v for k,v in self.meta.items() if k not in ("tables","sequences","additional_schemas")}
        stable["tables"] = [{k:t[k] for k in ("name","engine","kind","collation") if k in t} for t in self.meta["tables"]]
        stable["sequences"] = [{k:v for k,v in seq.items() if k != "last_value"} for seq in self.meta.get("sequences",[])]
        self.fingerprint = hashlib.sha256(canonical(stable).encode()).hexdigest()

    def col(self, table, name):
        return next(c for c in self.columns[table] if c["name"] == name)

    def keyed(self, c):
        coll = c.get("collation")
        if self.engine == "mysql" and coll:
            if coll != "utf8mb3_unicode_ci": raise ValueError("Unsupported source text collation")
            return True
        if self.engine == "postgres" and coll not in (None, "default"):
            raise ValueError("Nondefault PostgreSQL collation needs explicit conversion")
        return False

    def comparison_col(self, table, name):
        return "_key_" + name if self.keyed(self.col(table, name)) else name

    def dtype(self, c):
        t = c["source_type"]
        if t in ("integer", "bigint", "smallint", "boolean") or re.fullmatch(r"(?:tinyint|smallint|mediumint|int|bigint)(?:\(\d+\))?(?: unsigned)?", t): return "INTEGER"
        if t in ("numeric", "inet", "timestamp without time zone", "timestamp", "datetime", "text", "character varying") or re.fullmatch(r"(varchar|character varying|numeric)\(\d+(,\d+)?\)", t): return "TEXT"
        raise ValueError("Unsupported source column type: " + t)

    def ddl(self, gen):
        statements = []
        for table in self.tables:
            fields = []
            pk = self.primary.get(table, [])
            inline = len(pk) == 1 and self.dtype(self.col(table, pk[0])) == "INTEGER"
            for c in self.columns[table]:
                if c.get("generated") or c.get("identity"):
                    raise ValueError("Generated PostgreSQL column needs explicit conversion")
                if c.get("extra") not in (None, "", "auto_increment"):
                    raise ValueError("Unsupported MySQL declaration")
                name = c["name"]
                nullable = c["nullable"] is True or c["nullable"] == "YES"
                field = quote(name, 'postgres') + " " + self.dtype(c)
                if inline and name == pk[0]: field += " PRIMARY KEY AUTOINCREMENT"
                if not nullable: field += " NOT NULL"
                default = c.get("default_value")
                if default is not None and not (inline and name == pk[0]):
                    default = re.sub(r"::(?:character varying|text)$", "", str(default))
                    # MySQL exposes literal text unquoted; MariaDB quotes it.
                    if self.engine == "mysql" and self.dtype(c)=="TEXT" and not default.startswith("''") and not default.startswith("'") and default != "NULL":
                        if c.get("extra") == "DEFAULT_GENERATED": raise ValueError("Generated default needs explicit conversion")
                        default = "'" + default.replace("'", "''") + "'"
                    if re.fullmatch(r"-?\d+|NULL|'(?:[^']|'')*'", default):
                        field += " DEFAULT " + default
                    else:
                        raise ValueError("Source default requires explicit SQLite conversion")
                fields.append(field)
                if self.keyed(c):
                    shadow=quote("_key_" + name,'postgres') + " BLOB" + (" NOT NULL" if not nullable else "")
                    if default is not None and str(default).startswith("'") and str(default).endswith("'"):
                        literal=str(default)[1:-1].replace("''", "'")
                        shadow += " DEFAULT X'" + self.uca.key(literal).hex() + "'"
                    fields.append(shadow)
            if table == "users" and self.engine == "mysql": fields.append('"_credential_hash" TEXT NOT NULL')
            if pk and not inline:
                fields.append("PRIMARY KEY (" + ",".join(quote(c, 'postgres') for c in pk) + ")")
            if pk and any(self.keyed(self.col(table, c)) for c in pk):
                fields.append("UNIQUE (" + ",".join(quote(self.comparison_col(table, c), 'postgres') for c in pk) + ")")
            for group in self.unique[table]:
                fields.append("UNIQUE (" + ",".join(quote(self.comparison_col(table, c), 'postgres') for c in group) + ")")
            for c, ref_table, ref_col, update, delete in self.foreign[table]:
                fields.append(f"FOREIGN KEY ({quote(self.comparison_col(table,c),'postgres')}) REFERENCES {quote(physical(gen,ref_table),'postgres')}({quote(self.comparison_col(ref_table,ref_col),'postgres')}) ON UPDATE {update} ON DELETE {delete} DEFERRABLE INITIALLY DEFERRED")
            statements.append((f"CREATE TABLE {quote(physical(gen,table),'postgres')} (" + ",".join(fields) + ")", ()))
        return statements

    def index_ddl(self, gen):
        statements = []
        if self.engine == "postgres":
            for idx in self.meta["indexes"]:
                if idx["name"].endswith("_pkey"): continue
                m = re.fullmatch(r"CREATE (UNIQUE )?INDEX ([a-zA-Z_0-9]+) ON public\.([a-zA-Z_0-9]+) USING btree \(([a-zA-Z_0-9, ]+)\)", idx["definition"])
                if not m: raise ValueError("Unsupported PostgreSQL index")
                statements.append((f"CREATE {m[1] or ''}INDEX {quote('_g'+str(gen)+'_'+m[2],'postgres')} ON {quote(physical(gen,m[3]),'postgres')} ({m[4]})", ()))
            extras = {"shelters": ["CAST(mileage AS REAL)", "state_id,CAST(mileage AS REAL)"],
                      "weathers": ["shelter_id,weather_date"], "hourly_weathers": ["shelter_id,date"], "keys": ["key"]}
        else:
            groups = collections.defaultdict(list)
            for idx in self.meta["indexes"]:
                if idx["name"] == "PRIMARY": continue
                if idx["prefix_length"] is not None or idx["index_type"] != "BTREE":
                    raise ValueError("Unsupported MySQL index declaration")
                groups[(idx["table_name"], idx["name"], idx["non_unique"])].append(idx)
            for (table, name, nonunique), rows in groups.items():
                rows.sort(key=lambda r: r["position"])
                columns = [quote(self.comparison_col(table, r["column_name"]), 'postgres') for r in rows]
                statements.append((f"CREATE {'UNIQUE ' if not nonunique else ''}INDEX {quote('_g'+str(gen)+'_'+table+'_'+name,'postgres')} ON {quote(physical(gen,table),'postgres')} ({','.join(columns)})", ()))
            extras = {"weapons": ["_key_game_id,_key_name,id"], "loadouts": ["weapon_id,updated_at,id"],
                      "loadout_user": ["loadout_id,_key_user_id", "_key_user_id,updated_at,loadout_id"],
                      "attachment_loadout": ["loadout_id,attachment_id"], "password_reminders": ["email,token,created_at"],
                      "users": ["_key_username"]}
        for table, groups in extras.items():
            if table not in self.tables: continue
            for n, group in enumerate(groups):
                statements.append((f"CREATE INDEX {quote('_g'+str(gen)+'_'+table+'_rust_'+str(n),'postgres')} ON {quote(physical(gen,table),'postgres')} ({group})", ()))
        return statements

    def insert_row(self, table, original):
        values = [sqlite_value(v) for v in original]
        names = [c["name"] for c in self.columns[table]]
        row = dict(zip(names, values))
        for c in self.columns[table]:
            if self.keyed(c):
                names.append("_key_" + c["name"])
                values.append(None if row[c["name"]] is None else self.uca.key(row[c["name"]]))
        if table == "users" and self.engine == "mysql":
            names.append("_credential_hash")
            values.append(hashlib.sha256((row["password"] + "\0" + (row["remember_token"] or "")).encode()).hexdigest())
        return names, values

    def order(self):
        pending = set(self.tables); out = []
        while pending:
            ready = sorted(t for t in pending if all(f[1] not in pending for f in self.foreign[t]))
            if not ready: raise ValueError("Cyclic references need explicit bulk import policy")
            out.extend(ready); pending.difference_update(ready)
        return out


def validate(db, schema, gen):
    results = []
    reports = {r["table"]: r for r in schema.report["tables"]}
    for table in schema.tables:
        expected = reports[table]
        count, hsum, hxor = 0, 0, 0
        names = [c["name"] for c in schema.columns[table]]
        while True:
            rows = db.query(f"SELECT {','.join(quote(n,'postgres') for n in names)} FROM {quote(physical(gen,table),'postgres')} LIMIT 1000 OFFSET ?", (count,))
            if not rows: break
            for row in rows:
                normalized = [source_normalized(row[c["name"]], c) for c in schema.columns[table]]
                h = int.from_bytes(hashlib.sha256(canonical(normalized).encode()).digest(), "big")
                hsum = (hsum + h) % (1 << 256); hxor ^= h; count += 1
        good = (count == expected["source_rows"] and f"{hsum:064x}" == expected["row_hash_sum"] and f"{hxor:064x}" == expected["row_hash_xor"])
        results.append({"table": table, "source_rows": expected["source_rows"], "destination_rows": count,
                        "normalized_checksums_match": good})
        if not good: raise ValueError("Destination count/checksum validation failed for " + table)
    violations = db.query("PRAGMA foreign_key_check")
    if violations: raise ValueError("Destination relationship validation failed")
    integrity = db.query("PRAGMA integrity_check")
    if len(integrity) != 1 or next(iter(integrity[0].values())) != "ok":
        raise ValueError("Destination integrity validation failed")
    return results


def import_generation(db, schema, gen):
    started = time.monotonic()
    db.execute("CREATE TABLE IF NOT EXISTS _migration_staging(generation INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL)")
    if db.query("SELECT name FROM sqlite_master WHERE name=?", (physical(gen, schema.tables[0]),)):
        try:
            tables = validate(db, schema, gen)
            return {"generation":gen,"engine":schema.engine,"schema_sha256":schema.fingerprint,"tables":tables,"relationships":"passed","integrity":"passed","duration_seconds":round(time.monotonic()-started,3),"source_snapshot_utc":schema.report["snapshot_started_utc"],"reused":True}
        except ValueError:
            owned=db.query("SELECT fingerprint FROM _migration_staging WHERE generation=?",(gen,))
            state_exists=db.query("SELECT name FROM sqlite_master WHERE name='_migration_state'")
            active=db.query("SELECT generation FROM _migration_state WHERE id=1") if state_exists else []
            if not owned or owned[0]["fingerprint"]!=schema.fingerprint or (active and active[0]["generation"]==gen):
                raise ValueError("Existing generation is active or not owned by this import; retained unchanged")
            for table in reversed(schema.order()):db.execute(f"DROP TABLE IF EXISTS {quote(physical(gen,table),'postgres')}")
    db.execute("INSERT INTO _migration_staging VALUES(?,?) ON CONFLICT DO NOTHING",(gen,schema.fingerprint))
    db.batch(schema.ddl(gen))
    by_table = {r["table"]: r for r in schema.report["tables"]}
    for table in schema.order():
        with gzip.open(schema.snapshot / by_table[table]["file"], "rt") as f:
            header = json.loads(next(f))
            if header["columns"] != [c["name"] for c in schema.columns[table]]:
                raise ValueError("Snapshot column order differs from live-schema manifest")
            batch, size = [], 0
            for line in f:
                names, values = schema.insert_row(table, json.loads(line))
                sql = f"INSERT INTO {quote(physical(gen,table),'postgres')} ({','.join(quote(n,'postgres') for n in names)}) VALUES ({','.join('?' for _ in values)})"
                estimate = len(line.encode()) * 4 + 512
                if batch and (len(batch) >= 150 or size + estimate > 1024 * 1024):
                    db.batch(batch); batch, size = [], 0
                batch.append((sql, values)); size += estimate
            if batch: db.batch(batch)
    db.batch(schema.index_ddl(gen))
    tables = validate(db, schema, gen)
    return {"generation": gen, "engine": schema.engine, "schema_sha256": schema.fingerprint,
            "tables": tables, "relationships": "passed", "integrity": "passed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "http_requests": db.requests, "rows_read": db.read, "rows_written": db.written,
            "source_snapshot_utc": schema.report["snapshot_started_utc"]}


def publish(db, schema, gen, validation, extra=()):
    guards=[]
    if db.query("SELECT name FROM sqlite_master WHERE name='_rust_write_epoch'"):
        epoch=db.query("SELECT value FROM _rust_write_epoch WHERE id=1")[0]["value"]
        state=db.query("SELECT generation FROM _migration_state WHERE id=1")
        if schema.engine=="mysql" and epoch and state and state[0]["generation"]!=gen:
            raise ValueError("Independent Rust writes exist; use ownership-aware sync instead of raw generation publication")
        if schema.engine=="mysql":
            guards=[
                ("CREATE TABLE IF NOT EXISTS _migration_publish_guard(id INTEGER PRIMARY KEY CHECK(id=1),ok INTEGER NOT NULL CHECK(ok=1))",()),
                ("INSERT INTO _migration_publish_guard(id,ok) SELECT 1,CASE WHEN (SELECT value FROM _rust_write_epoch WHERE id=1)>0 AND (SELECT generation FROM _migration_state WHERE id=1)<>? THEN 0 ELSE 1 END ON CONFLICT(id) DO UPDATE SET ok=excluded.ok",(gen,)),
            ]
    db.batch([
        *guards,
        ("CREATE TABLE IF NOT EXISTS _migration_state(id INTEGER PRIMARY KEY CHECK(id=1),generation INTEGER NOT NULL,schema_sha256 TEXT NOT NULL,source_snapshot_utc TEXT NOT NULL,published_utc TEXT NOT NULL,syncing INTEGER NOT NULL DEFAULT 0)", ()),
        ("CREATE TABLE IF NOT EXISTS _migration_reports(generation INTEGER PRIMARY KEY,report TEXT NOT NULL)", ()),
        ("INSERT INTO _migration_reports(generation,report) VALUES(?,?) ON CONFLICT(generation) DO UPDATE SET report=excluded.report", (gen, canonical(validation))),
        ("INSERT INTO _migration_state(id,generation,schema_sha256,source_snapshot_utc,published_utc) VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,schema_sha256=excluded.schema_sha256,source_snapshot_utc=excluded.source_snapshot_utc,published_utc=excluded.published_utc",
         (gen, schema.fingerprint, schema.report["snapshot_started_utc"], dt.datetime.now(dt.UTC).isoformat())),
        *extra,
    ])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--database", choices=["atforecast-rust", "gameloadouts-rust"], required=True)
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument("--uca-file", type=Path, default=Path(__file__).with_name("uca4-primary.json"))
    p.add_argument("--generation", type=int, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    os.umask(0o077)
    if args.token_file.stat().st_mode & 0o077: p.error("Token file must be private")
    try:
        schema = Schema(args.snapshot, args.uca_file)
        db = Remote("libsql://" + args.database + "-greensquad.aws-eu-west-1.turso.io", args.token_file.read_text())
        validation = import_generation(db, schema, args.generation)
        if args.publish: publish(db, schema, args.generation, validation)
        validation["published"] = args.publish
        args.report.write_text(json.dumps(validation, indent=2))
        print(canonical(validation))
    except (ValueError, DatabaseError) as e:
        print(str(e), file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
