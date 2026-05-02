#!/usr/bin/env bash
# wg-server-setup.sh — Spin up a WireGuard server on Ubuntu/Debian.
#
# What this does:
#   1. Installs WireGuard.
#   2. Generates server keys.
#   3. Writes /etc/wireguard/wg0.conf with sane defaults.
#   4. Enables IP forwarding + a NAT rule so peers can reach the internet.
#   5. Starts the wg-quick@wg0 service.
#   6. Provides a helper to add peers and emit a client .conf.
#
# Usage:
#   sudo ./wg-server-setup.sh init [public-ip-or-hostname]
#   sudo ./wg-server-setup.sh add-peer <peer-name>
#   sudo ./wg-server-setup.sh list-peers
#
# Run on a fresh Ubuntu 22.04+ / Debian 12+ VPS. Tested behaviour, not
# audited. Educational use only.

set -euo pipefail

WG_DIR="/etc/wireguard"
WG_IFACE="wg0"
WG_CONF="${WG_DIR}/${WG_IFACE}.conf"
WG_PORT="51820"
WG_NET="10.77.0.0/24"
WG_SERVER_IP="10.77.0.1"
PEERS_DIR="${WG_DIR}/peers"

require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Must run as root (use sudo)." >&2
        exit 1
    fi
}

detect_wan_iface() {
    # The interface used to reach the public internet.
    ip route show default | awk '/default/ {print $5; exit}'
}

detect_public_ip() {
    # Fall back chain: argument > external lookup.
    if [[ -n "${1:-}" ]]; then
        echo "$1"; return
    fi
    curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || \
    curl -fsS --max-time 4 https://ifconfig.me 2>/dev/null || \
    { echo "Could not detect public IP. Pass it as an argument." >&2; exit 1; }
}

cmd_init() {
    require_root

    if [[ -f "$WG_CONF" ]]; then
        echo "$WG_CONF already exists. Refusing to overwrite." >&2
        echo "Remove it manually if you really want to start over." >&2
        exit 1
    fi

    local public_endpoint
    public_endpoint="$(detect_public_ip "${1:-}")"
    local wan_iface
    wan_iface="$(detect_wan_iface)"
    if [[ -z "$wan_iface" ]]; then
        echo "Couldn't find default route interface. Aborting." >&2
        exit 1
    fi

    echo "→ Installing WireGuard…"
    apt-get update -qq
    apt-get install -y -qq wireguard qrencode iptables curl

    echo "→ Generating server keys…"
    mkdir -p "$WG_DIR" "$PEERS_DIR"
    chmod 700 "$WG_DIR" "$PEERS_DIR"
    umask 077
    wg genkey | tee "${WG_DIR}/server_private.key" | \
        wg pubkey > "${WG_DIR}/server_public.key"

    local server_priv server_pub
    server_priv="$(cat "${WG_DIR}/server_private.key")"
    server_pub="$(cat "${WG_DIR}/server_public.key")"

    echo "→ Writing ${WG_CONF}…"
    cat > "$WG_CONF" <<EOF
# Managed by wg-server-setup.sh — peers appended below.
[Interface]
Address = ${WG_SERVER_IP}/24
ListenPort = ${WG_PORT}
PrivateKey = ${server_priv}

# NAT outbound traffic from the VPN subnet so peers reach the internet.
PostUp   = iptables -A FORWARD -i ${WG_IFACE} -j ACCEPT; iptables -t nat -A POSTROUTING -s ${WG_NET} -o ${wan_iface} -j MASQUERADE
PostDown = iptables -D FORWARD -i ${WG_IFACE} -j ACCEPT; iptables -t nat -D POSTROUTING -s ${WG_NET} -o ${wan_iface} -j MASQUERADE

# === Peers (do not edit by hand; use \`add-peer\`) ===
EOF
    chmod 600 "$WG_CONF"

    echo "→ Enabling IPv4 forwarding…"
    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wireguard.conf
    sysctl -q --system

    echo "→ Starting wg-quick@${WG_IFACE}…"
    systemctl enable --now "wg-quick@${WG_IFACE}"

    # Stash the public endpoint for add-peer to use later.
    echo "${public_endpoint}:${WG_PORT}" > "${WG_DIR}/server_endpoint"

    echo
    echo "Server is up."
    echo "  Endpoint:    ${public_endpoint}:${WG_PORT}"
    echo "  Server pubkey: ${server_pub}"
    echo
    echo "Open UDP/${WG_PORT} on your firewall/cloud security group."
    echo "Then add a peer with:  sudo $0 add-peer <name>"
}

cmd_add_peer() {
    require_root
    local name="${1:-}"
    if [[ -z "$name" ]]; then
        echo "Usage: $0 add-peer <name>" >&2; exit 2
    fi
    if [[ ! "$name" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; then
        echo "Peer name must be 1–32 chars of letters/digits/_/-." >&2; exit 2
    fi
    if [[ ! -f "$WG_CONF" ]]; then
        echo "Server not initialized. Run: $0 init" >&2; exit 1
    fi

    local peer_dir="${PEERS_DIR}/${name}"
    if [[ -d "$peer_dir" ]]; then
        echo "Peer '${name}' already exists at ${peer_dir}." >&2; exit 1
    fi

    mkdir -p "$peer_dir"
    chmod 700 "$peer_dir"
    umask 077

    # Pick the next free /32 in the subnet.
    local used_ips next_ip
    used_ips="$(grep -oP 'AllowedIPs\s*=\s*\K10\.77\.0\.\d+' "$WG_CONF" || true)"
    local i
    for i in $(seq 2 254); do
        if ! grep -qx "10.77.0.${i}" <<<"$used_ips"; then
            next_ip="10.77.0.${i}"; break
        fi
    done
    if [[ -z "${next_ip:-}" ]]; then
        echo "No free addresses in ${WG_NET}." >&2; exit 1
    fi

    # Generate the peer's key material on the server. (Acceptable for an
    # educational lab; in production you'd have peers generate their own
    # private keys and only send their public key to the server.)
    wg genkey | tee "${peer_dir}/private.key" | wg pubkey > "${peer_dir}/public.key"
    wg genpsk > "${peer_dir}/preshared.key"

    local peer_priv peer_pub peer_psk server_pub endpoint
    peer_priv="$(cat "${peer_dir}/private.key")"
    peer_pub="$(cat "${peer_dir}/public.key")"
    peer_psk="$(cat "${peer_dir}/preshared.key")"
    server_pub="$(cat "${WG_DIR}/server_public.key")"
    endpoint="$(cat "${WG_DIR}/server_endpoint")"

    # Append to server config + apply live (no restart needed).
    cat >> "$WG_CONF" <<EOF

# peer: ${name}
[Peer]
PublicKey = ${peer_pub}
PresharedKey = ${peer_psk}
AllowedIPs = ${next_ip}/32
EOF

    wg set "$WG_IFACE" \
        peer "$peer_pub" \
        preshared-key <(echo "$peer_psk") \
        allowed-ips "${next_ip}/32"

    # Build the client .conf the user will copy to their machine.
    local client_conf="${peer_dir}/${name}.conf"
    cat > "$client_conf" <<EOF
# Client config for peer '${name}'.
# Copy this file to the client machine and import it with wgvpn.
[Interface]
PrivateKey = ${peer_priv}
Address = ${next_ip}/24
DNS = 1.1.1.1, 9.9.9.9

[Peer]
PublicKey = ${server_pub}
PresharedKey = ${peer_psk}
Endpoint = ${endpoint}
# Route ALL traffic through the tunnel. Use 10.77.0.0/24 instead for split-tunnel.
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF
    chmod 600 "$client_conf"

    echo "Added peer '${name}' as ${next_ip}."
    echo "Client config: ${client_conf}"
    echo
    echo "QR code (scan from a phone WireGuard app):"
    qrencode -t ansiutf8 < "$client_conf"
}

cmd_list_peers() {
    require_root
    if [[ ! -d "$PEERS_DIR" ]]; then
        echo "(no peers)"; return
    fi
    printf "%-20s %-15s %s\n" "NAME" "ADDRESS" "PUBLIC KEY"
    local d name ip pub
    for d in "$PEERS_DIR"/*/; do
        [[ -d "$d" ]] || continue
        name="$(basename "$d")"
        pub="$(cat "${d}public.key" 2>/dev/null || echo "?")"
        ip="$(grep -B1 "${name}" "$WG_CONF" | grep -oP 'AllowedIPs\s*=\s*\K10\.77\.0\.\d+' || true)"
        # Fallback: pull from peer block by pubkey.
        if [[ -z "$ip" ]]; then
            ip="$(awk -v k="$pub" '
                /^\[Peer\]/ {block=""; next}
                {block=block"\n"$0}
                $0 ~ k {match(block, /AllowedIPs = 10\.77\.0\.[0-9]+/);
                        print substr(block, RSTART+14, RLENGTH-14); exit}
            ' "$WG_CONF")"
        fi
        printf "%-20s %-15s %s\n" "$name" "${ip:-?}" "$pub"
    done
}

usage() {
    cat <<EOF
WireGuard server setup helper.

Commands:
  init [public-ip]      Install + configure server. Public IP autodetected if omitted.
  add-peer <name>       Add a peer and print their client .conf + QR code.
  list-peers            Show known peers.

All commands require root.
EOF
}

main() {
    local cmd="${1:-}"; shift || true
    case "$cmd" in
        init)        cmd_init "$@" ;;
        add-peer)    cmd_add_peer "$@" ;;
        list-peers)  cmd_list_peers ;;
        ""|-h|--help|help) usage ;;
        *) echo "Unknown command: $cmd" >&2; usage; exit 2 ;;
    esac
}

main "$@"
