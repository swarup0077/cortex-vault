# Deploy on the Azure VM (SPEC §15, option D)

| | |
|---|---|
| VM | Ubuntu 24.04, `Standard_B2ls_v2` (2 vCPU, 4 GiB RAM), India South Central |
| User | `azureuser`, key `C:\Users\swaru\.ssh\vault-key.pem` |
| Address | `172.198.49.18` · `swarup-vault.indiasouthcentral.cloudapp.azure.com` |
| Open ports | 22, 80, 443 (auto-shutdown off) |
| Layout on the VM | bare repo `~/vault.git` → checkout `~/personal-search-vault` → systemd `vault-probe` on `127.0.0.1:7860` → Caddy on 443 |

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
git init --bare ~/vault.git
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

The unit ([vault-probe.service](vault-probe.service)) runs `probe/app.py` as `azureuser`, binds `127.0.0.1:7860`, disables Gradio analytics, and keeps the model cache in `data/models/` so it survives reboots.

```bash
sudo cp deploy/vault-probe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vault-probe
journalctl -u vault-probe -f      # first start downloads bge-small; Ctrl+C when it's up
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7860/    # 200
```

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
curl.exe -sI https://swarup-vault.indiasouthcentral.cloudapp.azure.com/
.venv\Scripts\python -c "from gradio_client import Client; print(Client('https://swarup-vault.indiasouthcentral.cloudapp.azure.com/', verbose=False).predict('hello', api_name='/embed'))"
```

Expect `dim: 384` and `l2_norm: 1.0`. Then open the URL on a phone.

## Updating after a change

On the **laptop**:

```powershell
git push vm main
ssh vault "cd ~/personal-search-vault && git pull --ff-only && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart vault-probe"
```

If `deploy/vault-probe.service` or `deploy/Caddyfile` changed, copy it again on the VM:

```bash
sudo cp deploy/vault-probe.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart vault-probe
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

## Troubleshooting

- **App:** `systemctl status vault-probe`, `journalctl -u vault-probe -n 100`.
- **No certificate:** check `journalctl -u caddy`, that ports 80 and 443 are open in the Azure network security group, and that the DNS name resolves to `172.198.49.18`.
- **Memory:** `free -h`; `journalctl -k | grep -i oom`.
- **After M4:** point `ExecStart` at `app.py` and add `Environment=VAULT_MODE=demo`.
