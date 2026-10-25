#!/usr/bin/env bash
# Prepare a fresh Ubuntu 22.04/24.04 server (x86_64 or ARM64, e.g. an Oracle
# Cloud Always Free VM) for ClaimLab: installs Docker Engine + the Compose
# plugin, opens ports 80/443 in the host firewall, and adds swap on small
# machines. Safe to run more than once.
#
# Usage (from the repository root):  sudo bash deploy/setup-server.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo: sudo bash deploy/setup-server.sh" >&2
  exit 1
fi
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
  echo "Warning: tested on Ubuntu; continuing on ${PRETTY_NAME:-an unknown OS}." >&2
fi

echo "==> Installing base packages"
apt-get update -y
apt-get install -y ca-certificates curl git openssl

echo "==> Installing Docker Engine + Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
TARGET_USER="${SUDO_USER:-ubuntu}"
if id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER"
fi

echo "==> Opening ports 80 (HTTP, certificate challenge) and 443 (HTTPS)"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 443/udp
fi
# Oracle Cloud Ubuntu images ship iptables rules that reject everything except
# SSH. Open the ports now and persist them in the saved ruleset (edited in
# place rather than re-saving, so Docker's own dynamic rules are not persisted).
if command -v iptables >/dev/null 2>&1; then
  for spec in "tcp 80" "tcp 443" "udp 443"; do
    set -- $spec
    if ! iptables -C INPUT -p "$1" --dport "$2" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null; then
      iptables -I INPUT 1 -p "$1" --dport "$2" -m conntrack --ctstate NEW -j ACCEPT
    fi
    RULES=/etc/iptables/rules.v4
    LINE="-A INPUT -p $1 -m state --state NEW -m $1 --dport $2 -j ACCEPT"
    if [ -f "$RULES" ] && ! grep -qF -- "$LINE" "$RULES"; then
      sed -i "0,/^-A INPUT -j REJECT/s//$LINE\n&/" "$RULES"
    fi
  done
fi

echo "==> Checking memory"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 3500 ] && [ -z "$(swapon --show --noheadings)" ]; then
  echo "    ${MEM_MB} MB RAM: adding a 2 GB swap file (needed to build the images)"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

cat <<EOF

Server ready. Next steps (as $TARGET_USER, after logging out and back in so the
docker group applies):
  bash deploy/configure.sh --domain <your-domain>
  cd deploy && docker compose up -d --build
See deploy/README.md for the full guide.
EOF
