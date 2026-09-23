# Task #79 -- shared by setup-oracle-vm.sh and setup-https.sh. Several
# Ubuntu cloud images -- Oracle's default ones included -- ship with
# iptables pre-configured to accept only established/related traffic,
# ICMP, loopback, and new SSH connections, REJECTing everything else on a
# catch-all rule. That's a second, separate firewall from both ufw and
# Oracle's own cloud-level Security List -- opening the Security List
# alone looks like it should work and then just silently times out. Hit
# live: the app ran fine and the Security List was correct, but every
# connection past port 22 died on this VM-local rule.
#
# Idempotent (checks for an existing ACCEPT rule for the port first) and
# inserts ahead of the first REJECT/DROP rule found, not at the end where
# a catch-all reject would still shadow it -- falls back to appending if
# there's no such rule.
open_iptables_port() {
  local port="$1"
  if ! command -v iptables >/dev/null 2>&1; then
    return 0
  fi
  if sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    return 0
  fi
  echo "==> Allowing TCP port $port through the VM's own iptables"
  local reject_line
  reject_line="$(sudo iptables -L INPUT -n --line-numbers | awk '/^[0-9]+ +(REJECT|DROP)/ {print $1; exit}')"
  if [ -n "$reject_line" ]; then
    sudo iptables -I INPUT "$reject_line" -p tcp --dport "$port" -j ACCEPT
  else
    sudo iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
  fi
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save
  else
    sudo mkdir -p /etc/iptables
    sudo sh -c 'iptables-save > /etc/iptables/rules.v4'
  fi
}
