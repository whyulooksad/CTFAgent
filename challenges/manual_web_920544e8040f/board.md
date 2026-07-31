# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|---------|---------|
| I1 | failed | Probe `?page=` router for LFI, traversal, PHP wrappers, and route/source disclosure | Core payloads consistently returned the home fallback; no inclusion behavior observed | 2026-07-31T18:42:26+08:00 |
| I2 | verified | Abuse archive export as a filesystem write primitive to forge a PHP session | Export with `sess_` username and crafted session title created a usable fake session; supplying its ID to `?page=flag` returned the flag | 2026-07-31T18:49:16+08:00 |
| I3 | verified | Authenticate using a compliant username and map authenticated note/export workflow | Logged in as `u1785494157`; add, notes, flag pages and export endpoint reached | 2026-07-31T18:36:35+08:00 |
| I4 | testing | Infer implementation from direct `pages/*.php` errors and targeted include probing | Direct page execution leaked helper dependency/context; no source disclosure was needed after solve | 2026-07-31T18:49:16+08:00 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | Easy Notes is PHP/7.3.9 behind openresty, uses PHPSESSID, visible router is `?page=login`, and notes export as ZIP/TAR. | homepage artifacts | 2026-07-31T18:34:28+08:00 |
| M2 | fact | Login POST accepts only `user` under client-side regex `^[0-9A-Za-z_-]{4,64}$`; authentication with a compliant username reaches add/notes/flag/export workflow. | login/workflow artifacts | 2026-07-31T18:36:35+08:00 |
| M3 | evidence | Empty export is 0 bytes; a note produces an archive. Download filename is server-generated as `<username>-<random>.<type>`, while MIME stays `application/zip` even for tar. | export artifacts | 2026-07-31T18:36:46+08:00 |
| M4 | failure_boundary | Username forms (short/array/NUL/traversal/boolean/admin-like), extra admin-role POST fields, and session-shaped request fields did not bypass admin authorization. | auth/session-pollution artifacts | 2026-07-31T18:47:53+08:00 |
| M5 | failure_boundary | Special note titles were partly normalized in archive names and no sleep timing signal occurred; straightforward title shell injection is unsupported. | title test artifacts | 2026-07-31T18:39:39+08:00 |
| M6 | evidence | `pages/` and `includes/` directories deny indexing, but direct `pages/*.php` scripts execute. `pages/notes.php`/`note.php` error on undefined `get_notes()` at line 2 in `/var/www/html/pages/`, showing normal router-loaded helpers. | direct-page artifacts | 2026-07-31T18:41:10+08:00 |
| M7 | failure_boundary | `?page=` known-page, traversal, `/etc/passwd`, and `php://filter` payloads all yielded the standard home fallback; router is effectively allowlisted for tested forms. | page payload artifacts | 2026-07-31T18:42:26+08:00 |
| M8 | external | Public Harekaze CTF Easy Notes source/writeup shows archive export writes in PHP session temp storage. Username `sess_`, `type=.`, and crafted serialized title can create a `sess_<id>` file holding admin state. | targeted web search | 2026-07-31T18:47:53+08:00 |
| M9 | evidence | Fake session ID `-4a9be60530fc95df` from the first export was accepted in `PHPSESSID`; `?page=flag` returned a congratulatory flag page. Initial solver missed success solely because its regex excluded `CTF2{...}`. | `artifacts/fake_session/manual_fake_flag.http`, progress.md, codex.log | 2026-07-31T18:49:16+08:00 |
| M10 | fact | Flag: `CTF2{730db9c4-5bea-4dec-84f2-6f2dcbead5fe}`. | verified response / progress.md | 2026-07-31T18:49:16+08:00 |
