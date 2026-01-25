#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

DRY_RUN=0
SKIP_UPGRADE=0
ALLOW_PORTS=()

usage() {
    cat <<'USAGE'
Usage: harden_server.sh [OPTIONS]

Hardens an Ubuntu server with basic security defaults.

Options:
    --allow PORT[/proto]   Allow additional inbound port(s) via UFW. Repeatable.
    --skip-upgrade         Skip apt upgrade step.
    --dry-run              Print actions without making changes.
    -h, --help             Show this help message.

Examples:
    ./harden_server.sh
    ./harden_server.sh --allow 80/tcp --allow 443/tcp
USAGE
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --allow)
            ALLOW_PORTS+=("$2")
            shift 2
            ;;
        --skip-upgrade)
            SKIP_UPGRADE=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Error: Run this script as root (e.g. sudo ./harden_server.sh)."
    exit 1
fi

log() {
    printf '[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '+ %q ' "$@"
        echo
        return 0
    fi
    "$@"
}

ensure_pkg() {
    local pkg=$1
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        run apt-get install -y --no-install-recommends "$pkg"
    fi
}

log "Detecting SSH port"
SSH_PORT=""
if command -v sshd >/dev/null 2>&1; then
    SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}')"
fi
SSH_PORT="${SSH_PORT:-22}"
log "Using SSH port: ${SSH_PORT}"

export DEBIAN_FRONTEND=noninteractive

if [[ $SKIP_UPGRADE -eq 0 ]]; then
    log "Updating packages"
    run apt-get update
    run apt-get -y upgrade
else
    log "Skipping apt upgrade"
fi

log "Installing hardening packages"
run apt-get install -y --no-install-recommends ufw fail2ban unattended-upgrades
ensure_pkg openssh-server

log "Configuring unattended upgrades"
cat <<'EOF_AUTO' > /etc/apt/apt.conf.d/20auto-upgrades
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF_AUTO

cat <<'EOF_UU' > /etc/apt/apt.conf.d/52unattended-upgrades-hardening
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF_UU

log "Applying sysctl hardening"
cat <<'EOF_SYSCTL' > /etc/sysctl.d/99-hardening.conf
# Basic network hardening
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.log_martians = 1

# Kernel hardening
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1

# Filesystem hardening
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
EOF_SYSCTL
run sysctl --system >/dev/null

log "Configuring SSH hardening"
cat <<'EOF_SSH' > /etc/ssh/sshd_config.d/99-hardening.conf
# Spawnm hardening defaults
PasswordAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
UsePAM yes
PermitEmptyPasswords no
X11Forwarding no
LoginGraceTime 30
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF_SSH

log "Validating SSH configuration"
run sshd -t
run systemctl reload ssh

log "Configuring UFW firewall"
run ufw default deny incoming
run ufw default allow outgoing
run ufw allow "${SSH_PORT}/tcp"
for port in "${ALLOW_PORTS[@]}"; do
    run ufw allow "$port"
done
run ufw --force enable

log "Configuring fail2ban"
cat <<EOF_F2B > /etc/fail2ban/jail.d/sshd.local
[sshd]
enabled = true
port = ${SSH_PORT}
backend = systemd
bantime = 1h
findtime = 10m
maxretry = 5
EOF_F2B
run systemctl enable --now fail2ban

log "Hardening complete"
log "UFW status:"
run ufw status verbose
