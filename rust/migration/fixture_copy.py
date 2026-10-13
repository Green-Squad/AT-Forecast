#!/usr/bin/env python3
"""Copy only explicitly isolated loopback test data to a loopback libSQL server."""
import argparse
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from source_snapshot import run
from snapshot_sqlite import Schema, import_generation, publish
from sync_data import init_tracking
from turso_http import Remote

p=argparse.ArgumentParser()
p.add_argument("--source-database",required=True)
p.add_argument("--target",required=True)
p.add_argument("--engine",choices=["mysql","postgres"],default="mysql")
args=p.parse_args()
target=urlparse(args.target)
if os.environ.get("TURSO_TEST_DATABASE")!="isolated" or target.scheme!="http" or target.hostname not in ("127.0.0.1","localhost","fixture-public","fixture-accounts","at-fixture-libsql"):
    raise SystemExit("Fixture copy requires an explicitly isolated loopback destination")
allowed=("loadouts_fixture","loadouts_accounts_fixture","at_forecast_test")
if args.source_database not in allowed:raise SystemExit("Source is not a disposable fixture database")
cfg={"engine":args.engine,"host":os.environ.get("TEST_MYSQL_HOST","127.0.0.1") if args.engine=="mysql" else os.environ.get("TEST_POSTGRES_HOST","127.0.0.1"),"port":int(os.environ.get("TEST_MYSQL_PORT","3307")) if args.engine=="mysql" else 5432,
     "database":args.source_database,"user":"root" if args.engine=="mysql" else "postgres",
     "password":"" if args.engine=="mysql" else "fixture-admin-only"}
if cfg["host"] not in ("127.0.0.1","localhost","fixture-mysql","at-fixture-pg"): raise SystemExit("Fixture source must be isolated")
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix="isolated-source-") as temp:
    snapshot=Path(temp)/"snapshot"
    run(cfg,snapshot,True,lock_nontransactional=True)
    schema=Schema(snapshot,Path(__file__).with_name("uca4-primary.json"))
    # New container per fixture. Never drop a pre-existing destination dataset.
    db=Remote(args.target,"")
    if db.query("SELECT name FROM sqlite_master WHERE type='table'"):
        raise SystemExit("Fixture destination must be empty")
    result=import_generation(db,schema,1)
    publish(db,schema,1,result)
    init_tracking(db,schema)
    print(json.dumps({"fixture":args.source_database,"tables":result["tables"],"relationships":result["relationships"]}))
