# Docker installation — verification report (v0.4.1)

Date: 2026-11-22. Engine: Docker 29.10 (Linux, containerd snapshotter) with Compose v5.7, the same Linux engine
Docker Desktop runs inside WSL 2 on Windows. Both setups were installed from a clean copy of the repository
using the files exactly as shipped.

**Only test-environment adjustments** (kept out of the repository): the development sandbox reaches the internet only
through an inspecting proxy, so the image builds were given that proxy and its certificate; and the test cloud used
Caddy's internal certificate on port 8443 (no public DNS name was available), which the branch was told to trust.

## Results
| # | Check | Result |
|---|---|---|
| 1 | Build both images (app 307 MB; backup 661 MB) | ✅ |
| 2 | `docker compose up -d`: database → migrations → app, sync worker, backups, HTTPS | ✅ ready in ≈ 8 s |
| 3 | HTTPS with **full certificate verification** at `https://localhost/` | ✅ |
| 4 | HTTPS by the **LAN IP address** (as phones use it), certificate verified, no DNS | ✅ after fix F1 |
| 5 | HTTP → HTTPS redirect; monitoring endpoint needs the token; release number reported (not `dev`) | ✅ after fix F4 |
| 6 | Secret files with **Windows-style loose permissions** (mode 666), CRLF tolerated | ✅ after fix F2 |
| 7 | Scheduled backup + **daily restore test** (checksums, decrypt, restore, amcheck, integrity SQL, documents) | ✅ after fix F3 |
| 8 | The 12 browser flows (desktop, phone, tablet, offline drafts) over HTTPS through Caddy, with the certificate installed as the guide says — **0 console errors** (without it, the browser refuses the offline service worker) | ✅ |
| 9 | Full shutdown and start (`down`/`up`, as after a reboot): data intact, data checks pass | ✅ |
| 10 | Database crash (`kill -9` of PostgreSQL): container restarts, app recovers, data checks pass | ✅ |
| 11 | Cloud setup (`docker-compose.cloud.yml`): admin created by CLI, **MFA enrolment**, branch created | ✅ |
| 12 | Branch connects to the cloud over HTTPS; initial copy; every table identical on both sides | ✅ |
| 13 | Cloud unreachable: branch keeps saving (5 records), shows "offline · 5 waiting"; cloud back ⇒ in sync in ≈ 12 s, identical | ✅ |
| 14 | Cloud backup + restore test, including integrity checks of **each branch replica** | ✅ after fix F5 |
| 15 | Disaster-recovery procedure A exactly as documented: restore an older backup over the live database ⇒ data checks pass | ✅ |
| 16 | Procedure A step 5 (`restore-from-cloud`): a change the cloud had confirmed after that backup is **recovered** | ✅ |
| 17 | Manual commands from the guides (`docker compose exec backup …` backup and verify) | ✅ after fix F6 |

## Defects found by this test and fixed
| | Defect | Impact | Fix |
|---|---|---|---|
| F1 | Caddy served only `localhost` and `SERVER_NAME`; opening `https://<server-ip>/` (what the guide told staff to do) failed the TLS handshake | Phones/tablets could not connect by IP | Caddy also serves `SERVER_IP` and uses it as the default for clients that send no name |
| F2 | Docker Desktop mounts secret files readable by everyone; PostgreSQL's client then **ignores** the password file | Backups would fail on Windows/macOS | Entrypoint makes a private copy (mode 600, CR stripped); also used by `docker compose exec` |
| F3 | Password-file line only covered the `restaurant` database; the restore test creates scratch databases and waited for a password **forever** (CPU spinning) | No restore test ever completed in Docker | Password applies to all databases (`*`); every database command is non-interactive (fails instead of waiting) |
| F4 | Images were tagged and reported as `dev` | Wrong version in health/metrics; rollback tags meaningless | Setup records `KL_VERSION` from `VERSION` |
| F5 | The cloud had **no backup service** (contrary to the DR guide), and the restore test assumed a branch database | Cloud users/settings unprotected; cloud restore test failed | Backup service added to the cloud; restore test detects a cloud database and checks every branch replica |
| F6 | `RESTORE_TEST_FAILED` was written but nothing read it and nothing cleared it | A failed restore test raised no alert (or would alert forever) | Metric `kl_restore_test_failed`, alert `RestoreTestFailed`, cron health check; cleared by the next passing test |
| F7 | Error responses showed "Reference: -" instead of the request id (also missing from the server log) | Support could not match a user's error to the log | Request id carried to the outermost error handler; regression test (fails on the old code) |
| F8 | No `.gitattributes`: Git for Windows would convert shell scripts to CRLF, which breaks them inside Linux containers | Backup container would not start on a Windows checkout | `.gitattributes` forces LF; the backup image also strips CR and sets the executable bit |
| F9 | The backup image downloaded packages from the Alpine mirror at build time | Build fails behind restrictive firewalls | Based on `postgres:16` (Debian), which already contains every tool needed |

## Not verified here
* **The Windows setup script (`Setup-Windows.cmd` / `scripts/windows/setup.ps1`) has not been run on Windows**
  (no Windows machine available). Everything it starts was verified above.
* Real phones and tablets on a restaurant Wi-Fi (the IP access and certificate were verified with a browser and `openssl`).
* A public cloud domain with an automatic (Let's Encrypt) certificate.
