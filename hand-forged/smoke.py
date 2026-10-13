"""Focused HTTP checks against the isolated container; credentials are synthetic."""
import http.cookiejar,urllib.request,urllib.parse,json,re,time,os
from pathlib import Path
base=os.environ.get('DEMO_BASE_URL','http://127.0.0.1:8080')
assert base=='http://127.0.0.1:8080','Container checks must not target production'
jar=http.cookiejar.CookieJar();browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
def request(path,fields=None):
 data=None if fields is None else urllib.parse.urlencode(fields).encode()
 q=urllib.request.Request(base+path,data=data,headers={'Referer':base+path})
 with browser.open(q,timeout=60) as response:
  return response.geturl().removeprefix(base),response.read().decode()
def csrf(html):
 m=re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"',html)
 if not m:m=re.search(r'name="authenticity_token"[^>]+value="([^"]+)"',html)
 assert m,'Original CSRF token missing';return m[1]
for _ in range(180):
 try:
  _,home=request('/');break
 except Exception:time.sleep(1)
else:raise AssertionError('Startup failed')
Path('hand-forged/test-results').mkdir(exist_ok=True)
Path('hand-forged/test-results/startup.json').write_text(json.dumps({'startup_seconds':round(time.time()-float(os.environ.get('HAND_FORGED_STARTED_AT',time.time())),3)}))
assert 'Hand-Forged Edition' in home and 'Sample forecasts' in home and 'Georgia' in home
_,body=request('/index.json?api_key=hand-forged-demo&include_shelters=true');data=json.loads(body)
assert len(data)==13 and sum(len(s['shelters']) for s in data)==281
_,body=request('/shelters/1.json?api_key=hand-forged-demo');weather=json.loads(body)[0]['daily_weather'];assert len(weather)==7 and sum(len(d['hourly_weather']) for d in weather)>40
_,html=request('/shelters/1');assert 'Max Epperson Shelter' in html
_,html=request('/nearest_shelter?nobo_mile=10');assert 'Hawk Mountain Shelter' in html
_,html=request('/admin/login');token=csrf(html)
path,html=request('/admin/login',{'authenticity_token':token,'admin_user[email]':'admin@handforged.invalid','admin_user[password]':'HandForgedDemo!','commit':'Login'})
assert '/admin' in path and 'Dashboard' in html and 'Invalid Email' not in html
assert any(c.name=='_atforecast_hand_forged_session' for c in jar) and not any(c.name=='_Fast-AT-Weather_session' for c in jar)
_,html=request('/admin/states/new');token=csrf(html)
path,html=request('/admin/states',{'authenticity_token':token,'state[name]':'Demo validation state','state[abbreviation]':'XX','commit':'Create State'})
assert re.fullmatch('/admin/states/[0-9]+',path),path
state=path.rsplit('/',1)[1]
_,html=request(path+'/edit');token=csrf(html)
path,html=request('/admin/states/'+state,{'_method':'put','authenticity_token':token,'state[name]':'Updated demo state','state[abbreviation]':'XX','commit':'Update State'})
assert 'Updated demo state' in html
_,html=request('/admin/shelters/new');token=csrf(html)
path,html=request('/admin/shelters',{'authenticity_token':token,'shelter[name]':'Demo relationship shelter','shelter[mileage]':'5.5','shelter[elevation]':'1000','shelter[long]':'-84.2','shelter[latt]':'34.6','shelter[state_id]':state,'commit':'Create Shelter'})
assert re.fullmatch('/admin/shelters/[0-9]+',path),path
request(path,{'_method':'delete','authenticity_token':csrf(html)})
_,html=request('/admin/states/'+state);request('/admin/states/'+state,{'_method':'delete','authenticity_token':csrf(html)})
_,html=request('/admin/shelters/1');request('/admin/shelters/1/refresh_demo_weather',{'authenticity_token':csrf(html)})
_,body=request('/shelters/1.json?api_key=hand-forged-demo');fresh=json.loads(body)[0]['daily_weather'];assert len(fresh)==7 and fresh[0]['daily_weather_id']!=weather[0]['daily_weather_id']
# Exercise the original Devise reset with local delivery for a temporary synthetic admin.
_,html=request('/admin/admin_users/new');token=csrf(html)
path,html=request('/admin/admin_users',{'authenticity_token':token,'admin_user[email]':'mailcheck@handforged.invalid','admin_user[password]':'HandForgedMail123!','admin_user[password_confirmation]':'HandForgedMail123!','commit':'Create Admin user'})
assert re.fullmatch('/admin/admin_users/[0-9]+',path),path
mail_user=path.rsplit('/',1)[1]
_,html=request('/admin');request('/admin/logout',{'_method':'delete','authenticity_token':csrf(html)})
_,html=request('/admin/password/new');request('/admin/password',{'authenticity_token':csrf(html),'admin_user[email]':'mailcheck@handforged.invalid','commit':'Reset My Password'})
_,inbox=request('/hand-forged/mail');assert 'Captured demo email' in inbox
_,html=request('/admin/login');request('/admin/login',{'authenticity_token':csrf(html),'admin_user[email]':'admin@handforged.invalid','admin_user[password]':'HandForgedDemo!','commit':'Login'})
_,html=request('/admin/admin_users/'+mail_user);request('/admin/admin_users/'+mail_user,{'_method':'delete','authenticity_token':csrf(html)})
Path('hand-forged/test-results').mkdir(exist_ok=True)
Path('hand-forged/test-results/public.html').write_text(home)
Path('hand-forged/test-results/shelter.json').write_text(body)
print('PASS populated HTML/assets, admin login, create/update/delete, shelter relationship, daily/hourly JSON, original-method fixture refresh')
