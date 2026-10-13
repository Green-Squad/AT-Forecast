"""Publish only a commit whose isolated disposable-demo checks passed."""
import json,re,sys,time,urllib.request
sha=sys.argv[1]
assert re.fullmatch('[0-9a-f]{40}',sha),'A full commit SHA is required'
url='https://api.github.com/repos/Green-Squad/AT-Forecast/commits/'+sha+'/check-runs'
for _ in range(20):
 try:
  request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'Hand-Forged-validation'})
  with urllib.request.urlopen(request,timeout=20) as response:data=json.load(response)
  checks=[c for c in data['check_runs'] if c['name']=='disposable-demo' and c['head_sha']==sha]
  if checks and checks[0]['status']=='completed':
   if checks[0]['conclusion']!='success':sys.exit('Isolated demo validation failed; deployment refused')
   print('Verified isolated disposable-demo checks for '+sha);sys.exit(0)
 except (OSError,ValueError) as error:print('Waiting for validation:',error,flush=True)
 time.sleep(30)
sys.exit('Validation did not pass in time; deployment refused')
