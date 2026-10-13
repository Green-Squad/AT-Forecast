#!/usr/bin/env python3
"""Provision named manual-only Rust jobs; never create or enable schedules."""
import json
import os, subprocess

def call(args,check=True):
    # gcloud must already be authenticated. Credentials never appear in output.
    result=subprocess.run(["gcloud",*args],capture_output=True)
    if check and result.returncode:raise RuntimeError("Rust resource operation failed: "+" ".join(args[:3]))
    return result

MAP=[('at-forecast','at-forecast-rust','us-east4','atforecast-rust-us'),('game-loadouts','game-loadouts-rust','us-east4','gameloadouts-rust-us')]
def provision(project,service,region,database,tag):
    job=service+'-sync'
    exists=call(['run','jobs','describe',job,'--region',region,'--project',project],check=False).returncode==0
    args=['run','jobs','update' if exists else 'create',job,'--region',region,'--project',project,
          '--image',f'gcr.io/{project}/{service}-sync:{tag}','--service-account','rust-turso-sync@'+project+'.iam.gserviceaccount.com',
          '--tasks','1','--parallelism','1','--max-retries','2','--task-timeout','600s','--cpu','1','--memory','512Mi',
          '--set-env-vars',f'TURSO_DATABASE_NAME={database},TURSO_DATABASE_URL=libsql://{database}-greensquad.aws-us-east-1.turso.io,SOURCE_CONFIG_FILE=/secrets/source/config.json',
          '--set-secrets','/secrets/source/config.json=rust-turso-source:1,TURSO_AUTH_TOKEN=rust-turso-import:2',
          '--labels','scope=rust-turso-migration','--quiet']
    call(args)
    print(json.dumps({'project':project,'job':job,'region':region,'mode':'manual-only','status':'configured'}))
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--project',choices=[x[0] for x in MAP],required=True);p.add_argument('--tag',required=True);a=p.parse_args()
    provision(*next(x for x in MAP if x[0]==a.project),a.tag)
