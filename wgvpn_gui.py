"""
wgvpn_gui.py — Minimal Tkinter GUI for the educational WireGuard client.

Talks to wgvpn_core.py for all real work. Keep this file UI-only:
no subprocess calls, no file logic. That separation is what makes
the project legible to students.

Run as Administrator (connect/disconnect requires service install).
"""

from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import wgvpn_core as core


REFRESH_MS = 1500  # how often to repoll status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_handshake(unix_ts: int) -> str:
    if not unix_ts:
        return "never"
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(unix_ts, timezone.utc)
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("WG-Edu VPN")
        self.geometry("520x400")
        self.minsize(480, 360)

        # Intentional palette — refined, not generic. Single accent.
        self.configure(bg="#0e1116")
        self._configure_style()

        self._selected_profile: core.Profile | None = None
        self._build_ui()
        self._refresh_profiles()
        self.after(REFRESH_MS, self._tick)

    # ----- styling ----------------------------------------------------------

    def _configure_style(self) -> None:
        s = ttk.Style(self)
        # Use 'clam' as a base because it actually respects color overrides
        # on Windows, unlike 'vista' which ignores most theming.
        s.theme_use("clam")
        bg = "#0e1116"
        fg = "#e6e6e6"
        muted = "#8a8f98"
        accent = "#7fdbb6"

        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("Muted.TLabel", background=bg, foreground=muted)
        s.configure("Heading.TLabel", background=bg, foreground=fg,
                    font=("Segoe UI Semibold", 11))
        s.configure("Status.TLabel", background=bg, foreground=accent,
                    font=("Consolas", 10))
        s.configure("TButton", padding=(10, 6))
        s.configure(
            "Listbox.TFrame",
            background="#161b22",
            relief="flat",
        )

    # ----- layout -----------------------------------------------------------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        # Header
        ttk.Label(outer, text="WG-Edu VPN", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Educational WireGuard client — wraps wireguard.exe",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        # Profiles list
        list_frame = tk.Frame(outer, bg="#161b22", highlightthickness=0, bd=0)
        list_frame.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(
            list_frame,
            bg="#161b22",
            fg="#e6e6e6",
            selectbackground="#1f2a37",
            selectforeground="#ffffff",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            font=("Consolas", 10),
        )
        self.listbox.pack(fill="both", expand=True, padx=1, pady=1)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # Status panel
        self.status_var = tk.StringVar(value="No profile selected.")
        ttk.Label(outer, textvariable=self.status_var, style="Status.TLabel") \
            .pack(anchor="w", pady=(10, 0))

        # Button row
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(12, 0))

        self.btn_connect = ttk.Button(btns, text="Connect", command=self._do_connect)
        self.btn_disconnect = ttk.Button(btns, text="Disconnect", command=self._do_disconnect)
        self.btn_import = ttk.Button(btns, text="Import .conf…", command=self._do_import)
        self.btn_delete = ttk.Button(btns, text="Delete", command=self._do_delete)

        self.btn_connect.pack(side="left")
        self.btn_disconnect.pack(side="left", padx=(8, 0))
        ttk.Frame(btns).pack(side="left", expand=True, fill="x")  # spacer
        self.btn_import.pack(side="right")
        self.btn_delete.pack(side="right", padx=(0, 8))

        self._update_buttons()

    # ----- state ------------------------------------------------------------

    def _refresh_profiles(self) -> None:
        self.listbox.delete(0, tk.END)
        self._profiles = core.list_profiles()
        for p in self._profiles:
            mark = "●" if core.is_connected(p) else "○"
            self.listbox.insert(tk.END, f"  {mark}  {p.name}")

        # Re-select if we had one
        if self._selected_profile:
            for i, p in enumerate(self._profiles):
                if p.name == self._selected_profile.name:
                    self.listbox.selection_set(i)
                    break
            else:
                self._selected_profile = None
        self._update_buttons()
        self._update_status()

    def _on_select(self, _evt) -> None:
        sel = self.listbox.curselection()
        if not sel:
            self._selected_profile = None
        else:
            self._selected_profile = self._profiles[sel[0]]
        self._update_buttons()
        self._update_status()

    def _update_buttons(self) -> None:
        has = self._selected_profile is not None
        connected = has and core.is_connected(self._selected_profile)

        self.btn_connect["state"] = "normal" if has and not connected else "disabled"
        self.btn_disconnect["state"] = "normal" if connected else "disabled"
        self.btn_delete["state"] = "normal" if has and not connected else "disabled"

    def _update_status(self) -> None:
        p = self._selected_profile
        if not p:
            self.status_var.set("No profile selected.")
            return

        info = core.status(p)
        if not info:
            self.status_var.set(f"{p.name}: disconnected")
            return

        self.status_var.set(
            f"{p.name}  •  {info['endpoint']}  •  "
            f"hs {fmt_handshake(info['last_handshake_unix'])}  •  "
            f"↓ {fmt_bytes(info['rx_bytes'])}  ↑ {fmt_bytes(info['tx_bytes'])}"
        )

    def _tick(self) -> None:
        # Lightweight poll. Only redraw the list if connectedness changed
        # for any profile, to avoid stealing the user's selection.
        try:
            new_states = [core.is_connected(p) for p in self._profiles]
            old_states = getattr(self, "_last_states", None)
            if old_states != new_states:
                self._last_states = new_states
                self._refresh_profiles()
            else:
                self._update_status()
                self._update_buttons()
        finally:
            self.after(REFRESH_MS, self._tick)

    # ----- actions ----------------------------------------------------------

    def _do_import(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a WireGuard config",
            filetypes=[("WireGuard config", "*.conf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            core.import_profile(Path(path))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Import failed", str(e))
            return
        self._refresh_profiles()

    def _do_delete(self) -> None:
        p = self._selected_profile
        if not p:
            return
        if not messagebox.askyesno("Delete profile", f"Delete '{p.name}'?"):
            return
        core.delete_profile(p.name)
        self._selected_profile = None
        self._refresh_profiles()

    def _do_connect(self) -> None:
        p = self._selected_profile
        if not p:
            return
        try:
            core.connect(p)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Connect failed", str(e))
            return
        self._refresh_profiles()

    def _do_disconnect(self) -> None:
        p = self._selected_profile
        if not p:
            return
        try:
            core.disconnect(p)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Disconnect failed", str(e))
            return
        self._refresh_profiles()


def main() -> int:
    # Sanity-check that WireGuard is actually installed before opening
    # the window — fail loudly with an actionable message.
    try:
        core.find_wireguard_exe()
    except FileNotFoundError as e:
        # No Tk yet; use a transient root so messagebox works.
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("WireGuard not installed", str(e))
        return 1

    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
