# Deploy on the Azure VM (SPEC §15, option D)

| | |
|---|---|
| VM | Ubuntu 24.04, `Standard_D4as_v4` (4 vCPU, 16 GB; resized in place from `Standard_B2ls_v2` on 18 Oct, §9), India South Central; everything is deleted after 30 Oct 2026 |
| User | `azureuser`, key `C:\Users\swaru\.ssh\vault-key.pem` |
| Address | `172.198.49.18` · `swarup-vault.indiasouthcentral.cloudapp.azure.com` |
| Open ports | 22, 80, 443 (auto-shutdown off) |
| Layout on the VM | bare repo `~/vault.git` → checkout `~/personal-search-vault` → systemd `vault` (M0: `vault-probe`) on `127.0.0.1:7860` → Caddy on 443 |

Commands marked **laptop** run in PowerShell on Windows; everything else runs on the VM over SSH.

## 1. SSH from Windows (laptop)

OpenSSH refuses a private key that other Windows users can read, so restrict it to your account:

```powershell
icacls "$env:USERPROFILE\.ssh\vault-key.pem" /inheritance:r /grant:r "${env:USERNAME}:R"
```

Add a host alias to `%USERPROFILE%\.ssh\config`:

```
Host vault
    HostName swarup-vault.indiasouthcentral.cloudapp.azure.com
    User azureuser
    IdentityFile ~/.ssh/vault-key.pem
    IdentitiesOnly yes
    ServerAliveInterval 60
```

Test it (the first connection records the host key):

```powershell
ssh -o StrictHostKeyChecking=accept-new vault "uname -a; lsb_release -ds; python3 --version"
```

## 2. Base packages

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git
```

Ubuntu 24.04's `python3` is 3.12, which matches the project.

## 3. 4 GB swap file

4 GiB of RAM is tight once the OCR, CLIP and reranker models are loaded, so add swap:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h    # Swap: 4.0Gi
```

## 4. Code: bare repo on the VM

On the VM:

```bash
git init --bare -b main ~/vault.git    # -b main so clones check out main
```

On the **laptop**, in the project folder:

```powershell
git remote add vm vault:vault.git
git push vm main
```

On the VM:

```bash
git clone ~/vault.git ~/personal-search-vault
```

## 5. Python venv and install

```bash
cd ~/personal-search-vault
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

## 6. systemd service

The unit ([vault.service](vault.service)) runs `app.py` as `azureuser` with `VAULT_MODE=demo`, binds `127.0.0.1:7860`, disables Gradio analytics, and keeps the model cache in `data/models/` so it survives reboots. It serves the committed `demo_index/` read-only; the server never indexes `corpus/` itself. Uploads go to per-session copies under `/tmp/vault_sessions/`, wiped 60 minutes after the session's last search or upload and on every start. A restart takes about 5 s: the app gives open requests `SHUTDOWN_GRACE_S` (5 s) and then stops (open item aa). Both units are enabled, so after a reboot the site is back by itself: 20 s from `sudo systemctl reboot` to the home page on 19 Oct.

```bash
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"    # 3.44+ for FTS5 secure-delete (open item h)
sudo cp deploy/vault.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vault
journalctl -u vault -f      # the first start downloads bge-small and CLIP into data/models/; "models warm" when ready
curl -s -o /dev/null -w "%{http_code}
" http://127.0.0.1:7860/    # 200
```

Until 18 Oct (M0–M3) the VM ran the deploy probe instead (`probe/app.py`, unit `vault-probe`); both were removed after the switch (`sudo systemctl disable --now vault-probe` and deleting its unit file).

## 7. Caddy for HTTPS on 443

Install from Caddy's official apt repo (https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy
```

Use the repo's [Caddyfile](Caddyfile), which reverse-proxies the DNS name to `127.0.0.1:7860`. Caddy gets the certificate on its own, which needs ports 80 and 443 open:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
journalctl -u caddy --since "5 min ago" | grep -i "certificate obtained"
```

## 8. Check it

From the **laptop**:

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" "https://swarup-vault.indiasouthcentral.cloudapp.azure.com/?q=error%200x80070005"
curl.exe -s -o NUL -w "%{http_code} %{content_type}\n" https://swarup-vault.indiasouthcentral.cloudapp.azure.com/thumb/1
.venv\Scripts\python deploy\check.py https://swarup-vault.indiasouthcentral.cloudapp.azure.com/ "error 0x80070005" "Screenshot 2026-10-12 101530.png"
```

Expect `200`, `200 image/webp` and `OK "error 0x80070005": #1 Screenshot 2026-10-12 101530.png (50 results, …)`.
Then open the URL on a phone.

`deploy/check.py` sends the search the way the page does: the search box's submit event with the query as event data
(`{q, scope, select}`), through Gradio's queue. Since the M6 re-skin the `search` endpoint takes no inputs, so a
`gradio_client` call with positional inputs fails (open item ab).

## Updating after a change

On the **laptop**:

```powershell
git push vm main
ssh vault "cd ~/personal-search-vault && git pull --ff-only && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart vault"
```

If `deploy/vault.service` or `deploy/Caddyfile` changed, copy it again on the VM:

```bash
sudo cp deploy/vault.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart vault
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

## 9. M4: resize to 4 vCPUs (SPEC §19, open item h)

OCR takes seconds per image, and 2 burstable vCPUs can't index demo uploads in time.

The Azure for Students quota in India South Central:
- **Total Regional vCPUs:** 6.
- **DASv4 family:** 4.
- **FALSv6 and DASv5 families:** 0. This subscription can't get quota increases at all; support's answer is to upgrade to Pay-As-You-Go, which we won't do.

| When | VM |
|---|---|
| M4 → 30 Oct 2026 | the current VM, **resized in place from `Standard_B2ls_v2` to `Standard_D4as_v4`** (4 vCPU, 16 GB, non-burstable): Stop → Size → Start. Same disk, IP, NSG and host key, so no new VM and no rebuild |
| after 30 Oct 2026 | delete the resource group (9c) |

The commands run in PowerShell on the **laptop** with the Azure CLI (`winget install Microsoft.AzureCLI`, then
`az login`). Check the credit balance in the Azure portal (Cost Management + Billing) before the resize.

### 9a. Names and quota

```powershell
az vm list -d -o table                      # the current VM's name and resource group
$RG = "<resource group>"
$VM = "<current VM name>"
az vm list-usage --location indiasouthcentral -o table | Select-String -Pattern "DASv4|Total Regional"
```

### 9b. Stop → Size → Start, then benchmark

The VM keeps its disk, static IP (and DNS name), NSG and SSH host key, so there's no new VM: no zone, IP or `known_hosts`
steps. The site is down for the few minutes this takes; don't do it while anyone is using it. The new total of 4 vCPUs
fits both the DASv4 quota (4) and the regional one (6), because the 2 B-series vCPUs are released.

```powershell
az vm deallocate -g $RG -n $VM                          # Stop (deallocate, so the size isn't tied to the current hardware)
az vm resize -g $RG -n $VM --size Standard_D4as_v4      # Size
az vm start -g $RG -n $VM                               # Start
```

If the resize is refused, the error says why. A quota error means the vCPU numbers above have changed. A size/feature
error means checking the VM's security type (`az vm show -g $RG -n $VM --query securityProfile`) against what the size
supports.

Check it:

```powershell
ssh vault "nproc; free -h"                    # 4 CPUs; the 4 GB swap file (§3) can stay
```

Benchmark with the app stopped, and record the results in SPEC §19 (open item h):

```bash
sudo systemctl stop vault
cd ~/personal-search-vault && source .venv/bin/activate
python bench/compare_ocr.py                   # OCR before/after table; ocr_threads = min(8, CPUs) = 4
python bench/make_bench_corpus.py
VAULT_DATA_DIR=bench/generated/data1 python -m vault index bench/generated/corpus
VAULT_DATA_DIR=bench/generated/data2 python -m vault index bench/generated/corpus
python bench/bench_ocr.py workers=2 ocr_threads=2   # two files OCR'd in parallel, all 4 CPUs
sudo systemctl start vault
```

Also re-run the embedding benchmark (method in SPEC §19). Then run §8 from the laptop.

### 9c. After 30 Oct 2026: delete everything

This removes the VM, disks, IP and DNS name for good. Check first that the group holds nothing else:

```powershell
az resource list -g $RG -o table
az group delete -n $RG --yes
```

## Troubleshooting

- **App:** `systemctl status vault`, `journalctl -u vault -n 100`.
- **No certificate:** check `journalctl -u caddy`, that ports 80 and 443 are open in the Azure network security group, and that the DNS name resolves to `172.198.49.18`.
- **Memory:** `free -h`; `journalctl -k | grep -i oom`.
