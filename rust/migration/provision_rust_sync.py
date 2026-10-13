#!/usr/bin/env python3
"""Only named Rust jobs/schedulers; no source or original resource mutations."""
import json
import os, subprocess

def call(args,check=True):
    # gcloud must already be authenticated. Credentials never appear in output.
    result=subprocess.run(["gcloud",*args],capture_output=True)
    if check and result.returncode:raise RuntimeError("Rust resource operation failed: "+" ".join(args[:3]))
    return result

MAP=[('at-forecast','at-forecast-rust','us-east4','atforecast-rust'),('game-loadouts','game-loadouts-rust','europe-west1','gameloadouts-rust')]
def provision(project,service,region,database,tag):
    job=service+'-sync';scheduler=job+'-schedule';scheduler_sa='rust-turso-scheduler@'+project+'.iam.gserviceaccount.com'
    call(['services','enable','cloudscheduler.googleapis.com','--project',project])
    if call(['iam','service-accounts','describe',scheduler_sa,'--project',project],check=False).returncode:
        call(['iam','service-accounts','create','rust-turso-scheduler','--display-name','Rust Turso sync scheduler','--project',project])
    exists=call(['run','jobs','describe',job,'--region',region,'--project',project],check=False).returncode==0
    args=['run','jobs','update' if exists else 'create',job,'--region',region,'--project',project,
          '--image',f'gcr.io/{project}/{service}-sync:{tag}','--service-account','rust-turso-sync@'+project+'.iam.gserviceaccount.com',
          '--tasks','1','--parallelism','1','--max-retries','2','--task-timeout','600s','--cpu','1','--memory','512Mi',
          '--set-env-vars',f'TURSO_DATABASE_NAME={database},TURSO_DATABASE_URL=libsql://{database}-greensquad.aws-eu-west-1.turso.io,SOURCE_CONFIG_FILE=/secrets/source/config.json',
          '--set-secrets','/secrets/source/config.json=rust-turso-source:1,TURSO_AUTH_TOKEN=rust-turso-import:1',
          '--labels','scope=rust-turso-migration','--quiet']
    call(args)
    call(['run','jobs','add-iam-policy-binding',job,'--region',region,'--project',project,'--member','serviceAccount:'+scheduler_sa,'--role','roles/run.invoker'])
    exists=call(['scheduler','jobs','describe',scheduler,'--location',region,'--project',project],check=False).returncode==0
    call(['scheduler','jobs','update' if exists else 'create','http',scheduler,'--location',region,'--project',project,
          '--schedule','7,37 * * * *' if project=='at-forecast' else '17,47 * * * *','--time-zone','Etc/UTC',
          '--uri',f'https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run','--http-method','POST',
          '--oauth-service-account-email',scheduler_sa,'--oauth-token-scope','https://www.googleapis.com/auth/cloud-platform',
          '--message-body','{}','--attempt-deadline','60s','--max-retry-attempts','2','--min-backoff','60s','--max-backoff','300s','--quiet'])
    print(json.dumps({'project':project,'job':job,'region':region,'interval_minutes':30,'scheduler':scheduler,'status':'configured'}))
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--project',choices=[x[0] for x in MAP],required=True);p.add_argument('--tag',required=True);a=p.parse_args()
    provision(*next(x for x in MAP if x[0]==a.project),a.tag)
