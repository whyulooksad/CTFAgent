# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|---------|---------|
| I1 | verified | HTTP fingerprinting and route/input enumeration | Root is a PHP 5.5.38 NMAP scan frontend. POST form has one `host` field; `/list.php` and static css/img/js/xml paths are exposed. | 2026-07-31 18:00:31 +08:00 |
| I2 | testing | `host`-parameter shell command injection | Metacharacter and quote attempts were passed to Nmap as literal invalid target text; escaping behavior needs precise validation before additional variants. | 2026-07-31 17:56:58 +08:00 |
| I3 | testing | Result-view data flow flaws: predictable result IDs, LFI/path traversal, stored output handling | `result.php?f=<id>` parses results; raw XML is directly readable at `/xml/<id>`. `list.php` leaks its filesystem location in a warning. | 2026-07-31 18:00:31 +08:00 |
| I4 | testing | Nmap option injection | Quote-closure variants attempting `-iL /flag` and `-oN` remained part of target specification; whitespace/encoding/backslash behavior remains unverified. | 2026-07-31 17:56:58 +08:00 |
| I5 | testing | PHP source/backup disclosure | Initial candidate probes showed no clear non-404 source-copy hit. Do not call failed yet; static directory indexes/resources pending review. | 2026-07-31 18:00:31 +08:00 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | Target is `http://53653037b8441eb267c2487f.http-ctf2.dasctf.com:80`; no background/hint was supplied. | progress.md | 2026-07-31 17:52:55 +08:00 |
| M2 | evidence | Root response: Server chain reports openresty; origin identifies Apache/2.4.10 Debian; `X-Powered-By: PHP/5.5.38`. | codex.log | 2026-07-31 17:53:41 +08:00 |
| M3 | fact | Root form is `POST /?` with text input `host`; its UI links to `/list.php` (“View existing results”). | codex.log | 2026-07-31 17:53:41 +08:00 |
| M4 | hint | Root HTML comment explicitly states `flag is in /flag`. | codex.log | 2026-07-31 17:53:41 +08:00 |
| M5 | evidence | Probes for `/app.py`, `/package.json`, and `/composer.json` returned Apache 404. | codex.log | 2026-07-31 17:53:41 +08:00 |
| M6 | evidence | Generated scan XML records an effective Nmap command approximately `nmap -Pn -T4 -F --host-timeout 1000ms -oX xml/<id> "<host>"`; Nmap is 6.47. | codex.log | 2026-07-31 17:56:58 +08:00 |
| M7 | evidence | Quote/option payloads were reflected as a single escaped target and skipped as invalid. Web probe for attempted output `/qout.txt` was 404. | codex.log | 2026-07-31 17:56:58 +08:00 |
| M8 | fact | `result.php?f=<id>` consumes saved scan data and raw result data is accessible at `/xml/<id>`. | progress.md / codex.log | 2026-07-31 17:56:58 +08:00 |
| M9 | fact | `branch.py` is not in the challenge cwd; absolute path is `/home/stw/ctf-agent/branch.py`. Initial relative-path spawn failed before starting branches. | codex.log | 2026-07-31 17:56:58 +08:00 |
| M10 | evidence | A normal scan redirects to `result.php?f=9b495`; `/xml/9b495` is valid Nmap XML. `list.php` warning leaks `/var/www/html/list.php`. | progress.md Branch branch_001 Notes | 2026-07-31 18:00:31 +08:00 |
| M11 | evidence | Directory enumeration found css, img, js, and xml directories. Initial source/backup candidate list had no clear hit beyond same-sized 404s. | progress.md Branch branch_001 Notes | 2026-07-31 18:00:31 +08:00 |
