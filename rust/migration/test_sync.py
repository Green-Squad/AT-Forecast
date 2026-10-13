"""Controlled synthetic snapshots. No live credentials, source connections or records."""
import gzip, hashlib, json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from source_snapshot import canonical
from snapshot_sqlite import Schema, import_generation, publish, validate
from sync_data import init_tracking, delta_sync, forecast_sync, acquire_lease, release_lease

class Local:
    def __init__(self):
        self.c=sqlite3.connect(':memory:',isolation_level=None);self.c.row_factory=sqlite3.Row
        self.requests=self.read=self.written=0;self.fail=None
    def execute(self,sql,args=(),close=True):
        if self.fail and self.fail(sql):raise RuntimeError('controlled failure')
        if sql=='BEGIN READONLY':sql='BEGIN'
        self.requests+=1;cursor=self.c.execute(sql,args)
        rows=cursor.fetchall();self.read+=len(rows);self.written+=max(cursor.rowcount,0)
        result={'rows':rows,'cols':[c[0] for c in cursor.description] if cursor.description else [],'affected_row_count':max(cursor.rowcount,0)}
        if close and self.c.in_transaction:self.c.rollback()
        return result
    def query(self,sql,args=(),close=True):return [dict(r) for r in self.execute(sql,args,close)['rows']]
    def pipeline(self,operations,close=True):
        from turso_http import decode,value
        result=[]
        for op in operations:
            stmt=op['stmt'];r=self.execute(stmt['sql'],[decode(v) for v in stmt['args']],False)
            result.append({'cols':[{'name':c} for c in r['cols']],'rows':[[value(v) for v in row] for row in r['rows']]})
        if close and self.c.in_transaction:self.c.rollback()
        return result
    def batch(self,statements,atomic=True,close=True):
        if atomic:self.c.execute('PRAGMA foreign_keys=ON');self.c.execute('BEGIN IMMEDIATE')
        try:
            result=[self.execute(sql,args,False) for sql,args in statements]
            if atomic:self.c.execute('COMMIT')
        except Exception:
            if close and self.c.in_transaction:self.c.rollback()
            raise
        if close and self.c.in_transaction:self.c.rollback()
        return result

class IsolatedRemote:
    def __init__(self,url):
        from turso_http import Remote
        from urllib.parse import urlparse
        target=urlparse(url)
        if os.getenv("TURSO_TEST_DATABASE")!="isolated" or target.scheme!="http" or target.hostname not in ("fixture-sync","127.0.0.1","localhost"):
            raise ValueError("Controlled sync tests require an isolated destination")
        self.db=Remote(url,"");self.fail=None
        names=self.db.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        self.db.execute("PRAGMA foreign_keys=OFF",close=False)
        for row in names:self.db.execute('DROP TABLE "'+row['name']+'"',close=False)
        self.db.execute("PRAGMA foreign_keys=ON")
    def __getattr__(self,name):return getattr(self.db,name)
    def execute(self,sql,args=(),close=True):
        if self.fail and self.fail(sql):raise RuntimeError("controlled failure")
        return self.db.execute(sql,args,close)
    def query(self,sql,args=(),close=True):return self.db.query(sql,args,close)
    def batch(self,statements,atomic=True,close=True):
        statements=list(statements)
        if self.fail and any(self.fail(sql) for sql,_ in statements):raise RuntimeError("controlled failure")
        return self.db.batch(statements,atomic,close)

class SyncTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=IsolatedRemote(os.environ["SYNC_TEST_URL"]) if "SYNC_TEST_URL" in os.environ else Local();self.number=0
    def tearDown(self):
        if isinstance(self.db,Local):self.db.c.close()
        self.temp.cleanup()
    def snapshot(self,rows,forecast=False):
        if forecast:
            rows={t:list(values) for t,values in rows.items()}
            for t,n in [('weathers',7),('hourly_weathers',40)]:
                if len(rows[t])==1:
                    row=rows[t][0];rows[t]=[[row[0]+i,row[1],row[2]+str(i)] for i in range(n)]
        self.number+=1;p=self.root/str(self.number);p.mkdir()
        columns={t:[('id','integer'),('name','text')] for t in rows}
        if "children" in rows:
            columns["children"]=[("id","integer"),("name","text"),("parent_id","integer")]
        if forecast:
            columns={'shelters':[('id','integer'),('mileage','numeric'),('state_id','integer')], 'weathers':[('id','integer'),('shelter_id','integer'),('weather_date','text')], 'hourly_weathers':[('id','integer'),('shelter_id','integer'),('date','text')]}
        meta={'columns':[{'table_name':t,'name':n,'source_type':ty,'nullable':True,'default_value':None,'collation':None} for t,cs in columns.items() for n,ty in cs], 'constraints':[{'table_name':t,'kind':'p','definition':'PRIMARY KEY (id)'} for t in rows], 'indexes':[],'tables':[{'name':t} for t in rows], 'sequences':[], 'views':[], 'triggers':[]}
        if "children" in rows:
            meta["constraints"].append({"table_name":"children","kind":"f","definition":"FOREIGN KEY (parent_id) REFERENCES parents(id)"})
        if forecast:
            meta['constraints'] += [{'table_name':t,'kind':'f','definition':'FOREIGN KEY (shelter_id) REFERENCES shelters(id)'} for t in ('weathers','hourly_weathers')]
        report={'engine':'postgres','status':'snapshot-complete','snapshot_started_utc':'2026-10-13T21:00:00+00:00','tables':[]}
        for t,values in rows.items():
            hs=hx=0;file=t+'.gz'
            with gzip.open(p/file,'wt') as f:
                f.write(canonical({'columns':[n for n,_ in columns[t]]})+'\n')
                for row in values:
                    text=canonical(row);f.write(text+'\n');h=int.from_bytes(hashlib.sha256(text.encode()).digest(),'big');hs=(hs+h)%(1<<256);hx^=h
            report['tables'].append({'table':t,'file':file,'source_rows':len(values),'row_hash_sum':f'{hs:064x}','row_hash_xor':f'{hx:064x}'})
        (p/'schema.json').write_text(canonical(meta));(p/'report.json').write_text(canonical(report))
        return Schema(p,Path(__file__).with_name('uca4-primary.json'))
    def initialize(self,s):publish(self.db,s,1,import_generation(self.db,s,1));init_tracking(self.db,s)
    def test_insert_update_delete_retry_and_local_ownership(self):
        self.initialize(self.snapshot({'items':[[1,'old'],[2,'delete'],[3,'source-old']]}))
        self.db.batch([("UPDATE _g1_items SET name='local-edit' WHERE id=3",()),("INSERT INTO _g1_items VALUES(4294967296,'local-only')",()),("UPDATE _rust_write_epoch SET value=value+1",())])
        s=self.snapshot({'items':[[1,'new'],[3,'source-new'],[4,'insert']]})
        result=delta_sync(self.db,s,'r1');self.assertEqual(result['changes'],{'insert':1,'update':1,'delete':1});self.assertEqual(result['conflicts_preserved'],1)
        self.assertEqual(self.db.query('SELECT id,name FROM _g1_items ORDER BY id'),[{'id':1,'name':'new'},{'id':3,'name':'local-edit'},{'id':4,'name':'insert'},{'id':4294967296,'name':'local-only'}])
        self.assertEqual(delta_sync(self.db,s,'r2')['method'],'unchanged-snapshot')
        self.assertEqual(len(self.db.query('SELECT * FROM _sync_conflicts')),1)
    def test_failure_retains_active_data_and_retry_recovers(self):
        self.initialize(self.snapshot({'items':[[1,'old']]}));s=self.snapshot({'items':[[1,'new'],[2,'insert']]})
        self.db.fail=lambda sql:sql.startswith('INSERT INTO _source_baseline')
        with self.assertRaisesRegex(RuntimeError,'controlled failure'):delta_sync(self.db,s,'fail')
        self.db.fail=None
        self.assertEqual(self.db.query('SELECT name FROM _g1_items'),[{'name':'old'}]);delta_sync(self.db,s,'retry');self.assertEqual(len(self.db.query('SELECT * FROM _g1_items')),2)
    def test_forecast_replacement_and_incomplete_rejection(self):
        shelter=[1,{'$decimal':'1.250'},None]
        self.initialize(self.snapshot({'shelters':[shelter],'weathers':[[1,1,'old']],'hourly_weathers':[[1,1,'old']]},True))
        incomplete=self.snapshot({'shelters':[shelter],'weathers':[],'hourly_weathers':[[2,1,'new']]},True)
        with self.assertRaisesRegex(ValueError,'incomplete'):forecast_sync(self.db,incomplete,'bad')
        self.assertEqual(self.db.query('SELECT generation FROM _migration_state')[0]['generation'],1)
        good=self.snapshot({'shelters':[shelter],'weathers':[[2,1,'new']],'hourly_weathers':[[2,1,'new']]},True)
        self.assertEqual(forecast_sync(self.db,good,'good')['generation'],2);self.assertEqual(validate(self.db,good,2)[0]['normalized_checksums_match'],True)
        self.assertEqual(forecast_sync(self.db,good,'again')['method'],'unchanged-snapshot')
    def test_epoch_guard_and_overlap_lease(self):
        self.initialize(self.snapshot({'items':[[1,'old']]}));s=self.snapshot({'items':[[1,'new']]})
        self.assertTrue(acquire_lease(self.db,'one'));self.assertFalse(acquire_lease(self.db,'two'));release_lease(self.db,'two');self.assertFalse(acquire_lease(self.db,'two'));release_lease(self.db,'one');self.assertTrue(acquire_lease(self.db,'two'))
        from sync_data import guard_epoch
        def concurrent(db,epoch):db.execute('UPDATE _rust_write_epoch SET value=value+1',close=False);guard_epoch(db,epoch)
        with patch('sync_data.guard_epoch',concurrent):
            with self.assertRaisesRegex(ValueError,'Destination changed'):delta_sync(self.db,s,'race')
        self.assertEqual(self.db.query('SELECT name FROM _g1_items')[0]['name'],'old');delta_sync(self.db,s,'safe')
    def test_large_refresh_stages_and_publishes_atomically(self):
        self.initialize(self.snapshot({'items':[[1,'old']]}));s=self.snapshot({'items':[[i,'new'] for i in range(1,1201)]})
        self.assertEqual(delta_sync(self.db,s,'large')['method'],'validated-staging-swap');self.assertEqual(validate(self.db,s,1)[0]['destination_rows'],1200)
    def test_parent_replacement_and_destination_reference_retention(self):
        first=self.snapshot({'parents':[[1,'old']],'children':[[1,'source',1]]})
        first.foreign['children']=[('parent_id','parents','id','CASCADE','CASCADE')]
        self.initialize(first)
        moved=self.snapshot({'parents':[[2,'new']],'children':[[1,'source',2]]})
        moved.foreign['children']=[('parent_id','parents','id','CASCADE','CASCADE')]
        delta_sync(self.db,moved,'replace')
        self.assertEqual(self.db.query('SELECT parent_id FROM _g1_children'),[{'parent_id':2}])
        self.db.batch([("INSERT INTO _g1_children VALUES(4294967296,'local',2)",()),("UPDATE _rust_write_epoch SET value=value+1",())])
        removed=self.snapshot({'parents':[],'children':[]});removed.foreign['children']=moved.foreign['children']
        result=delta_sync(self.db,removed,'remove')
        self.assertGreater(result['conflicts_preserved'],0)
        self.assertEqual(self.db.query('SELECT id FROM _g1_parents'),[{'id':2}])
        self.assertEqual(self.db.query('SELECT id FROM _g1_children'),[{'id':4294967296}])
    def test_raw_generation_publication_cannot_replace_independent_writes(self):
        s=self.snapshot({'items':[[1,'old']]});self.initialize(s)
        report=import_generation(self.db,s,2);self.db.execute('UPDATE _rust_write_epoch SET value=1')
        s.engine='mysql'
        with self.assertRaisesRegex(ValueError,'Independent Rust writes'):publish(self.db,s,2,report)
        self.assertEqual(self.db.query('SELECT generation FROM _migration_state')[0]['generation'],1)
    def test_raw_publish_epoch_guard_is_atomic(self):
        s=self.snapshot({'items':[[1,'old']]});self.initialize(s);report=import_generation(self.db,s,2);s.engine='mysql'
        original=self.db.batch
        def race(statements,atomic=True,close=True):
            statements=list(statements)
            if any('_migration_publish_guard' in sql for sql,_ in statements):
                self.db.execute('UPDATE _rust_write_epoch SET value=1')
            return original(statements,atomic,close)
        self.db.batch=race
        with self.assertRaises(Exception):publish(self.db,s,2,report)
        self.db.batch=original
        self.assertEqual(self.db.query('SELECT generation FROM _migration_state')[0]['generation'],1)
        self.db.execute('UPDATE _rust_write_epoch SET value=0');publish(self.db,s,2,report)
        self.assertEqual(self.db.query('SELECT generation FROM _migration_state')[0]['generation'],2)
    def test_import_is_idempotent_and_recovers_owned_partial_stage(self):
        s=self.snapshot({'items':[[1,'old'],[2,'new']]});self.initialize(s)
        self.assertTrue(import_generation(self.db,s,1)['reused'])
        self.db.fail=lambda sql:sql.startswith('INSERT INTO "_g2_items"')
        with self.assertRaises(RuntimeError):import_generation(self.db,s,2)
        self.db.fail=None;self.assertEqual(import_generation(self.db,s,2)['tables'][0]['destination_rows'],2)

if __name__=='__main__':unittest.main()
