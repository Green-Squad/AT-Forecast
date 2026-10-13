#!/usr/bin/env python3
"""Source snapshot validation and destination-only three-way synchronization.

Source credentials are consumed only by source_snapshot's read-only exporter.
All writes target the private Rust libSQL databases. Source refreshes preserve
independent destination edits and keep exact incoming source rows in a private
baseline for validation, recovery and explicit conflict accounting.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
from source_snapshot import canonical, quote, run as export_source
from snapshot_sqlite import Schema, sqlite_value, source_normalized, physical, import_generation, publish
from turso_http import Remote, DatabaseError, statement, decode


def utc(): return dt.datetime.now(dt.UTC).isoformat()


def row_key(schema, table, row, occurrence=0):
    names = [c["name"] for c in schema.columns[table]]
    values = dict(zip(names,row))
    pk = schema.primary.get(table)
    if pk:
        key=[]
        for c in pk:
            value=sqlite_value(values[c])
            key.append(schema.uca.key(value).hex() if schema.keyed(schema.col(table,c)) else value)
        return canonical(key)
    return hashlib.sha256(canonical(row).encode()).hexdigest() + ":" + str(occurrence)


def snapshot_rows(schema, table):
    report=next(t for t in schema.report["tables"] if t["table"]==table)
    out={}; duplicates=collections.Counter(); total=xor=0; count=0
    with gzip.open(schema.snapshot/report["file"],"rt") as f:
        header=json.loads(next(f))
        if header["columns"] != [c["name"] for c in schema.columns[table]]:
            raise ValueError("Snapshot schema column order changed")
        for line in f:
            row=json.loads(line); text=canonical(row)
            h=int.from_bytes(hashlib.sha256(text.encode()).digest(),"big")
            total=(total+h)%(1<<256);xor^=h;count+=1
            key=row_key(schema,table,row)
            if not schema.primary.get(table):
                occurrence=duplicates[key];duplicates[key]+=1;key=row_key(schema,table,row,occurrence)
            if key in out: raise ValueError("Snapshot has a duplicate primary key")
            out[key]=row
    if count != report["source_rows"] or f"{total:064x}" != report["row_hash_sum"] or f"{xor:064x}" != report["row_hash_xor"]:
        raise ValueError("Snapshot checksum verification failed for " + table)
    return out


def init_tracking(db, schema, generation=1):
    db.batch([
        ("CREATE TABLE IF NOT EXISTS _source_baseline(table_name TEXT NOT NULL,row_key TEXT NOT NULL,source_row TEXT NOT NULL,PRIMARY KEY(table_name,row_key))",()),
        ("CREATE TABLE IF NOT EXISTS _source_fingerprints(table_name TEXT PRIMARY KEY,row_count INTEGER NOT NULL,hash_sum TEXT NOT NULL,hash_xor TEXT NOT NULL)",()),
        ("CREATE TABLE IF NOT EXISTS _source_schema(id INTEGER PRIMARY KEY CHECK(id=1),fingerprint TEXT NOT NULL)",()),
        ("CREATE TABLE IF NOT EXISTS _sync_lease(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT NOT NULL,expires_utc TEXT NOT NULL)",()),
        ("CREATE TABLE IF NOT EXISTS _sync_conflicts(run_id TEXT NOT NULL,table_name TEXT NOT NULL,row_key TEXT NOT NULL,reason TEXT NOT NULL,source_row TEXT,destination_row TEXT,created_utc TEXT NOT NULL,PRIMARY KEY(run_id,table_name,row_key))",()),
        ("CREATE TABLE IF NOT EXISTS _sync_runs(run_id TEXT PRIMARY KEY,report TEXT NOT NULL)",()),
        ("CREATE TABLE IF NOT EXISTS _rust_write_epoch(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL)",()),
        ("INSERT INTO _rust_write_epoch(id,value) VALUES(1,0) ON CONFLICT DO NOTHING",()),
        ("CREATE TABLE IF NOT EXISTS _rust_sequences(table_name TEXT PRIMARY KEY,next_id INTEGER NOT NULL CHECK(next_id>=4294967296 AND next_id<=9007199254740991))",()),
    ])
    existing=db.query("SELECT fingerprint FROM _source_schema WHERE id=1")
    if existing:
        if existing[0]["fingerprint"] != schema.fingerprint: raise ValueError("Source schema differs from initialized baseline")
        return
    for table in schema.tables:
        rows=snapshot_rows(schema,table);batch=[]
        for key,row in rows.items():
            batch.append(("INSERT INTO _source_baseline(table_name,row_key,source_row) VALUES(?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET source_row=excluded.source_row",(table,key,canonical(row))))
            if len(batch)==150: db.batch(batch);batch=[]
        if batch: db.batch(batch)
        report=next(t for t in schema.report["tables"] if t["table"]==table)
        db.batch([("INSERT INTO _source_fingerprints(table_name,row_count,hash_sum,hash_xor) VALUES(?,?,?,?) ON CONFLICT(table_name) DO UPDATE SET row_count=excluded.row_count,hash_sum=excluded.hash_sum,hash_xor=excluded.hash_xor",(table,report["source_rows"],report["row_hash_sum"],report["row_hash_xor"]))])
    db.batch([
        ("INSERT INTO _source_schema(id,fingerprint) VALUES(1,?)",(schema.fingerprint,)),
        ("UPDATE _migration_state SET schema_sha256=? WHERE id=1",(schema.fingerprint,)),
        *[("INSERT INTO _rust_sequences(table_name,next_id) VALUES(?,4294967296) ON CONFLICT DO NOTHING",(t,)) for t in ("loadouts","loadout_user","attachment_loadout") if t in schema.tables],
    ])


def merge_row(old, incoming, destination, columns):
    """Destination wins an overlapping independent edit; preserve its history."""
    if old is None:
        if destination is None or destination == incoming: return incoming,[]
        return destination,["independent-insert-collision"]
    if incoming is None:
        if destination is None or destination == old: return None,[]
        return destination,["source-deletion-of-local-edit"]
    if destination is None:
        return None,(["source-update-of-local-deletion"] if incoming != old else [])
    result=list(destination);conflicts=[]
    for i,column in enumerate(columns):
        if incoming[i] == old[i]: continue
        if destination[i] == old[i] or destination[i] == incoming[i]: result[i]=incoming[i]
        elif column == "updated_at": result[i]=max(incoming[i],destination[i],key=canonical)
        else: conflicts.append(column)
    return result,conflicts


def destination_rows(db,schema,table,generation,close=False):
    names=[c["name"] for c in schema.columns[table]];out={};offset=0;duplicates=collections.Counter()
    while True:
        rows=db.query(f"SELECT {','.join(quote(c,'postgres') for c in names)} FROM {quote(physical(generation,table),'postgres')} LIMIT 1000 OFFSET ?",(offset,),close=close)
        if not rows: break
        for values in rows:
            row=[source_normalized(values[c["name"]],c) for c in schema.columns[table]]
            key=row_key(schema,table,row)
            if not schema.primary.get(table):
                occurrence=duplicates[key];duplicates[key]+=1;key=row_key(schema,table,row,occurrence)
            if key in out: raise ValueError("Destination has duplicate logical keys")
            out[key]=row
        offset+=len(rows)
    return out


def predicate(schema,table,row):
    names=[c["name"] for c in schema.columns[table]]
    pk=schema.primary.get(table)
    if pk:
        values=dict(zip(names,row));clauses=[];args=[]
        for c in pk:
            value=sqlite_value(values[c]);column=schema.comparison_col(table,c)
            clauses.append(quote(column,'postgres') + " IS ?")
            args.append(schema.uca.key(value) if schema.keyed(schema.col(table,c)) else value)
        return " AND ".join(clauses),args
    return " AND ".join(quote(c,'postgres')+" IS ?" for c in names),[sqlite_value(v) for v in row]


def mutation(schema,table,generation,old,new):
    target=quote(physical(generation,table),'postgres')
    if old is None:
        names,values=schema.insert_row(table,new)
        return f"INSERT INTO {target} ({','.join(quote(c,'postgres') for c in names)}) VALUES({','.join('?' for _ in values)})",values
    where,args=predicate(schema,table,old)
    if new is None:
        return f"DELETE FROM {target} WHERE rowid IN(SELECT rowid FROM {target} WHERE {where} LIMIT 1)",args
    names,values=schema.insert_row(table,new)
    return f"UPDATE {target} SET {','.join(quote(c,'postgres')+'=?' for c in names)} WHERE rowid IN(SELECT rowid FROM {target} WHERE {where} LIMIT 1)",values+args


def changed_tables(schema,fingerprints):
    previous={r["table_name"]:r for r in fingerprints};changed=[]
    for t in schema.report["tables"]:
        old=previous.get(t["table"])
        if not old or (old["row_count"],old["hash_sum"],old["hash_xor"]) != (t["source_rows"],t["row_hash_sum"],t["row_hash_xor"]): changed.append(t["table"])
    return changed


def baseline_rows(db,table,close=False):
    out={};offset=0
    while True:
        rows=db.query("SELECT row_key,source_row FROM _source_baseline WHERE table_name=? LIMIT 500 OFFSET ?",(table,offset),close=close)
        if not rows:break
        for row in rows:out[row["row_key"]]=json.loads(row["source_row"])
        offset+=len(rows)
    return out


def close_relationships(schema,desired,destination,incoming,conflicts):
    """Retain a deleted parent for local data; quarantine incompatible source edits."""
    for _ in range(len(schema.tables)+1):
        adjusted=False
        for table in schema.order():
            names=[c["name"] for c in schema.columns[table]]
            for column,parent,parent_column,_,_ in schema.foreign[table]:
                parent_names=[c["name"] for c in schema.columns[parent]]
                def comparison(v,t,c):
                    value=sqlite_value(v)
                    return schema.uca.key(value) if value is not None and schema.keyed(schema.col(t,c)) else value
                parent_index={comparison(row[parent_names.index(parent_column)],parent,parent_column):key for key,row in desired[parent].items()}
                original_index={comparison(row[parent_names.index(parent_column)],parent,parent_column):(key,row) for key,row in destination[parent].items()}
                for key,row in list(desired[table].items()):
                    reference=comparison(row[names.index(column)],table,column)
                    if reference is None or reference in parent_index:continue
                    if reference in original_index:
                        pkey,prow=original_index[reference]
                        desired[parent][pkey]=prow;parent_index[reference]=pkey
                        conflicts.append((parent,pkey,["parent-retained-for-destination-reference"],incoming[parent].get(pkey),prow));adjusted=True
                    else:
                        previous=destination[table].get(key)
                        if previous is None:desired[table].pop(key)
                        else:desired[table][key]=previous
                        conflicts.append((table,key,["source-edit-references-local-deletion"],incoming[table].get(key),previous));adjusted=True
        if not adjusted:return
    raise ValueError("Ownership conflicts cannot produce a valid relationship closure")


def merged_snapshot(schema,desired,directory):
    directory.mkdir(mode=0o700)
    (directory/"schema.json").write_bytes((schema.snapshot/"schema.json").read_bytes())
    report=dict(schema.report);report["tables"]=[]
    for table in schema.tables:
        filename=hashlib.sha256(table.encode()).hexdigest()+".jsonl.gz"
        total=xor=0
        with gzip.open(directory/filename,"wt") as f:
            f.write(canonical({"table":table,"columns":[c["name"] for c in schema.columns[table]]})+"\n")
            for row in desired[table].values():
                text=canonical(row);f.write(text+"\n")
                h=int.from_bytes(hashlib.sha256(text.encode()).digest(),"big");total=(total+h)%(1<<256);xor^=h
        report["tables"].append({"table":table,"source_rows":len(desired[table]),"file":filename,"row_hash_sum":f"{total:064x}","row_hash_xor":f"{xor:064x}"})
    (directory/"report.json").write_text(canonical(report))
    return Schema(directory,Path(__file__).with_name("uca4-primary.json"))


def guard_epoch(db,expected):
    current=db.query("SELECT value FROM _rust_write_epoch WHERE id=1",close=False)[0]["value"]
    if current!=expected:raise ValueError("Destination changed during refresh planning; retry from a fresh destination snapshot")


def write_source_fingerprints(db,schema,close=False):
    statements=[]
    for t in schema.report["tables"]:
        statements.append(("UPDATE _source_fingerprints SET row_count=?,hash_sum=?,hash_xor=? WHERE table_name=?",(t["source_rows"],t["row_hash_sum"],t["row_hash_xor"],t["table"])))
    db.batch(statements,atomic=False,close=close)


def stage_full_sync(db,schema,incoming,desired,conflicts,epoch,run_id):
    # Private staging is built while app writes remain enabled. The epoch lease
    # prevents publishing any plan that missed a destination write.
    generation=int(time.time()*1000)
    with tempfile.TemporaryDirectory(prefix="rust-merge-") as temp:
        staged_schema=merged_snapshot(schema,desired,Path(temp)/"merged")
        staging_report=import_generation(db,staged_schema,generation)
    baseline_table="_baseline_"+run_id.replace('-','')
    db.execute(f"CREATE TABLE {quote(baseline_table,'postgres')} (table_name TEXT NOT NULL,row_key TEXT NOT NULL,source_row TEXT NOT NULL,PRIMARY KEY(table_name,row_key))")
    batch=[]
    for table,rows in incoming.items():
        for key,row in rows.items():
            batch.append((f"INSERT INTO {quote(baseline_table,'postgres')} VALUES(?,?,?)",(table,key,canonical(row))))
            if len(batch)==150:db.batch(batch);batch=[]
    if batch:db.batch(batch)
    db.batch([("PRAGMA foreign_keys=ON",()),("BEGIN IMMEDIATE",())],atomic=False,close=False)
    try:
        guard_epoch(db,epoch)
        current=db.query("SELECT generation FROM _migration_state WHERE id=1",close=False)[0]["generation"]
        statements=[]
        for table in reversed(schema.order()): statements.append((f"DELETE FROM {quote(physical(current,table),'postgres')}",()))
        for table in schema.order(): statements.append((f"INSERT INTO {quote(physical(current,table),'postgres')} SELECT * FROM {quote(physical(generation,table),'postgres')}",()))
        statements.extend([
            ("DELETE FROM _source_baseline",()),
            (f"INSERT INTO _source_baseline SELECT * FROM {quote(baseline_table,'postgres')}",()),
        ])
        db.batch(statements,atomic=False,close=False)
        if db.query("PRAGMA foreign_key_check",close=False):raise ValueError("Staged refresh relationship validation failed")
        for table in schema.tables:
            count=db.query(f"SELECT COUNT(*) AS count FROM {quote(physical(current,table),'postgres')}",close=False)[0]["count"]
            if count!=len(desired[table]):raise ValueError("Staged refresh copy count failed")
        write_source_fingerprints(db,schema)
        result={"run_id":run_id,"method":"validated-staging-swap","generation":current,"staging_generation":generation,"conflicts_preserved":len(conflicts),"source_snapshot_utc":schema.report["snapshot_started_utc"],"tables":staging_report["tables"],"status":"validated"}
        save_run(db,schema,result,conflicts)
        db.execute("COMMIT",close=True)
    except Exception:
        try:db.execute("ROLLBACK",close=True)
        except DatabaseError:pass
        raise
    # These exact private staging objects belong to this completed run only.
    for table in reversed(schema.order()):db.execute(f"DROP TABLE {quote(physical(generation,table),'postgres')}")
    db.execute(f"DROP TABLE {quote(baseline_table,'postgres')}")
    db.execute("DELETE FROM _migration_staging WHERE generation=?",(generation,))
    return result


def save_run(db,schema,result,conflicts):
    unique={(t,key):(t,key,fields,new,dest) for t,key,fields,new,dest in conflicts}
    statements=[]
    for table,key,fields,new,dest in unique.values():
        statements.append(("INSERT INTO _sync_conflicts(run_id,table_name,row_key,reason,source_row,destination_row,created_utc) VALUES(?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",(result["run_id"],table,key,','.join(fields),None if new is None else canonical(new),None if dest is None else canonical(dest),utc())))
    statements.extend([
        ("UPDATE _migration_state SET source_snapshot_utc=?,published_utc=? WHERE id=1",(schema.report["snapshot_started_utc"],utc())),
        ("INSERT INTO _sync_runs(run_id,report) VALUES(?,?)",(result["run_id"],canonical(result))),
    ])
    for start in range(0,len(statements),150):db.batch(statements[start:start+150],atomic=False,close=False)


def acquire_lease(db,owner):
    expires=(dt.datetime.now(dt.UTC)+dt.timedelta(minutes=15)).isoformat()
    result=db.execute("INSERT INTO _sync_lease(id,owner,expires_utc) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_utc=excluded.expires_utc WHERE _sync_lease.expires_utc<?",(owner,expires,utc()))
    return result["affected_row_count"]==1


def release_lease(db,owner):
    db.execute("DELETE FROM _sync_lease WHERE id=1 AND owner=?",(owner,))


def delta_sync(db,schema,run_id):
    started=time.monotonic()
    state=db.query("SELECT generation,schema_sha256 FROM _migration_state WHERE id=1")[0]
    if state["schema_sha256"]!=schema.fingerprint: raise ValueError("Live source schema changed; review conversion before refresh")
    changed=changed_tables(schema,db.query("SELECT * FROM _source_fingerprints"))
    incoming={t:snapshot_rows(schema,t) for t in schema.tables}
    generation=state["generation"]
    if not changed:
        db.batch([("PRAGMA foreign_keys=ON",()),("BEGIN IMMEDIATE",())],atomic=False,close=False)
        result={"run_id":run_id,"method":"unchanged-snapshot","changed_tables":[],"changes":{},"conflicts_preserved":0,"source_snapshot_utc":schema.report["snapshot_started_utc"],"source_counts":{t["table"]:t["source_rows"] for t in schema.report["tables"]},"duration_seconds":round(time.monotonic()-started,3),"status":"validated"}
        try:
            save_run(db,schema,result,[]);db.execute("COMMIT",close=True)
        except Exception:
            try:db.execute("ROLLBACK",close=True)
            except DatabaseError:pass
            raise
        return result
    db.execute("BEGIN READONLY",close=False)
    try:
        epoch=db.query("SELECT value FROM _rust_write_epoch WHERE id=1",close=False)[0]["value"]
        destination={t:destination_rows(db,schema,t,generation) for t in schema.tables}
        previous={t:baseline_rows(db,t) if t in changed else incoming[t] for t in schema.tables}
        db.execute("ROLLBACK",close=True)
    except Exception:
        try:db.execute("ROLLBACK",close=True)
        except DatabaseError:pass
        raise
    desired={t:dict(rows) for t,rows in destination.items()};conflicts=[];baseline=[]
    for table in changed:
        for key in sorted(set(previous[table])|set(incoming[table])):
            old=previous[table].get(key);new=incoming[table].get(key);dest=destination[table].get(key)
            if old==new:continue
            merged,fields=merge_row(old,new,dest,[c["name"] for c in schema.columns[table]])
            if fields:conflicts.append((table,key,fields,new,dest))
            if merged is None:desired[table].pop(key,None)
            else:desired[table][key]=merged
            if new is None:baseline.append(("DELETE FROM _source_baseline WHERE table_name=? AND row_key=?",(table,key)))
            else:baseline.append(("INSERT INTO _source_baseline(table_name,row_key,source_row) VALUES(?,?,?) ON CONFLICT(table_name,row_key) DO UPDATE SET source_row=excluded.source_row",(table,key,canonical(new))))
    close_relationships(schema,desired,destination,incoming,conflicts)
    ops=[];changes=collections.Counter();representatives=[]
    order={t:i for i,t in enumerate(schema.order())}
    for table in schema.tables:
        for key in set(destination[table])|set(desired[table]):
            old=destination[table].get(key);new=desired[table].get(key)
            if old==new:continue
            kind="insert" if old is None else "delete" if new is None else "update"
            changes[kind]+=1
            sort=(2,-order[table]) if kind=="delete" else (0,order[table]) if kind=="insert" else (1,order[table])
            ops.append((sort,mutation(schema,table,generation,old,new)))
            if new is not None:representatives.append((table,new))
    if len(ops)+len(baseline)>2000:
        return stage_full_sync(db,schema,incoming,desired,conflicts,epoch,run_id)
    ops.sort(key=lambda x:x[0])
    db.batch([("PRAGMA foreign_keys=ON",()),("BEGIN IMMEDIATE",())],atomic=False,close=False)
    try:
        guard_epoch(db,epoch)
        for start in range(0,len(ops),150):db.batch([op for _,op in ops[start:start+150]],atomic=False,close=False)
        if db.query("PRAGMA foreign_key_check",close=False):raise ValueError("Refresh would violate relationships; active dataset retained")
        # Every changed row is checked before publishing. Unchanged rows came
        # from a pinned, previously validated destination snapshot.
        for start in range(0,len(representatives),150):
            selected=representatives[start:start+150];queries=[]
            for table,row in selected:
                where,args=predicate(schema,table,row)
                names=[c["name"] for c in schema.columns[table]]
                sql=f"SELECT {','.join(quote(c,'postgres') for c in names)} FROM {quote(physical(generation,table),'postgres')} WHERE {where} LIMIT 1"
                queries.append({"type":"execute","stmt":statement(sql,args)})
            actuals=db.pipeline(queries,close=False)
            for (table,row),actual in zip(selected,actuals):
                columns=[c["name"] for c in actual["cols"]]
                values=dict(zip(columns,(decode(v) for v in actual["rows"][0]))) if actual["rows"] else {}
                if not values or [source_normalized(values[c["name"]],c) for c in schema.columns[table]]!=row:raise ValueError("Changed-row validation failed")
        for start in range(0,len(baseline),150):db.batch(baseline[start:start+150],atomic=False,close=False)
        write_source_fingerprints(db,schema)
        result={"run_id":run_id,"method":"delta-three-way","changed_tables":changed,"changes":dict(changes),"conflicts_preserved":len(conflicts),"source_snapshot_utc":schema.report["snapshot_started_utc"],"source_counts":{t["table"]:t["source_rows"] for t in schema.report["tables"]},"destination_counts":{t:len(desired[t]) for t in schema.tables},"duration_seconds":round(time.monotonic()-started,3),"status":"validated"}
        save_run(db,schema,result,conflicts)
        db.execute("COMMIT",close=True)
        return result
    except Exception:
        try:db.execute("ROLLBACK",close=True)
        except DatabaseError:pass
        raise


def forecast_complete(schema):
    if schema.engine!="postgres":return
    shelters=snapshot_rows(schema,"shelters");names=[c["name"] for c in schema.columns["shelters"]];ids={r[names.index("id")] for r in shelters.values()}
    for table,minimum in [("weathers",7),("hourly_weathers",40)]:
        columns=[c["name"] for c in schema.columns[table]];counts=collections.Counter(r[columns.index("shelter_id")] for r in snapshot_rows(schema,table).values())
        if any(counts[i]<minimum for i in ids):raise ValueError("Forecast source replacement is incomplete; active dataset retained")


def forecast_sync(db,schema,run_id):
    forecast_complete(schema)
    state=db.query("SELECT generation,schema_sha256 FROM _migration_state WHERE id=1")[0]
    if state["schema_sha256"]!=schema.fingerprint:raise ValueError("Source schema changed; refresh conversion needs review")
    changed=changed_tables(schema,db.query("SELECT * FROM _source_fingerprints"))
    if not changed:return delta_sync(db,schema,run_id)
    generation=state["generation"]+1
    result=import_generation(db,schema,generation)
    result.update({"run_id":run_id,"method":"validated-generation","changed_tables":changed,"status":"validated"})
    extra=[]
    for table in changed:
        t=next(t for t in schema.report["tables"] if t["table"]==table)
        extra.append(("UPDATE _source_fingerprints SET row_count=?,hash_sum=?,hash_xor=? WHERE table_name=?",(t["source_rows"],t["row_hash_sum"],t["row_hash_xor"],table)))
    extra.append(("INSERT INTO _sync_runs(run_id,report) VALUES(?,?)",(run_id,canonical(result))))
    publish(db,schema,generation,result,extra)
    cleanup_generations(db,schema,generation)
    return result


def cleanup_generations(db,schema,active):
    # Keep the active and previous published generation for destination rollback.
    keep=[r["generation"] for r in db.query("SELECT generation FROM _migration_reports ORDER BY generation DESC LIMIT 2")]
    for row in db.query("SELECT generation FROM _migration_staging"):
        gen=row["generation"]
        if gen in keep or gen>=active:continue
        for table in reversed(schema.order()):db.execute(f"DROP TABLE IF EXISTS {quote(physical(gen,table),'postgres')}")
        db.batch([("DELETE FROM _migration_staging WHERE generation=?",(gen,)),("DELETE FROM _migration_reports WHERE generation=?",(gen,))])


def main():
    database=os.environ["TURSO_DATABASE_NAME"]
    if database not in ("atforecast-rust","gameloadouts-rust"):raise ValueError("Destination is not an allowlisted Rust database")
    db=Remote(os.environ["TURSO_DATABASE_URL"],os.environ["TURSO_AUTH_TOKEN"])
    run_id=str(uuid.uuid4());started=time.monotonic()
    if not acquire_lease(db,run_id):
        print(canonical({"status":"skipped-overlap","run_id":run_id}));return 0
    try:
        with tempfile.TemporaryDirectory(prefix="rust-source-") as temp:
            destination=Path(temp)/"snapshot"
            cfg=json.loads(Path(os.environ["SOURCE_CONFIG_FILE"]).read_text())
            export_source(cfg,destination,True,lock_nontransactional=True)
            schema=Schema(destination,Path(__file__).with_name("uca4-primary.json"))
            for attempt in range(3):
                try:
                    result=forecast_sync(db,schema,run_id) if cfg["engine"]=="postgres" else delta_sync(db,schema,run_id)
                    break
                except ValueError as e:
                    if not str(e).startswith("Destination changed during refresh planning") or attempt==2:raise
            db.execute("DELETE FROM _sync_runs WHERE run_id NOT IN(SELECT run_id FROM _sync_runs ORDER BY rowid DESC LIMIT 2000)")
            result.update({"http_requests":db.requests,"rows_read":db.read,"rows_written":db.written,"finished_utc":utc(),"total_duration_seconds":round(time.monotonic()-started,3),"freshness_seconds":round((dt.datetime.now(dt.UTC)-dt.datetime.fromisoformat(schema.report["snapshot_started_utc"])).total_seconds(),3)})
            print(canonical(result))
    except Exception as e:
        print(canonical({"status":"failed","run_id":run_id,"error_type":type(e).__name__,"error_reason":str(e) if isinstance(e,DatabaseError) else "validation-or-source-error"}));return 1
    finally:
        release_lease(db,run_id)
    return 0


if __name__=="__main__":raise SystemExit(main())
