# Board

## Ideas

| ID | Status | Idea | Result | Updated |
|----|--------|------|---------|--------|
| I1 | failed | Map application routes, static assets and accidental source/deployment exposure | branch_001 completed: only `/static/js/jquery.min.js` and `/static/background.jpg` beyond known pages; no source/config/flag/hidden route disclosure. | 2026-08-01 20:47 +08:00 |
| I2 | blocked | Reach unsafe Python pickle deserialization through Redis-backed server-side session data | Historical chain is technically confirmed but unavailable: Redis write needs an authenticated JumpServer connection token that is not exposed by the target. | 2026-08-01 20:57 +08:00 |
| I3 | failed | Determine whether registration/login inputs can influence session serialization or another injection sink | SSTI/XSS/pickle usernames/passwords render or authenticate as ordinary data; no execution or file side effect. | 2026-08-01 20:18 +08:00 |
| I4 | failed | Forge Flask session with weak/default signer secret | 336 candidate signer/salt/key-derivation combinations produced no matching signature. | 2026-08-01 20:18 +08:00 |
| I5 | failed | Overwrite another user's Redis session through username key collision | Registering `session:<target_sid>` did not change the target session response. | 2026-08-01 20:18 +08:00 |
| I6 | blocked | Obtain CTF² target metadata/forwarder connection details to reach an additional TCP service | `:9998` is TLS `target-forwarder`; without platform auth/target metadata, SNI variants return `Target not found`. | 2026-08-01 20:40 +08:00 |
| I7 | failed | Obtain Redis/session write or secret through HTTP edge parsing, cache, proxy or static-file quirks | branch_003 tested method/content-type/cookie/header/static/debug/cache boundaries without finding a primitive or leak. | 2026-08-01 20:37 +08:00 |
| I8 | failed | Authenticate to 63790 by guessing/deriving JumpServer proxy token or reaching Core through target | branch_004 confirmed syntax but token value is server-random; target exposes no Core API or JumpServer auth/session. | 2026-08-01 20:40 +08:00 |
| I9 | blocked | Use public CTF² practice metadata to map target to a challenge and acquire legitimate target details | Public API is readable but initial searches did not map current app; platform self-registration attempts were forbidden. | 2026-08-01 20:57 +08:00 |

## Memory

| ID | Kind | Content | Source | Updated |
|----|------|---------|--------|---------|
| M1 | fact | Original target was `http://9bc98ac1fbf88cb6911b31bb.http-ctf2.dasctf.com:80`; it is now expired/stopped. | progress.md / codex.log | 2026-08-01 20:57 +08:00 |
| M2 | evidence | A current GET to the original URL returns openresty `404 Target not found`, not the Flask challenge page. | codex.log | 2026-08-01 20:57 +08:00 |
| M3 | fact | Before expiry, the app was titled `Deserialization Login`, issued signed UUID-like session IDs, and authenticated `/` echoed the username with a Redis hint. | progress.md | 2026-08-01 20:57 +08:00 |
| M4 | external | Same-challenge write-up describes `session:<cookie UUID>` storing Python pickle and triggering unpickling after Redis overwrite. | progress.md / external write-up | 2026-08-01 20:40 +08:00 |
| M5 | evidence | `117.21.200.176:63790` is JumpServer/Magnus Redis DB Client proxy, not direct challenge Redis. AUTH requires `<connection_token.id>@<connection_token.value>`. | branch_004 result | 2026-08-01 20:40 +08:00 |
| M6 | boundary | Required connection-token value is server-random; Core secret endpoint requires privilege and target HTTP/port 81 do not expose Core endpoints. | branch_004 result | 2026-08-01 20:40 +08:00 |
| M7 | boundary | branch_002 marked pickle exploitation infeasible without Redis AUTH/write access; branch_003 ruled out HTTP edge/cookie/cache/static primitives. | branch results | 2026-08-01 20:57 +08:00 |
| M8 | boundary | Weak signer guesses, ordinary route enum, input pickle probes, key collisions, `@` variations and SNI guessing yielded no primitive. | progress.md | 2026-08-01 20:57 +08:00 |
| M9 | evidence | CTF² API base is `/api/v1`; public practice/challenge APIs are readable, but app mapping was not found and self-registration was forbidden. | progress.md | 2026-08-01 20:57 +08:00 |
| M10 | recovery | On a restarted target, first verify liveness and repeat baseline recon; do not assume session UUID, mapped ports, or proxy behavior persists. | Hermes | 2026-08-01 20:57 +08:00 |
