# Deploy on the Azure VM (SPEC §15, option D)

| | |
|---|---|
| VM | Ubuntu 24.04, `Standard_B2ls_v2` (2 vCPU, 4 GiB RAM), India South Central. At M4 it is resized in place to `Standard_D4as_v4` (4 vCPU, 16 GB; same IP and URL; §9); everything is deleted after 30 Oct 2026 |
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

## 9. M4: move to 4 vCPUs (SPEC §19, open item h)

OCR takes seconds per image, and 2 burstable vCPUs can't index demo uploads in time.

The Azure for Students quota in India South Central (checked by the owner, 17 Oct):
- **FALSv6 family:** 0 vCPUs; the automatic increase was refused.
- **DASv4 family:** 4 vCPUs.
- **Total Regional vCPUs:** 6.

| When | VM | Notes |
|---|---|---|
| M4 → 30 Oct | **resize the current VM in place to `Standard_D4as_v4`** (4 vCPU, 16 GB, non-burstable) | Same IP and URL, no rebuild (9b) |
| only if a support ticket raises the FALSv6 quota **before M4** | a new `Standard_F4als_v6` instead (9c) | NVMe disk controller, so a new VM that takes over the static IP |
| 26 → 30 Oct, F4 path only | `Standard_F8als_v6`, if Total Regional reaches 8 (9d) | In-place resize, same family |
| after 30 Oct 2026 | delete the resource group (9e) | The VM isn't needed after that |

The commands run in PowerShell on the **laptop** with the Azure CLI (`winget install Microsoft.AzureCLI`, then
`az login`). Check the credit balance in the Azure portal (Cost Management + Billing) before each size change.

### 9a. Names and quota

```powershell
az vm list -d -o table                      # the current VM's name and resource group
$RG  = "<resource group>"
$OLD = "<current VM name>"
az vm list-usage --location indiasouthcentral -o table | Select-String -Pattern "DASv4|FALSv6|Total Regional"
```

### 9b. Resize in place to D4as_v4 (the plan)

The resize restarts the VM, so the site is down for a few minutes; don't do it while anyone is using it. The static IP,
the DNS name, the disk and everything on it stay. The new total of 4 vCPUs fits both the DASv4 quota (4) and the
regional one (6), because the 2 B-series vCPUs are released.

```powershell
az vm list-vm-resize-options -g $RG -n $OLD -o table | Select-String "Standard_D4as_v4"
az vm resize -g $RG -n $OLD --size Standard_D4as_v4
```

If `Standard_D4as_v4` isn't in the list, the VM's current hardware cluster can't host it. Run
`az vm deallocate -g $RG -n $OLD`, then the resize, then `az vm start -g $RG -n $OLD`. A static IP survives
deallocation. If the size still isn't offered after deallocating, check the VM's security type (`az vm show -g $RG -n $OLD
--query securityProfile`) against what the size supports.

Check it, then benchmark with the app stopped, and record the results in SPEC §19 (open item h):

```powershell
ssh vault "nproc; free -h"                    # 4 CPUs; the 4 GB swap file (§3) can stay
```

```bash
sudo systemctl stop vault-probe
cd ~/personal-search-vault && source .venv/bin/activate
python bench/compare_ocr.py                   # OCR before/after table; ocr_threads = min(8, CPUs) = 4
python bench/make_bench_corpus.py
VAULT_DATA_DIR=bench/generated/data1 python -m vault index bench/generated/corpus
VAULT_DATA_DIR=bench/generated/data2 python -m vault index bench/generated/corpus
python bench/bench_ocr.py workers=2 ocr_threads=2   # two files OCR'd in parallel
sudo systemctl start vault-probe
```

Also re-run the embedding benchmark (method in SPEC §19). Then run §8 from the laptop.

### 9c. Only if the FALSv6 quota is raised before M4: a new F4als_v6

v6 sizes only support the NVMe disk controller and the current VM uses SCSI, so this is a new VM that takes over the
static IP (and with it the DNS name). Use this path instead of 9b, never after it. During the cutover both VMs exist:
2 + 4 = 6 vCPUs, exactly the regional quota.

Names:

```powershell
$NEW = "vault-v6"
az network public-ip list -g $RG -o table   # the static IP that carries the swarup-vault DNS label
$IP  = "<static public IP name>"
$OLDNIC   = (az vm show -g $RG -n $OLD --query "networkProfile.networkInterfaces[0].id" -o tsv).Split('/')[-1]
$OLDIPCFG = az network nic ip-config list -g $RG --nic-name $OLDNIC --query "[0].name" -o tsv
$SUBNETID = az network nic ip-config list -g $RG --nic-name $OLDNIC --query "[0].subnet.id" -o tsv
$NSGID    = az network nic show -g $RG -n $OLDNIC --query "networkSecurityGroup.id" -o tsv  # empty if the NSG is on the subnet
$OLDDISK  = az vm show -g $RG -n $OLD --query "storageProfile.osDisk.name" -o tsv
$IPZONES  = az network public-ip show -g $RG -n $IP --query "zones" -o tsv                  # empty = not zonal
az vm list-skus --location indiasouthcentral --size Standard_F4als_v6 -o table             # offered here? which zones?
```

Create it with a temporary IP. `--subnet`, `--nsg` and `--zone` reuse what the old VM has:
- **Zone:** a zonal static IP can only attach to a VM in the same zone. Pass `--zone` when `$IPZONES` is a single zone; drop that line when it's empty.
- **Security group:** without `--nsg`, `az vm create` makes a new security group that only opens SSH. If `$NSGID` is empty, pass `--nsg '""'`.

```powershell
ssh-keygen -y -f "$env:USERPROFILE\.ssh\vault-key.pem" | Out-File -Encoding ascii "$env:USERPROFILE\.ssh\vault-key.pub"
az vm create -g $RG -n $NEW --location indiasouthcentral --size Standard_F4als_v6 `
  --image Canonical:ubuntu-24_04-lts:server:latest --disk-controller-type NVMe `
  --admin-username azureuser --ssh-key-values "$env:USERPROFILE\.ssh\vault-key.pub" `
  --subnet $SUBNETID --nsg $NSGID --public-ip-address "$NEW-tmp-ip" --public-ip-sku Standard `
  --zone $IPZONES
$NEWNIC   = (az vm show -g $RG -n $NEW --query "networkProfile.networkInterfaces[0].id" -o tsv).Split('/')[-1]
$NEWIPCFG = az network nic ip-config list -g $RG --nic-name $NEWNIC --query "[0].name" -o tsv
$TMPIP    = az network public-ip show -g $RG -n "$NEW-tmp-ip" --query ipAddress -o tsv
```

If `az` doesn't know `--disk-controller-type`, run `az upgrade`.

Set it up through the temporary IP (`ssh -i "$env:USERPROFILE\.ssh\vault-key.pem" azureuser@$TMPIP`):
- **§2–§6:** as written.
- **§4 (code):** push to it as a second remote, `git remote add vmnew "azureuser@${TMPIP}:vault.git"`, with the key in `GIT_SSH_COMMAND`.
- **§7 (Caddy):** install it, but expect no certificate until the cutover.

Benchmark as in 9b. Then cut over. The DNS label lives on the IP resource, so it moves with it:

```powershell
az network nic ip-config update -g $RG --nic-name $OLDNIC -n $OLDIPCFG --remove publicIpAddress
az network nic ip-config update -g $RG --nic-name $NEWNIC -n $NEWIPCFG --remove publicIpAddress
az network nic ip-config update -g $RG --nic-name $NEWNIC -n $NEWIPCFG --public-ip-address $IP
az network public-ip delete -g $RG -n "$NEW-tmp-ip"
ssh-keygen -R swarup-vault.indiasouthcentral.cloudapp.azure.com   # the host key changed
ssh -o StrictHostKeyChecking=accept-new vault "nproc; uptime"
```

Then finish the cutover:
1. On the VM, run `sudo systemctl reload caddy` and check that the certificate is obtained (§7).
2. Run §8.
3. Run `git remote remove vmnew`.
4. Delete the old VM, disk and NIC. The NSG stays, since the new VM uses it.

```powershell
az vm delete -g $RG -n $OLD --yes
az disk delete -g $RG -n $OLDDISK --yes
az network nic delete -g $RG -n $OLDNIC
```

### 9d. 26 → 30 Oct, F4 path only: F8als_v6 if Total Regional reaches 8

F8 alone needs 8 regional vCPUs, and FALSv6 ≥ 8, with the old VM already deleted. It's the same family, so it's an
in-place resize (it restarts the VM):

```powershell
az vm list-usage --location indiasouthcentral -o table | Select-String -Pattern "FALSv6|Total Regional"
az vm resize -g $RG -n $NEW --size Standard_F8als_v6
```

Benchmark again as in 9b, with `workers=2 ocr_threads=4` for the parallel run. Use F8 only from 26 to 30 Oct.

### 9e. After 30 Oct 2026: delete everything

This removes the VM, disks, IP and DNS name for good. Check first that the group holds nothing else:

```powershell
az resource list -g $RG -o table
az group delete -n $RG --yes
```

## Troubleshooting

- **App:** `systemctl status vault-probe`, `journalctl -u vault-probe -n 100`.
- **No certificate:** check `journalctl -u caddy`, that ports 80 and 443 are open in the Azure network security group, and that the DNS name resolves to `172.198.49.18`.
- **Memory:** `free -h`; `journalctl -k | grep -i oom`.
- **After M4:** point `ExecStart` at `app.py` and add `Environment=VAULT_MODE=demo`.
