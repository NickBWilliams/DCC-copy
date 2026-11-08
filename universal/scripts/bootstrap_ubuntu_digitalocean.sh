#!/usr/bin/env bash
# DCC Universal / personal DigitalOcean Droplet bootstrap.
# Run on the owner's Ubuntu 24.04 LTS server via key-authenticated SSH:
# sudo bash scripts/bootstrap_ubuntu_digitalocean.sh
#
# This script installs Docker Engine & Compose from official signed APT.
# It does NOT start the app, open ports, configure credentials or modify SSH.

set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

log() { printf '[dcc-bootstrap] %s\n' "$*"; }
die() { printf '[dcc-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run with sudo/root on your DigitalOcean Droplet."
[[ -r /etc/os-release ]] || die "Cannot determine the operating system."
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] ||
  die "Expected Ubuntu 24.04 LTS, found ${PRETTY_NAME:-unknown}."
[[ $(dpkg --print-architecture) == amd64 ]] ||
  die "Expected the approved x86_64 Droplet architecture."

log "Installing Docker APT repository prerequisites."
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --location \
  https://download.docker.com/linux/ubuntu/gpg \
  --output /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-noble}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

log "Installing Docker Engine, Compose and Buildx."
apt-get update -qq
apt-get install -y --no-install-recommends \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker >/dev/null
docker --version
docker compose version
docker info --format 'Docker server: {{.ServerVersion}}'

# No docker-group user: membership confers root-equivalent power.
# No docker compose up, public ports, UFW change, or password changes.
install -d -m 0700 -o root -g root /opt/dcc-universal

log "Bootstrap complete. No app services exposed."
log "Production still requires a private routing key, approved DNS, and SMTP."
