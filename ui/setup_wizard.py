from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from core.setup_manager import SetupManager
from providers.gmail import GmailProvider

BG = "#0b0f14"
PANEL = "#111821"
PANEL_2 = "#151e29"
BORDER = "#243244"
TEXT = "#eef4fb"
MUTED = "#8ea0b5"
ACCENT = "#5aa9ff"
GOOD = "#62d692"
WARN = "#f3c969"
BAD = "#ff7373"


class SetupWizard(tk.Toplevel):
    def __init__(self, parent, manager: SetupManager, on_change=None):
        super().__init__(parent)
        self.parent = parent
        self.manager = manager
        self.on_change = on_change
        self.title("Host Command Center — Setup Wizard")
        self.geometry("980x700")
        self.minsize(880, 620)
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.current = "system"
        self.step_buttons: dict[str, tk.Button] = {}
        self.body = None
        self.status_var = tk.StringVar(value="")
        self._build_shell()
        self.show_step("system")

    def _build_shell(self):
        top = tk.Frame(self, bg=PANEL, height=64)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="FIRST-TIME SETUP", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(side="left", padx=22)
        tk.Label(top, text="Live reads + alerts / remote writes locked", bg=PANEL, fg=GOOD, font=("Segoe UI Semibold", 9)).pack(side="right", padx=22)

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True)
        nav = tk.Frame(wrap, bg=PANEL, width=220)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        self.main = tk.Frame(wrap, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        labels = [
            ("system", "1  System Check"),
            ("email", "2  Bot Email"),
            ("hospitable", "3  Hospitable"),
            ("properties", "4  Properties"),
            ("webhook", "5  Webhook Prep"),
            ("final", "6  Final Check"),
        ]
        tk.Label(nav, text="SETUP STEPS", bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=18, pady=(20, 8))
        for key, label in labels:
            b = tk.Button(nav, text=label, anchor="w", command=lambda k=key: self.show_step(k), bg=PANEL, fg=TEXT,
                          activebackground=PANEL_2, activeforeground=TEXT, relief="flat", bd=0, padx=16, pady=11,
                          font=("Segoe UI", 10), cursor="hand2")
            b.pack(fill="x", padx=8, pady=1)
            self.step_buttons[key] = b
        self.summary = tk.Label(nav, text="", justify="left", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), wraplength=180)
        self.summary.pack(anchor="w", padx=18, pady=18)
        self._refresh_nav()

    def _refresh_nav(self):
        statuses = self.manager.step_status()
        done = sum(1 for x in statuses.values() if x["ok"])
        self.summary.configure(text=f"{done}/{len(statuses)} setup checks complete")
        for key, b in self.step_buttons.items():
            ok = statuses[key]["ok"]
            prefix = "✓" if ok else "○"
            label = b.cget("text")
            if label[:1] in {"✓", "○"}:
                label = label[2:]
            b.configure(text=f"{prefix} {label}", fg=GOOD if ok else TEXT)
        if self.on_change:
            self.on_change()

    def _clear(self):
        for child in self.main.winfo_children():
            child.destroy()

    def _header(self, title, subtitle):
        tk.Label(self.main, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 21)).pack(anchor="w", padx=28, pady=(28, 4))
        tk.Label(self.main, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 10), wraplength=650, justify="left").pack(anchor="w", padx=28)

    def _card(self):
        c = tk.Frame(self.main, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=18, pady=16)
        c.pack(fill="x", padx=28, pady=18)
        return c

    def _button(self, parent, text, cmd, color=ACCENT):
        return tk.Button(parent, text=text, command=cmd, bg=PANEL_2, fg=color, activebackground=BORDER,
                         activeforeground=color, relief="flat", bd=0, padx=12, pady=8,
                         font=("Segoe UI Semibold", 9), cursor="hand2")

    def show_step(self, key: str):
        self.current = key
        self._clear()
        for k, b in self.step_buttons.items():
            b.configure(bg=PANEL_2 if k == key else PANEL)
        getattr(self, f"_step_{key}")()
        self._refresh_nav()

    def _step_system(self):
        self._header("System Check", "Verify the local install before connecting any outside account.")
        c = self._card()
        st = self.manager.step_status()["system"]
        tk.Label(c, text=("✓ READY" if st["ok"] else "⚠ CHECK NEEDED"), bg=PANEL, fg=GOOD if st["ok"] else WARN, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(c, text=st["detail"], bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 2))
        tk.Label(c, text="Safe Mode is locked ON. This setup wizard cannot accept, decline, or alter a live Airbnb booking.", bg=PANEL, fg=MUTED, wraplength=620, justify="left").pack(anchor="w", pady=(8, 12))
        self._button(c, "Re-run System Check", lambda: self.show_step("system")).pack(anchor="w")

    def _step_email(self):
        self._header("Bot Email", "Authorize a dedicated Gmail account, choose where approval alerts go, and verify a real Gmail message can be delivered.")
        c = self._card()
        tk.Label(c, text="Approval recipient", bg=PANEL, fg=MUTED).grid(row=0, column=0, sticky="w", pady=8)
        self.recipient = tk.StringVar(value=self.manager.db.get_setting("notification_recipient", ""))
        tk.Entry(c, textvariable=self.recipient, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", width=42).grid(row=0, column=1, padx=10, sticky="w")
        self._button(c, "Save", self._save_recipient).grid(row=0, column=2, padx=5)

        gmail_ok = self.manager.gmail_configured()
        tk.Label(c, text=f"{'✓' if gmail_ok else '○'} Gmail OAuth {'already configured' if gmail_ok else 'not configured'}", bg=PANEL, fg=GOOD if gmail_ok else MUTED, font=("Segoe UI Semibold", 10)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 5))
        self._button(c, "1. Open Google Gmail API Setup", lambda: webbrowser.open("https://developers.google.com/workspace/gmail/api/quickstart/python"), TEXT).grid(row=2, column=0, sticky="w", pady=4)
        self._button(c, "2. Choose OAuth JSON + Authorize", self._authorize_gmail, ACCENT).grid(row=2, column=1, sticky="w", padx=10, pady=4)
        self._button(c, "Test Gmail Connection", self._test_gmail, GOOD).grid(row=3, column=1, sticky="w", padx=10, pady=4)
        self._button(c, "Send LIVE Test Email", self._send_live_test_email, GOOD).grid(row=3, column=2, sticky="w", padx=5, pady=4)
        tk.Label(c, text="Google opens in your browser for consent. The app stores an OAuth refresh token—not your Gmail password.", bg=PANEL, fg=MUTED, wraplength=650, justify="left", font=("Segoe UI", 8)).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _save_recipient(self):
        try:
            self.manager.save_recipient(self.recipient.get())
            messagebox.showinfo("Saved", "Approval recipient saved.", parent=self)
            self._refresh_nav()
        except Exception as exc:
            messagebox.showerror("Recipient", str(exc), parent=self)

    def _authorize_gmail(self):
        path = filedialog.askopenfilename(parent=self, title="Choose Google OAuth client JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        temp_auth = self.manager.data_dir / ".gmail_oauth_import.json"
        cmd = [sys.executable, str(self.manager.app_dir / "providers" / "gmail_oauth_setup.py"), path, "--out", str(temp_auth)]
        def worker():
            try:
                proc = subprocess.run(cmd, cwd=self.manager.app_dir, capture_output=True, text=True, timeout=300)
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or "OAuth setup failed")[-2000:]
                    self.after(0, lambda: messagebox.showerror("Gmail OAuth", detail, parent=self))
                else:
                    self.manager.import_gmail_auth_file(temp_auth)
                    self.manager.db.log("INFO", "SETUP", "Gmail OAuth completed")
                    self.after(0, lambda: messagebox.showinfo("Gmail OAuth", "Gmail authorization completed and credentials were encrypted for this Windows user.", parent=self))
                    self.after(0, lambda: self.show_step("email"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Gmail OAuth", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def _test_gmail(self):
        def worker():
            ok, detail = self.manager.gmail_health()
            self.manager.db.log("INFO" if ok else "ERROR", "SETUP", f"Gmail health: {detail}")
            self.after(0, lambda: messagebox.showinfo("Gmail", detail, parent=self) if ok else messagebox.showerror("Gmail", detail, parent=self))
            self.after(0, self._refresh_nav)
        threading.Thread(target=worker, daemon=True).start()


    def _send_live_test_email(self):
        try:
            self.manager.save_recipient(self.recipient.get())
        except Exception as exc:
            messagebox.showerror("Recipient", str(exc), parent=self); return
        def worker():
            try:
                msg = self.manager.send_live_test_email()
                self.after(0, self._refresh_nav)
                self.after(0, lambda: messagebox.showinfo("Live Gmail", f"Real email sent successfully.\nMessage ID: {msg}", parent=self))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Live Gmail", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def _step_hospitable(self):
        self._header("Hospitable", "Connect the official Airbnb/PMS data path. Keep this token READ-only while we validate Shadow Mode.")
        c = self._card()
        tk.Label(c, text="Hospitable PAT", bg=PANEL, fg=MUTED).grid(row=0, column=0, sticky="w", pady=8)
        current = self.manager.secrets.get_secret("hospitable_pat", "")
        self.pat = tk.StringVar(value=current)
        tk.Entry(c, textvariable=self.pat, show="•", bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", width=42).grid(row=0, column=1, padx=10, sticky="w")
        self._button(c, "Save Securely", self._save_pat, ACCENT).grid(row=0, column=2, padx=5)
        self._button(c, "Open Hospitable", lambda: webbrowser.open("https://my.hospitable.com/"), TEXT).grid(row=1, column=0, sticky="w", pady=6)
        self._button(c, "PAT Instructions", lambda: webbrowser.open("https://help.hospitable.com/en/articles/8609392-accessing-the-public-api-with-a-personal-access-token-pat"), TEXT).grid(row=1, column=1, sticky="w", padx=10, pady=6)
        self._button(c, "Test Read-Only Connection", self._test_hospitable, GOOD).grid(row=2, column=1, sticky="w", padx=10, pady=6)
        persist_text = "Windows DPAPI encryption enabled" if self.manager.secrets.persistent else "Development host: token is memory-only"
        tk.Label(c, text=f"{persist_text}. Never paste this PAT into chat or email.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _save_pat(self):
        try:
            self.manager.save_hospitable_token(self.pat.get())
            messagebox.showinfo("Hospitable", "PAT saved securely for this Windows user.", parent=self)
            self._refresh_nav()
        except Exception as exc:
            messagebox.showerror("Hospitable", str(exc), parent=self)

    def _test_hospitable(self):
        try:
            self.manager.save_hospitable_token(self.pat.get())
        except Exception as exc:
            messagebox.showerror("Hospitable", str(exc), parent=self); return
        def worker():
            ok, detail = self.manager.hospitable_health()
            self.manager.db.log("INFO" if ok else "ERROR", "SETUP", f"Hospitable health: {detail}")
            self.after(0, lambda: messagebox.showinfo("Hospitable", detail, parent=self) if ok else messagebox.showerror("Hospitable", detail, parent=self))
            self.after(0, self._refresh_nav)
        threading.Thread(target=worker, daemon=True).start()

    def _step_properties(self):
        self._header("Properties", "Pull the real property list from Hospitable. This is read-only and cannot change Airbnb.")
        c = self._card()
        count = len(self.manager.db.list_remote_properties("hospitable"))
        tk.Label(c, text=f"{count} live properties currently cached", bg=PANEL, fg=GOOD if count else MUTED, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        self._button(c, "Sync Live Properties (Read Only)", self._sync_properties, ACCENT).pack(anchor="w", pady=(12, 4))
        tk.Label(c, text="After sync, the app maps these into real live property profiles automatically. Developer demo profiles disappear from normal property views.", bg=PANEL, fg=MUTED, wraplength=650, justify="left", font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 0))

    def _sync_properties(self):
        def worker():
            try:
                count = self.manager.sync_properties()
                self.after(0, lambda: messagebox.showinfo("Properties", f"Synced {count} live properties. No remote data was modified.", parent=self))
                self.after(0, lambda: self.show_step("properties"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Properties", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def _step_webhook(self):
        self._header("Webhook Preparation", "Prepare a private webhook path. Until the 24/7 cloud endpoint is deployed, the desktop app also reconciles reservations by polling while it is open.")
        c = self._card()
        current = self.manager.db.get_setting("webhook_path_token", "")
        status = "Prepared" if current else "Not prepared"
        tk.Label(c, text=f"Webhook secret: {status}", bg=PANEL, fg=GOOD if current else MUTED, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        self._button(c, "Generate / Verify Webhook Secret", self._prepare_webhook, ACCENT).pack(anchor="w", pady=(12, 6))
        tk.Label(c, text="This does not expose your PC to the internet. Cloud deployment is still a separate step; live read reconciliation already works while the desktop app is open.", bg=PANEL, fg=MUTED, wraplength=650, justify="left", font=("Segoe UI", 8)).pack(anchor="w")

    def _prepare_webhook(self):
        self.manager.prepare_webhook_secret()
        messagebox.showinfo("Webhook", "Private webhook path secret prepared.", parent=self)
        self.show_step("webhook")

    def _step_final(self):
        self._header("Final Readiness Check", "Run hardening, initialize the first real reservation reconciliation, and verify Shadow Mode before finishing setup.")
        c = self._card()
        statuses = self.manager.step_status()
        for key in ("system", "email", "hospitable", "properties", "webhook"):
            st = statuses[key]
            tk.Label(c, text=f"{'✓' if st['ok'] else '○'} {key.title()}: {st['detail']}", bg=PANEL, fg=GOOD if st["ok"] else WARN, font=("Segoe UI", 9), wraplength=650, justify="left").pack(anchor="w", pady=3)
        self._button(c, "Run Robustness Lab", self._run_robustness, TEXT).pack(anchor="w", pady=(14, 5))
        self._button(c, "Initialize LIVE Shadow Mode", self._initialize_shadow, GOOD).pack(anchor="w", pady=5)
        self._button(c, "Finish Setup", self._finish, ACCENT).pack(anchor="w", pady=5)
        tk.Label(c, text="Finishing setup does NOT enable autonomous booking actions. Safe Mode remains locked ON.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))


    def _initialize_shadow(self):
        def worker():
            try:
                stats = self.manager.initialize_shadow_mode()
                self.manager.db.set_setting("initial_shadow_sync_passed", True)
                self.after(0, self._refresh_nav)
                body = f"Live reconciliation succeeded.\n\nFetched: {stats.get('fetched', 0)}\nPending: {stats.get('pending', 0)}\nAccepted: {stats.get('accepted', 0)}\nApproval emails sent: {stats.get('emailed', 0)}"
                self.after(0, lambda: messagebox.showinfo("Shadow Mode", body, parent=self))
            except Exception as exc:
                self.manager.db.set_setting("initial_shadow_sync_passed", False)
                self.after(0, lambda: messagebox.showerror("Shadow Mode", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def _run_robustness(self):
        def worker():
            try:
                from robustness_lab import run
                results = run()
                failed = [r for r in results if not r[1]]
                body = "\n".join(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}" for name, ok, detail in results)
                self.manager.db.set_setting("last_robustness_passed", not failed)
                self.manager.db.log("INFO" if not failed else "ERROR", "SETUP", f"Robustness lab: {len(results)-len(failed)}/{len(results)} passed")
                self.after(0, lambda: messagebox.showinfo("Robustness Lab", body, parent=self) if not failed else messagebox.showerror("Robustness Lab", body, parent=self))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Robustness Lab", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self):
        statuses = self.manager.step_status()
        required = ("system", "email", "hospitable", "properties", "webhook")
        missing = [k for k in required if not statuses[k]["ok"]]
        if missing:
            if not messagebox.askyesno("Setup incomplete", "These steps are incomplete:\n\n" + "\n".join(f"• {x.title()}" for x in missing) + "\n\nFinish anyway? You can rerun Setup later.", parent=self):
                return
        self.manager.mark_complete()
        self._refresh_nav()
        messagebox.showinfo("Setup", "Setup state saved. LIVE Shadow Mode is ready while the desktop app is open.\n\nRemote Airbnb/Hospitable write actions remain locked OFF.", parent=self)
        self.destroy()
