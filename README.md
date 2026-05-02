# WG-Edu VPN

A small, working WireGuard VPN built as a teaching artifact. ~600 lines
of Python on the client + ~250 lines of bash on the server. End-to-end
demo: client GUI on Windows ↔ server on a Linux VPS.

## What this is (and isn't)

**Is:** a thin, legible orchestration layer over the official WireGuard
implementation. The interesting code is configuration, lifecycle, status
display, and peer provisioning — the parts a real VPN product spends
most of its engineering effort on.

**Isn't:** a from-scratch implementation of the WireGuard protocol.
That would be either a 2000-line research project (correct) or an
insecure toy (likely). Real shipping clients delegate the cryptography
and tunneling to the same `wireguard.exe` we wrap here.

## Layout

```
client/
  wgvpn_core.py      # CLI + Python API — all real logic lives here
  wgvpn_gui.py       # Tkinter GUI, calls into core
server/
  wg-server-setup.sh # init / add-peer / list-peers
```

## Server setup (Ubuntu 22.04+ / Debian 12+ VPS)

On the server, as root:

```bash
chmod +x wg-server-setup.sh
sudo ./wg-server-setup.sh init
sudo ./wg-server-setup.sh add-peer alice
```

`add-peer` prints a client `.conf` path and a QR code. Copy that `.conf`
to the Windows machine. Open UDP/51820 on whatever firewall sits between
the VPS and the internet (cloud provider security group, ufw, etc.).

## Client setup (Windows 10/11)

1. Install **WireGuard for Windows**: <https://www.wireguard.com/install/>
   (this gives you `wireguard.exe` and `wg.exe` — we wrap both).
2. Install Python 3.10+.
3. Copy the `client/` folder anywhere.

CLI usage (open `cmd` **as Administrator** for `up`/`down`):

```
python wgvpn_core.py import alice.conf
python wgvpn_core.py list
python wgvpn_core.py up alice
python wgvpn_core.py status alice
python wgvpn_core.py down alice
```

GUI usage (also run as Administrator):

```
python wgvpn_gui.py
```

## What students should learn from reading this

A list of things that show up here that aren't obvious from a textbook:

1. **Why we shell out to `wireguard.exe`.** The Windows tunnel driver is
   a kernel-level component that requires a signed service. You can't
   replicate it in user-space Python. This is the same constraint every
   commercial VPN client on Windows runs into.
2. **Privilege boundary.** Reading/writing profiles needs no privileges.
   Installing/removing the tunnel service does. The CLI cleanly separates
   these so you don't need admin to just look at things.
3. **How peers actually get provisioned.** The `add-peer` script
   generates a private key on the server, which is fine for a lab but
   wrong for production. The right pattern is: client generates its own
   private key, sends only the public key to the server, server never
   sees the private key. A good extension exercise.
4. **Pre-shared keys.** Used here on top of the standard public-key
   handshake — quantum-resistance hedge built into WireGuard.
5. **Status without polling the kernel.** `wg show <iface> dump` gives
   tab-separated fields you can parse without external dependencies.
6. **What's missing from this being a "product".** No kill switch, no
   DNS leak protection on disconnect, no IPv6 handling, no auto-reconnect
   on network change, no code signing, no installer. Each of those is a
   project on its own.

## Suggested student extensions

In rough order of difficulty:

- **Easy:** add a system tray icon (use `pystray`); show last-handshake
  age more prominently; add a "copy public IP" button that pings an
  external IP service to verify the tunnel is actually being used.
- **Medium:** implement a kill switch (drop default route or add a
  Windows Firewall rule when disconnected unexpectedly); detect DNS
  leaks by querying `whoami.cloudflare`; flip to client-side keygen so
  the server never sees private keys.
- **Hard:** package it with PyInstaller + a code-signed installer;
  port the client to Android (`VpnService` API + a JNI binding to the
  Go WireGuard implementation); add multi-server failover.
- **Research-grade:** reimplement the WireGuard handshake in pure
  Python against the spec and validate against the reference test
  vectors. Don't deploy this. But it's a great way to actually
  understand Noise_IK.

## Security notes

- Runs the WireGuard service as Local System (because that's what
  `wireguard.exe /installtunnelservice` does). This is the same trust
  model as the official GUI.
- Server-side keys live in `/etc/wireguard/` with 0700/0600 perms.
- Pre-shared keys are written into client `.conf` files in plaintext.
  That's how WireGuard works; protect those files.
- No log redaction. Don't paste your `.conf` files into Discord.
