#!/bin/bash
# Embedded in CloudFormation UserData. No parameter values or application release.
set -euo pipefail
umask 077
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y docker.io docker-compose-v2 awscli jq iptables
systemctl enable --now docker
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
  snap install amazon-ssm-agent --classic
fi
snap start --enable amazon-ssm-agent
install -d -m 0700 /etc/eventquest /opt/eventquest/bin /var/lib/eventquest/releases
printf '%s\n' '__SERVICE_ROLE__' > /etc/eventquest/service-role
install -d -m 0700 -o 10001 -g 10001 /var/lib/eventquest/fault-lab \
  /var/lib/eventquest/caddy-data /var/lib/eventquest/caddy-config
# IMDSv2 hop-limit is also 1. Persist the Docker forwarding deny after reboot.
cat > /opt/eventquest/bin/block-imds <<'EOF'
#!/bin/bash
set -euo pipefail
iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null || \
  iptables -I DOCKER-USER 1 -d 169.254.169.254/32 -j DROP
EOF
chmod 0700 /opt/eventquest/bin/block-imds
cat > /etc/systemd/system/eventquest-block-imds.service <<'EOF'
[Unit]
Description=Block container access to EC2 instance credentials
Requires=docker.service
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
ExecStart=/opt/eventquest/bin/block-imds
RemainAfterExit=yes
[Install]
WantedBy=docker.service
EOF
systemctl daemon-reload
systemctl enable --now eventquest-block-imds
# A6 installs the reviewed release helper. This marker is NOT application readiness.
touch /etc/eventquest/host-bootstrap-complete
