# Deploying 0bt on Hetzner Cloud (CX23)

This is the recommended production path. A CX23 (€3.49/mo, 2 vCPU / 4 GB RAM / 40 GB SSD / **20 TB egress included**) is enough to host a small-to-mid file host indefinitely.

The whole flow is generic Linux + Docker; nothing here is Hetzner-locked. Substitute another provider (Scaleway, OVH, DigitalOcean, …) and the steps are identical past the first command.

---

## 0. Prerequisites

- A domain name you control. We'll use `files.example.com` throughout.
- A Hetzner Cloud account and an API token (Project → Security → API Tokens). Optional: install the `hcloud` CLI locally.
- An SSH key uploaded to Hetzner.

## 1. Provision the server

Web UI: New Project → New Server → CX23 / Debian 13 / your SSH key / **enable Backups (€1.30/mo extra, worth it)** / pick a Cloud Firewall (or attach one in step 3).

Or via CLI:

```bash
hcloud server create \
  --name 0bt \
  --type cx23 \
  --image debian-13 \
  --ssh-key your-key-name \
  --location hel1 \
  --enable-backup
hcloud server ip 0bt   # note the public IPv4
```

## 2. DNS

Point `files.example.com` at the server's public IPv4 with an `A` record. (And `AAAA` to IPv6 if you have one.) Wait for it to propagate — `dig +short files.example.com` should return the right address before continuing.

## 3. Firewall

Open these inbound ports:

| Port      | Protocol | Why                              |
|-----------|----------|----------------------------------|
| 22        | TCP      | SSH                              |
| 80        | TCP      | HTTP (Let's Encrypt + redirect)  |
| 443       | TCP+UDP  | HTTPS (UDP for HTTP/3)           |
| 51413     | TCP+UDP  | BitTorrent peer connections      |

Either configure a Hetzner Cloud Firewall (Project → Security → Firewalls) and attach it to the server, or use `ufw` on the server itself.

## 4. SSH in and bootstrap

```bash
ssh root@<server-ip>
```

Then on the server:

```bash
# Update + minimal hardening
apt update && apt full-upgrade -y
apt install -y ca-certificates curl git ufw

# (Optional) ufw if you didn't use a Cloud Firewall
ufw allow 22/tcp
ufw allow 80,443/tcp
ufw allow 443/udp
ufw allow 51413
ufw enable

# Docker (official convenience script)
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

## 5. Deploy the app

```bash
git clone https://github.com/audiodude/0bt.git /opt/0bt
cd /opt/0bt
cp .env.example .env

# Generate a strong transmission password and bake it in
TR_PASS=$(openssl rand -hex 16)
sed -i "s|^TRANSMISSION_RPC_PASSWORD=.*|TRANSMISSION_RPC_PASSWORD=$TR_PASS|" .env

# Set your domain, base URL, and admin email
sed -i "s|^FHOST_BASE_URL=.*|FHOST_BASE_URL=https://files.example.com|" .env
sed -i "s|^CADDY_DOMAIN=.*|CADDY_DOMAIN=files.example.com|" .env
sed -i "s|^CADDY_ADMIN_EMAIL=.*|CADDY_ADMIN_EMAIL=you@example.com|" .env

docker compose --profile caddy up -d --build
```

That's it. Wait a minute for Caddy to issue the cert, then:

```bash
curl -F "file=@/etc/hosts" https://files.example.com
```

You should get back three lines (HTTP URL, `.torrent` URL, magnet URI).

## 6. Verify the BitTorrent path

From any other machine:

```bash
# Grab the .torrent
curl -O https://files.example.com/<short>.torrent
# Add it to a BT client (transmission-cli, qBittorrent, etc.)
transmission-cli <short>.torrent --download-dir ./out
```

Confirm the file downloads and that `transmission-cli` reports a peer at `<your-server-ip>:51413`. If it stalls, double-check the firewall rules for 51413/TCP+UDP.

## 7. Operations

**Logs.** `docker compose logs -f app transmission caddy`.

**Updates.**
```bash
cd /opt/0bt && git pull
docker compose --profile caddy up -d --build
```

**Backups.** Hetzner's daily snapshot covers everything. If you want offsite as well, `restic` or `rclone` the `/var/lib/docker/volumes/0bt_app_data/` directory to S3-compatible storage (Backblaze B2, Cloudflare R2). That single volume holds all uploaded files + the SQLite DB.

**Pruning expired files.** A cron job:

```bash
cat >/etc/cron.d/0bt-prune <<'EOF'
17 3 * * * root cd /opt/0bt && docker compose exec -T app python3 scripts/prune.py
EOF
```

**Boot persistence.** `restart: unless-stopped` on every service means the stack comes back automatically after reboot. No systemd unit required.

**Monitoring.** A 5-minute external check on `https://files.example.com/healthz` (UptimeRobot, healthchecks.io, anything) catches both app and transmission failures — `/healthz` reports the state of both.

## 8. Costs (rough monthly, 2026)

| Item                                     | Cost              |
|------------------------------------------|-------------------|
| Hetzner CX23                             | €3.49             |
| CX23 backups (snapshots)                 | €1.30             |
| Domain (.com average)                    | €1                |
| **Total**                                | **~€6 / mo**      |
| Egress over the included 20 TB           | €1 / TB           |

Compare with a hyperscaler: the same 20 TB of egress on AWS, GCP, or Azure costs roughly **$1,800/mo**. That's the reason this guide exists.
