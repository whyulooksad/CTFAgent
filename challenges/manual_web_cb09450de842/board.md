# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|---------|---------|
| I1 | verified | Baseline application reconnaissance | PHP/7.3.11 on openresty; login form sends GET parameters `username` and `password` to `check.php`. | 2026-07-31 18:26 +08:00 |
| I2 | verified | Filter-bypass, error-based SQL injection | `'or(1)#` establishes a successful tautology. `updatexml(1,concat(0x7e,<expression>,0x7e),1)` leaks expressions in XPath errors despite keyword filtering. | 2026-07-31 18:30 +08:00 |
| I3 | verified | Extract flag in error-output chunks | `left(password,24)`, `right(password,24)`, and `length(password)=42` reconstruct the full flag. | 2026-07-31 18:32 +08:00 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | Target is `http://6fb2ba677ad56a139506d872.http-ctf2.dasctf.com:80`; challenge type is web and no background hint is supplied. | progress.md | 2026-07-31 18:32 +08:00 |
| M2 | evidence | `check.php` uses user-controlled SQL: `username='` yields MariaDB syntax error. `'or(1)#` logs in while `'or(0)#` fails. | progress.md | 2026-07-31 18:32 +08:00 |
| M3 | evidence | Filter blocks whitespace, comparison symbols, `AND`, `UNION`, `SLEEP`, `BENCHMARK`, `ASCII`, `SUBSTR/SUBSTRING`, `IF`, `BINARY`, and various operators. It allows parentheses, commas, dot, `#`, `OR`, SELECT, FROM, WHERE, UPDATEXML, EXTRACTVALUE, DATABASE, LIKE and REGEXP. | progress.md | 2026-07-31 18:32 +08:00 |
| M4 | evidence | UpdateXML error injection leaked DB `geek`, MariaDB 10.3.18, user `root@localhost`, and table `H4rDsq1(id,username,password)`. | progress.md | 2026-07-31 18:32 +08:00 |
| M5 | fact | The sole target row has username `flag`; password length is 42. LEFT and RIGHT chunk leaks reconstruct `CTF2{042aac0d-0f7a-45a5-91ac-a02979598d6b}`. | progress.md | 2026-07-31 18:32 +08:00 |
| M6 | fact | `branch_001` completed and `branch_002` was killed after flag discovery. | progress.md | 2026-07-31 18:32 +08:00 |
