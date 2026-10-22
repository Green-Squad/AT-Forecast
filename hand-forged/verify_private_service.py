"""Check privacy after deployment; Cloud Run waits for readiness and CI tests the app."""
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

service = 'atforecast-hand-forged'
command = ['gcloud', 'run', 'services']
flags = ['--project=at-forecast', '--region=us-east4', '--format=json']
info = json.loads(subprocess.check_output(command + ['describe', service] + flags))
assert info['metadata']['annotations'].get('run.googleapis.com/iap-enabled') == 'true'
assert info['metadata']['annotations'].get('run.googleapis.com/invoker-iam-disabled') != 'true'
policy = json.loads(subprocess.check_output(command + ['get-iam-policy', service] + flags))
assert not any({'allUsers', 'allAuthenticatedUsers'} & set(b['members']) for b in policy.get('bindings', []))
assert any(b['role'] == 'roles/run.invoker' and not b.get('condition') and
           'serviceAccount:service-375079752959@gcp-sa-iap.iam.gserviceaccount.com' in b['members']
           for b in policy.get('bindings', []))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

urls = {info['status']['url'], 'https://handforged.atforecast.app',
        'https://atforecast-hand-forged-375079752959.us-east4.run.app'}
urls.update(t['url'] for t in info['status'].get('traffic', []) if t.get('url'))
for url in urls:
    try:
        response = urllib.request.build_opener(NoRedirect()).open(url + '/', timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.code in (302, 303), 'Expected Google sign-in redirect'
        assert urllib.parse.urlsplit(response.headers.get('Location', '')).hostname == 'accounts.google.com'
        assert not response.headers.get('X-Powered-By'), 'Anonymous request reached PHP'
print('Private IAP permissions and Google sign-in verified on every demo URL.')
