# Installing on Windows (Docker Desktop)

This runs the complete branch server on a Windows 10/11 PC: database, app, cloud-sync worker, automatic
encrypted backups and HTTPS for the phones and tablets in the restaurant. No Ubuntu or Linux knowledge needed.

## What you need
* Windows 10 or 11 (64-bit), 8 GB RAM recommended, an SSD, ideally a UPS (battery backup).
* **Docker Desktop** with the default WSL 2 engine, running in **Linux containers** mode.
* **Git for Windows** (<https://git-scm.com/download/win>), or download the repository as a ZIP from GitHub.
* For phones/tablets: the PC and the devices on the **same Wi-Fi/network** (not a "guest" network that isolates devices).

> Docker Desktop is free for small businesses (fewer than 250 employees **and** less than US $10 million annual
> revenue); larger organisations need a paid Docker subscription.

## 1. Get the code
Open **PowerShell** and run (any folder **outside OneDrive** — OneDrive would upload your passwords and can
interfere with the database files):
```powershell
cd C:\
```
```powershell
git clone https://github.com/fresh-prog/inventory-management-system.git KitchenLedger
```
(ZIP instead: extract it to `C:\KitchenLedger`.)

## 2. Run the setup
In File Explorer open `C:\KitchenLedger`, **right-click `Setup-Windows.cmd` → Run as administrator**
(administrator is only needed once, to let phones and tablets through the Windows Firewall).

To send off-site backup copies to a USB disk or network share, run it from PowerShell instead:
```powershell
C:\KitchenLedger\Setup-Windows.cmd -OffsitePath E:\RestaurantBackups
```

The script:
1. checks Docker Desktop (and starts it if needed);
2. creates `.env` and the `secrets\` folder: sign-in secret, database password, **backup passphrase**;
3. detects the PC's network address;
4. builds and starts everything (first time: 5–15 minutes);
5. waits until the system reports healthy;
6. trusts the HTTPS certificate on this PC (Windows asks you to confirm) and opens the firewall.

It is safe to run again at any time: existing passwords and data are never replaced.

At the end it prints the addresses, for example:
```
   On this computer:        https://localhost/
   Phones/tablets (Wi-Fi):  https://192.168.1.20/
```

**Immediately copy `secrets\backup_passphrase.txt`, the whole `secrets` folder and `.env` somewhere off
this PC** (password manager + a USB stick kept elsewhere). Without the passphrase the backups cannot be
restored; without the folder a new PC cannot take over.

## 3. First use
Open `https://localhost/`. The **setup screen** appears once: enter the branch name, currency (UGX: 0 decimals;
ERN: 2) and the owner account. Then **Settings → Users & roles** to add staff.

## 4. Phones and tablets
1. Give the PC a **fixed address** (in the router: "DHCP reservation"), so the address does not change. If it
   changes, run the setup again: it updates the address.
2. Install the certificate once per device, so the browser trusts the server and the app can work offline.
   The file is `C:\KitchenLedger\restaurant-root.crt` (send it by e-mail, WhatsApp or USB):
   * **Android:** Settings → Security → Encryption & credentials → Install a certificate → **CA certificate**.
   * **iPhone/iPad:** open the file → Settings → *Profile Downloaded* → Install; then Settings → General →
     About → **Certificate Trust Settings** → turn it on.
3. Open `https://<PC address>/` and choose **Add to Home Screen**.

## 5. Keep it running
* Docker Desktop → Settings → General → **Start Docker Desktop when you sign in**. The containers restart by
  themselves after a reboot once Docker Desktop is running.
* Windows: set the PC to sign in automatically (or leave it signed in), **never sleep** (Settings → System →
  Power), and set Windows Update **active hours** to the restaurant's opening hours.
* A UPS protects against power cuts. (Even without one, saved data survives a power cut: tested.)

## Everyday tasks (PowerShell in `C:\KitchenLedger`)
| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Stop / start | `docker compose stop` / `docker compose start` (or the Docker Desktop window) |
| Backup now | `docker compose exec backup /scripts/backup.sh /backups` |
| Check the newest backup | `docker compose exec backup sh -c '/scripts/verify_restore.sh "$(ls -d /backups/restaurant-* \| tail -1)"'` |
| Backup log | `docker compose logs backup` |
| Update to a new version | Backup now, then `git pull`, then run `Setup-Windows.cmd` again. Afterwards open **Reports → Data checks**. |

## Troubleshooting
| Problem | Fix |
|---|---|
| "Docker Desktop is not running" | Open Docker Desktop and wait for *Engine running*. If it asks to update WSL, run `wsl --update` in PowerShell. |
| "Windows-containers mode" | Right-click the Docker whale icon → **Switch to Linux containers**. |
| Port 443 / 80 already in use | Another program (IIS, Skype, VMware, another web server) uses it. Stop that program or service. |
| Phone cannot open the address | Same Wi-Fi as the PC? Not a guest network? Run the setup **as administrator** once (firewall). In Windows, the network must be **Private** (Settings → Network → your network → Private). |
| Browser warns about the certificate | Install `restaurant-root.crt` on that device (section 4). |
| Setup stops with a message | The message says what to do; the lines above it show Docker's own output. |

## What was and was not tested
The Docker setup itself (images, HTTPS by name and IP, backups with restore tests, restarts, crash recovery,
cloud sync) was built and tested end to end on a Linux Docker engine, the same engine Docker Desktop runs
inside WSL 2 — including Windows-style loose file permissions on the secret files
([DOCKER-VERIFICATION.md](DOCKER-VERIFICATION.md)). **The PowerShell setup script could not be executed in
the development environment (no Windows available)**: it is written for Windows PowerShell 5.1 and PowerShell 7.
Please report any message it shows on the first run.
