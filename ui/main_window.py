from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from core.booking_reasoner import BookingReasoner
from core.database import Database
from core.event_engine import EventEngine
from core.hospitable_mapper import reservation_financial_value, reservation_property_id, reservation_status
from core.models import ApprovalState, PropertyProfile
from core.secret_store import SecretStore
from core.setup_manager import SetupManager
from providers.gmail import GmailProvider
from providers.hospitable import HospitableProvider
from providers.mock_email import MockEmailProvider
from services.approval_service import ApprovalService
from services.notification_service import NotificationService
from services.shadow_sync_service import ShadowSyncService
from ui.setup_wizard import SetupWizard


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


class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")


class HostCommandCenter(tk.Tk):
    def __init__(self, app_dir: Path):
        super().__init__()
        self.app_dir = app_dir
        self.data_dir = app_dir / "data"
        self.db = Database(self.data_dir / "host_command_center.db")
        self.reasoner = BookingReasoner()
        self.engine = EventEngine(self.db, self.reasoner)
        self.approvals = ApprovalService(self.db)
        self.db.expire_overdue_approvals()
        self.secret_store = SecretStore(self.data_dir / "secrets.json")
        self.setup_manager = SetupManager(self.app_dir, self.db, self.secret_store)
        self.session_hospitable_token = self.secret_store.get_secret("hospitable_pat", "")
        self.current_page = "Dashboard"
        self._sync_in_progress = False
        self._last_auto_sync_attempt: datetime | None = None
        self._seed_properties()

        self.title("Host Command Center")
        self.geometry("1360x820")
        self.minsize(1050, 680)
        self.configure(bg=BG)
        self._styles()
        self._build_shell()
        self.show_page("Dashboard")
        self.after(1000, self._tick)
        self.after(2500, self._maybe_start_shadow_mode)
        self.after(6000, self._check_webhook_inbox)

    def _styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TScrollbar", background=PANEL_2, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED)
        s.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT, arrowcolor=TEXT)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)])

    def _seed_properties(self):
        # Development profiles are retained only as a fallback until a real
        # Hospitable property sync succeeds. Live pages hide them once live
        # property profiles exist.
        if self.db.list_properties():
            return
        src = self.data_dir / "properties.json"
        for item in json.loads(src.read_text(encoding="utf-8")):
            item.setdefault("source", "demo")
            item.setdefault("policy_configured", True)
            self.db.upsert_property(PropertyProfile(**item))
        self.db.log("INFO", "SYSTEM", "Initialized development property profiles")

    def _live_profiles(self) -> list[PropertyProfile]:
        return [p for p in self.db.list_properties() if p.source == "hospitable"]

    def _visible_profiles(self) -> list[PropertyProfile]:
        live = self._live_profiles()
        return live if live else self.db.list_properties()

    def _gmail(self) -> GmailProvider:
        return GmailProvider(self.data_dir / "gmail_auth.json", self.secret_store)

    def _hospitable(self) -> HospitableProvider:
        token = self.secret_store.get_secret("hospitable_pat", "") or self.session_hospitable_token
        return HospitableProvider(token)

    def _shadow_service(self) -> ShadowSyncService:
        gmail = self._gmail() if self.setup_manager.gmail_configured() else None
        return ShadowSyncService(self.db, self.engine, self._hospitable(), gmail)

    def _shadow_ready(self) -> bool:
        return bool(self.secret_store.get_secret("hospitable_pat", "") or self.session_hospitable_token) and bool(self._live_profiles())

    def _build_shell(self):
        top = tk.Frame(self, bg=PANEL, height=62)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="HOST COMMAND CENTER", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(side="left", padx=22)
        self.status_label = tk.Label(top, text="● SAFE SHADOW MODE", bg=PANEL, fg=GOOD, font=("Segoe UI Semibold", 10))
        self.status_label.pack(side="right", padx=22)
        self.clock_label = tk.Label(top, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.clock_label.pack(side="right", padx=8)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=PANEL, width=210)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        self.nav_buttons: dict[str, tk.Button] = {}
        nav = [
            ("Dashboard", "⌂"), ("Approvals", "✓"), ("Properties", "▦"), ("Guests", "✉"),
            ("Pricing", "$"), ("Operations", "⚙"), ("Analytics", "▥"), ("AI Tools", "✦"),
            ("Activity", "≡"), ("Health", "♥"), ("Settings", "⚙"),
        ]
        tk.Label(self.sidebar, text="AUTOMATION", bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=18, pady=(20, 8))
        for name, icon in nav:
            b = tk.Button(
                self.sidebar, text=f" {icon}   {name}", anchor="w", command=lambda n=name: self.show_page(n),
                bg=PANEL, fg=TEXT, activebackground=PANEL_2, activeforeground=TEXT,
                relief="flat", bd=0, font=("Segoe UI", 10), padx=14, pady=10, cursor="hand2",
            )
            b.pack(fill="x", padx=8, pady=1)
            self.nav_buttons[name] = b

        tk.Frame(self.sidebar, bg=BORDER, height=1).pack(fill="x", padx=15, pady=12)
        self.conn_summary = tk.Label(self.sidebar, text="", justify="left", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.conn_summary.pack(anchor="w", padx=18)
        self._refresh_connection_summary()

    def show_page(self, page: str):
        self.current_page = page
        for child in self.content.winfo_children():
            child.destroy()
        for name, b in self.nav_buttons.items():
            b.configure(bg=PANEL_2 if name == page else PANEL, fg=ACCENT if name == page else TEXT)
        getattr(self, f"_page_{page.lower().replace(' ', '_')}")()

    def _page_header(self, parent, title, subtitle=""):
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", padx=28, pady=(24, 16))
        tk.Label(box, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 21)).pack(anchor="w")
        if subtitle:
            tk.Label(box, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        return box

    def _card(self, parent, padx=18, pady=16):
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        frame.configure(padx=padx, pady=pady)
        return frame

    def _metric(self, parent, title, value, detail="", color=TEXT):
        c = self._card(parent)
        tk.Label(c, text=title.upper(), bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Label(c, text=value, bg=PANEL, fg=color, font=("Segoe UI Semibold", 21)).pack(anchor="w", pady=(8, 2))
        tk.Label(c, text=detail, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        return c

    def _button(self, parent, text, command, color=TEXT):
        return tk.Button(parent, text=text, command=command, bg=PANEL_2, fg=color, activebackground=BORDER, activeforeground=color, relief="flat", bd=0, padx=12, pady=7, font=("Segoe UI Semibold", 8), cursor="hand2")

    # ------------------------------------------------------------------
    # DASHBOARD / LIVE DATA
    # ------------------------------------------------------------------
    def _page_dashboard(self):
        self._page_header(self.content, "Dashboard", "V0.4 Shadow Mode uses live Hospitable reads and live Gmail alerts; Airbnb/Hospitable writes stay locked off.")
        approvals = self.db.list_approvals()
        pending = [a for a in approvals if a["state"] == ApprovalState.PENDING_APPROVAL.value]
        waiting = [a for a in approvals if a["state"] == ApprovalState.WAITING_PLATFORM_CONFIRMATION.value]
        remote_res = self.db.list_remote_reservations("hospitable")
        accepted = [r for r in remote_res if reservation_status(r) == "accepted"]
        revenue = sum(reservation_financial_value(r)[0] for r in accepted if reservation_financial_value(r)[1])

        metrics = tk.Frame(self.content, bg=BG)
        metrics.pack(fill="x", padx=28)
        for i in range(4): metrics.grid_columnconfigure(i, weight=1)
        self._metric(metrics, "Pending approvals", str(len(pending)), "Live + developer test requests", WARN if pending else GOOD).grid(row=0, column=0, sticky="nsew", padx=(0,8))
        self._metric(metrics, "Live reservations", str(len(remote_res)), "Cached from Hospitable", ACCENT if remote_res else MUTED).grid(row=0, column=1, sticky="nsew", padx=8)
        self._metric(metrics, "Known booked value", f"${revenue:,.0f}", "Only provider rows with financials", TEXT).grid(row=0, column=2, sticky="nsew", padx=8)
        self._metric(metrics, "System", "SHADOW", "Live reads + alerts; writes blocked", GOOD).grid(row=0, column=3, sticky="nsew", padx=(8,0))

        section = tk.Frame(self.content, bg=BG)
        section.pack(fill="both", expand=True, padx=28, pady=20)
        left = self._card(section); left.pack(side="left", fill="both", expand=True, padx=(0,10))
        right = self._card(section); right.pack(side="left", fill="both", expand=True, padx=(10,0))
        tk.Label(left, text="Properties", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
        profiles = self._visible_profiles()
        if not profiles:
            tk.Label(left, text="No properties synced yet.", bg=PANEL, fg=MUTED).pack(anchor="w", pady=10)
        for p in profiles:
            row = tk.Frame(left, bg=PANEL_2, padx=12, pady=10); row.pack(fill="x", pady=6)
            badge = "LIVE" if p.source == "hospitable" else "DEMO"
            tk.Label(row, text=f"{p.name}   [{badge}]", bg=PANEL_2, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
            facts = []
            if p.bedrooms: facts.append(f"{p.bedrooms}BR")
            if p.bathrooms: facts.append(f"{p.bathrooms:g}BA")
            if p.max_guests: facts.append(f"max {p.max_guests}")
            if p.policy_configured and p.nightly_target: facts.append(f"target ${p.nightly_target:,.0f}/night")
            else: facts.append("automation pricing rules not configured")
            tk.Label(row, text=" • ".join(facts), bg=PANEL_2, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        tk.Label(right, text="Recent activity", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
        for log in self.db.recent_logs(10):
            row = tk.Frame(right, bg=PANEL, pady=5); row.pack(fill="x")
            tk.Label(row, text=log["created_at"][11:19], bg=PANEL, fg=MUTED, font=("Consolas", 8)).pack(side="left")
            tk.Label(row, text=f"  {log['message']}", bg=PANEL, fg=TEXT, font=("Segoe UI", 8), wraplength=430, justify="left").pack(side="left", anchor="w")

    # ------------------------------------------------------------------
    # APPROVALS
    # ------------------------------------------------------------------
    def _page_approvals(self):
        hdr = self._page_header(self.content, "Approvals", "Live request-to-book reservations are reconciled from Hospitable. Your decision is recorded here; the actual accept/decline remains manual.")
        self._button(hdr, "↻ Sync Live Now", lambda: self.sync_shadow_mode(manual=True), ACCENT).pack(side="right", anchor="e")
        scroll = ScrollFrame(self.content); scroll.pack(fill="both", expand=True, padx=28, pady=(0,24))
        rows = self.db.list_approvals()
        if not rows:
            empty = self._card(scroll.inner); empty.pack(fill="x", pady=10)
            tk.Label(empty, text="No approval requests yet", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
            tk.Label(empty, text="Once Hospitable is connected, pending Request-to-Book reservations appear here automatically. Developer simulation is available under Settings.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=900, justify="left").pack(anchor="w", pady=(4,0))
            return
        for row in rows:
            self._approval_card(scroll.inner, row)

    def _approval_card(self, parent, row: dict):
        c = self._card(parent); c.pack(fill="x", pady=8)
        analysis = json.loads(row.get("analysis_json") or "{}")
        rec = analysis.get("recommendation", row.get("recommendation", "REVIEW"))
        color = {"ACCEPT": GOOD, "REVIEW": WARN, "DECLINE": BAD}.get(rec, WARN)
        top = tk.Frame(c, bg=PANEL); top.pack(fill="x")
        source = "LIVE" if row.get("source") == "hospitable" else "TEST"
        tk.Label(top, text=f"{row['property_key']}  •  {row['reservation_id']}  [{source}]", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(side="left")
        tk.Label(top, text=f"{rec}  {analysis.get('score', row.get('score', ''))}/100", bg=PANEL, fg=color, font=("Segoe UI Semibold", 10)).pack(side="right")
        state = row["state"]
        money = f"${row['gross_revenue']:,.0f}" if float(row.get("gross_revenue") or 0) > 0 else "financials unavailable"
        tk.Label(c, text=f"{row['guest_name']}  •  {row['check_in']} → {row['check_out']}  •  {row['guests']} guests  •  {money}", bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", pady=(9,3))
        expires = row.get("expires_at") or ""; exp_text = ""
        if expires:
            try:
                remaining = datetime.fromisoformat(expires) - datetime.now()
                hrs = max(0, int(remaining.total_seconds() // 3600)); mins = max(0, int((remaining.total_seconds() % 3600) // 60))
                exp_text = f" • expires in {hrs}h {mins}m"
            except Exception:
                pass
        tk.Label(c, text=f"STATE: {state}{exp_text}", bg=PANEL, fg=MUTED, font=("Consolas", 8)).pack(anchor="w")
        reasons = analysis.get("reasons", [])[:4]; warnings = analysis.get("warnings", [])[:4]
        if reasons:
            tk.Label(c, text="  ✓ " + "\n  ✓ ".join(reasons), bg=PANEL, fg=GOOD, justify="left", wraplength=900, font=("Segoe UI", 8)).pack(anchor="w", pady=(10,0))
        if warnings:
            tk.Label(c, text="  ⚠ " + "\n  ⚠ ".join(warnings), bg=PANEL, fg=WARN, justify="left", wraplength=900, font=("Segoe UI", 8)).pack(anchor="w", pady=(5,0))
        buttons = tk.Frame(c, bg=PANEL); buttons.pack(fill="x", pady=(14,0))
        if state == ApprovalState.PENDING_APPROVAL.value:
            self._button(buttons, "Approve Decision", lambda r=row["reservation_id"]: self._approval_action("approve", r), GOOD).pack(side="left", padx=(0,6))
            self._button(buttons, "Decline Decision", lambda r=row["reservation_id"]: self._approval_action("decline", r), BAD).pack(side="left", padx=6)
        self._button(buttons, "Open Official Request", lambda r=row["reservation_id"]: self.approvals.open_platform(r), ACCENT).pack(side="left", padx=6)
        self._button(buttons, "Send Approval Email", lambda r=row["reservation_id"]: self.send_email_for(r), TEXT).pack(side="left", padx=6)

    # ------------------------------------------------------------------
    # PROPERTIES
    # ------------------------------------------------------------------
    def _page_properties(self):
        hdr = self._page_header(self.content, "Properties", "Properties come from the live Hospitable/Airbnb connection. You only configure our automation rules on top of them.")
        self._button(hdr, "↻ Sync Hospitable", lambda: self.sync_shadow_mode(manual=True, reservations=False), ACCENT).pack(side="right")
        scroll = ScrollFrame(self.content); scroll.pack(fill="both", expand=True, padx=28, pady=(0,24))
        live = self._live_profiles()
        if not live:
            c = self._card(scroll.inner); c.pack(fill="x", pady=8)
            tk.Label(c, text="No live properties synced", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(anchor="w")
            tk.Label(c, text="Run Setup → Hospitable → Sync Live Properties. The temporary developer profiles are intentionally hidden here.", bg=PANEL, fg=MUTED, wraplength=850, justify="left").pack(anchor="w", pady=(5,0))
            return
        for p in live:
            c = self._card(scroll.inner); c.pack(fill="x", pady=8)
            top = tk.Frame(c, bg=PANEL); top.pack(fill="x")
            tk.Label(top, text=p.name, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 13)).pack(side="left")
            tk.Label(top, text="● LIVE", bg=PANEL, fg=GOOD, font=("Segoe UI Semibold", 9)).pack(side="right")
            tk.Label(c, text=p.address or "Address not exposed in synced property payload", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(3,8))
            facts = f"{p.bedrooms or '?'} BR • {p.bathrooms or '?'} BA • max {p.max_guests or '?'} guests • Hospitable ID {p.hospitable_property_id}"
            tk.Label(c, text=facts, bg=PANEL, fg=TEXT, font=("Segoe UI", 9)).pack(anchor="w")
            rule = (f"Floor ${p.nightly_floor:,.0f} • Target ${p.nightly_target:,.0f} • Ceiling ${p.nightly_ceiling:,.0f} • Cleaning ${p.cleaning_cost:,.0f}" if p.policy_configured else "Automation pricing policy: NOT CONFIGURED — booking recommendations will stay REVIEW")
            tk.Label(c, text=rule, bg=PANEL, fg=GOOD if p.policy_configured else WARN, font=("Segoe UI", 8)).pack(anchor="w", pady=(6,6))
            self._button(c, "Configure Automation Rules", lambda x=p: self.configure_property_rules(x), ACCENT).pack(anchor="w")

    def configure_property_rules(self, prop: PropertyProfile):
        win = tk.Toplevel(self); win.title(f"Automation Rules — {prop.name}"); win.configure(bg=BG); win.geometry("520x440"); win.transient(self); win.grab_set()
        tk.Label(win, text=prop.name, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w", padx=22, pady=(20,4))
        tk.Label(win, text="These are Host Command Center rules, not edits to Airbnb.", bg=BG, fg=MUTED).pack(anchor="w", padx=22, pady=(0,14))
        form = self._card(win); form.pack(fill="both", expand=True, padx=22, pady=(0,22))
        fields = [
            ("Max guests", prop.max_guests), ("Nightly floor", prop.nightly_floor), ("Nightly target", prop.nightly_target),
            ("Nightly ceiling", prop.nightly_ceiling), ("Cleaning cost", prop.cleaning_cost),
        ]
        vars_: list[tk.StringVar] = []
        for i, (label, value) in enumerate(fields):
            tk.Label(form, text=label, bg=PANEL, fg=MUTED).grid(row=i, column=0, sticky="w", pady=8)
            v = tk.StringVar(value=str(value if value else "")); vars_.append(v)
            tk.Entry(form, textvariable=v, bg=PANEL_2, fg=TEXT, insertbackground=TEXT, relief="flat", width=24).grid(row=i, column=1, sticky="w", padx=12)
        def save():
            try:
                max_guests = int(float(vars_[0].get())); floor=float(vars_[1].get()); target=float(vars_[2].get()); ceiling=float(vars_[3].get()); cleaning=float(vars_[4].get() or 0)
                if max_guests <= 0: raise ValueError("Max guests must be greater than zero")
                if min(floor,target,ceiling,cleaning) < 0: raise ValueError("Values cannot be negative")
                if not (floor <= target <= ceiling): raise ValueError("Use floor ≤ target ≤ ceiling")
                prop.max_guests=max_guests; prop.nightly_floor=floor; prop.nightly_target=target; prop.nightly_ceiling=ceiling; prop.cleaning_cost=cleaning; prop.policy_configured=True
                self.db.upsert_property(prop); self.db.log("INFO","PROPERTY","Automation rules updated",details={"property":prop.key})
                win.destroy(); self.show_page("Properties")
            except Exception as exc:
                messagebox.showerror("Rules", str(exc), parent=win)
        self._button(form, "Save Rules", save, GOOD).grid(row=len(fields), column=1, sticky="w", padx=12, pady=(18,0))

    # ------------------------------------------------------------------
    # GUESTS / PRICING / OPERATIONS / ANALYTICS
    # ------------------------------------------------------------------
    def _page_guests(self):
        hdr = self._page_header(self.content, "Guests", "Read-only message history can be synced from live reservations. Sending guest messages remains disabled in Shadow Mode.")
        self._button(hdr, "↻ Sync Messages", self.sync_live_messages, ACCENT).pack(side="right")
        scroll=ScrollFrame(self.content); scroll.pack(fill="both",expand=True,padx=28,pady=(0,24))
        msgs=self.db.list_remote_messages(150)
        if not msgs:
            c=self._card(scroll.inner); c.pack(fill="x",pady=8)
            tk.Label(c,text="No live messages cached yet",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",13)).pack(anchor="w")
            tk.Label(c,text="Click Sync Messages after live reservations have been synced. If the API/account does not permit message reads, the app will show the provider error instead of fabricating data.",bg=PANEL,fg=MUTED,wraplength=900,justify="left").pack(anchor="w",pady=(5,0))
            return
        for m in msgs:
            body = m.get("body") or m.get("message") or m.get("text") or m.get("content") or "(message body unavailable)"
            created = m.get("created_at") or m.get("sent_at") or m.get("timestamp") or m.get("_synced_at","")
            author = m.get("author") or m.get("sender") or {}
            if isinstance(author,dict): author = author.get("name") or author.get("type") or author.get("id") or "Unknown"
            c=self._card(scroll.inner); c.pack(fill="x",pady=5)
            tk.Label(c,text=f"Reservation {m.get('_reservation_id','')} • {author} • {created}",bg=PANEL,fg=MUTED,font=("Segoe UI",8)).pack(anchor="w")
            tk.Label(c,text=str(body),bg=PANEL,fg=TEXT,font=("Segoe UI",9),wraplength=950,justify="left").pack(anchor="w",pady=(5,0))

    def _page_pricing(self):
        self._page_header(self.content, "Pricing", "No simulated pricing is shown. These are your real synced properties and the local guardrails the future pricing engine will use.")
        scroll=ScrollFrame(self.content); scroll.pack(fill="both",expand=True,padx=28,pady=(0,24))
        for p in self._live_profiles():
            c=self._card(scroll.inner); c.pack(fill="x",pady=7)
            tk.Label(c,text=p.name,bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",12)).pack(anchor="w")
            if p.policy_configured:
                tk.Label(c,text=f"Floor ${p.nightly_floor:,.0f}   Target ${p.nightly_target:,.0f}   Ceiling ${p.nightly_ceiling:,.0f}",bg=PANEL,fg=GOOD,font=("Segoe UI",10)).pack(anchor="w",pady=(6,2))
            else:
                tk.Label(c,text="Pricing guardrails not configured",bg=PANEL,fg=WARN).pack(anchor="w",pady=(6,2))
            tk.Label(c,text="LIVE PRICE WRITES: LOCKED OFF. PriceLabs/market recommendations come in a later stage.",bg=PANEL,fg=MUTED,font=("Segoe UI",8)).pack(anchor="w")
        if not self._live_profiles():
            c=self._card(scroll.inner); c.pack(fill="x"); tk.Label(c,text="Sync live properties first.",bg=PANEL,fg=MUTED).pack(anchor="w")

    def _page_operations(self):
        self._page_header(self.content, "Operations", "Upcoming accepted live reservations are used to surface turnover candidates. Task creation is still read-only/planning-only.")
        scroll=ScrollFrame(self.content); scroll.pack(fill="both",expand=True,padx=28,pady=(0,24))
        rows=[]
        for r in self.db.list_remote_reservations("hospitable"):
            if reservation_status(r)!="accepted": continue
            checkout = r.get("check_out") or r.get("checkout") or r.get("checkout_date") or r.get("departure_date")
            if checkout and str(checkout)[:10] >= date.today().isoformat(): rows.append(r)
        if not rows:
            c=self._card(scroll.inner); c.pack(fill="x"); tk.Label(c,text="No upcoming accepted reservations cached yet.",bg=PANEL,fg=MUTED).pack(anchor="w")
            return
        propmap={p.hospitable_property_id:p.name for p in self._live_profiles()}
        for r in rows[:100]:
            checkin=r.get("check_in") or r.get("checkin") or r.get("checkin_date") or "?"; checkout=r.get("check_out") or r.get("checkout") or r.get("checkout_date") or "?"
            pid=reservation_property_id(r); rid=r.get("id") or r.get("uuid") or r.get("code") or ""
            c=self._card(scroll.inner); c.pack(fill="x",pady=5)
            tk.Label(c,text=f"{propmap.get(pid,pid)} • {checkin} → {checkout}",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",10)).pack(anchor="w")
            tk.Label(c,text=f"Reservation {rid} • turnover should be planned after checkout. Automatic cleaner/task writes are not enabled yet.",bg=PANEL,fg=MUTED,font=("Segoe UI",8)).pack(anchor="w",pady=(4,0))

    def _page_analytics(self):
        self._page_header(self.content, "Analytics", "Calculated from the live reservation cache. Missing provider financials are excluded rather than guessed.")
        rows=self.db.list_remote_reservations("hospitable")
        accepted=[r for r in rows if reservation_status(r)=="accepted"]
        known=[reservation_financial_value(r)[0] for r in accepted if reservation_financial_value(r)[1]]
        metrics=tk.Frame(self.content,bg=BG); metrics.pack(fill="x",padx=28)
        for i in range(4): metrics.grid_columnconfigure(i,weight=1)
        self._metric(metrics,"Reservations",str(len(rows)),"Live rows cached").grid(row=0,column=0,sticky="nsew",padx=(0,8))
        self._metric(metrics,"Accepted",str(len(accepted)),"Provider status").grid(row=0,column=1,sticky="nsew",padx=8)
        self._metric(metrics,"Known value",f"${sum(known):,.0f}",f"{len(known)} rows with financials").grid(row=0,column=2,sticky="nsew",padx=8)
        self._metric(metrics,"Pending",str(sum(1 for r in rows if reservation_status(r)=="pending")),"Requests needing review",WARN).grid(row=0,column=3,sticky="nsew",padx=(8,0))

    def _page_ai_tools(self):
        self._page_header(self.content, "AI Tools", "Build prompts from the real synced property profile. Listing edits remain human-approved.")
        scroll=ScrollFrame(self.content); scroll.pack(fill="both",expand=True,padx=28,pady=(0,24))
        for p in self._live_profiles():
            c=self._card(scroll.inner); c.pack(fill="x",pady=7)
            tk.Label(c,text=p.name,bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",12)).pack(anchor="w")
            row=tk.Frame(c,bg=PANEL); row.pack(anchor="w",pady=(10,0))
            self._button(row,"Copy Listing Optimizer Prompt",lambda x=p:self.copy_ai_prompt(x),ACCENT).pack(side="left")
            self._button(row,"Open ChatGPT",self.open_chatgpt,TEXT).pack(side="left",padx=8)
        if not self._live_profiles():
            c=self._card(scroll.inner); c.pack(fill="x"); tk.Label(c,text="Sync live properties first.",bg=PANEL,fg=MUTED).pack(anchor="w")

    def _page_activity(self):
        self._page_header(self.content,"Activity","Every live sync, notification, recommendation, manual decision, and provider error is recorded here.")
        scroll=ScrollFrame(self.content); scroll.pack(fill="both",expand=True,padx=28,pady=(0,24))
        for log in self.db.recent_logs(250):
            c=tk.Frame(scroll.inner,bg=PANEL,padx=12,pady=8); c.pack(fill="x",pady=2)
            color=BAD if log["level"]=="ERROR" else WARN if log["level"]=="WARN" else TEXT
            tk.Label(c,text=f"{log['created_at']}  [{log['category']}]",bg=PANEL,fg=MUTED,font=("Consolas",8)).pack(anchor="w")
            tk.Label(c,text=log["message"],bg=PANEL,fg=color,font=("Segoe UI",9),wraplength=950,justify="left").pack(anchor="w",pady=(3,0))

    # ------------------------------------------------------------------
    # HEALTH / SETTINGS
    # ------------------------------------------------------------------
    def _page_health(self):
        self._page_header(self.content,"System Health","Shadow Mode is allowed to read live data and send host emails. It has no code path for Airbnb accept/decline or pricing writes.")
        c=self._card(self.content); c.pack(fill="x",padx=28,pady=8)
        gmail_ok,gmail_detail=self.setup_manager.gmail_health() if self.setup_manager.gmail_configured() else (False,"Not configured")
        token=bool(self.secret_store.get_secret("hospitable_pat","") or self.session_hospitable_token)
        last_sync=self.db.get_setting("last_reservation_sync_at","")
        items=[
            ("Database",self.db.integrity_check()[0],self.db.integrity_check()[1]),
            ("Gmail",gmail_ok,gmail_detail),
            ("Hospitable token",token,"Stored" if token else "Missing"),
            ("Live properties",bool(self._live_profiles()),f"{len(self._live_profiles())} mapped"),
            ("Live reservations",bool(self.db.list_remote_reservations("hospitable")),f"{len(self.db.list_remote_reservations('hospitable'))} cached"),
            ("Last reconciliation",bool(last_sync),str(last_sync or "Never")),
        ]
        for name,ok,detail in items:
            tk.Label(c,text=f"{'✓' if ok else '○'} {name}: {detail}",bg=PANEL,fg=GOOD if ok else WARN,font=("Segoe UI",9)).pack(anchor="w",pady=3)
        row=tk.Frame(c,bg=PANEL); row.pack(anchor="w",pady=(14,0))
        self._button(row,"Run Robustness Lab",self.run_robustness_lab,ACCENT).pack(side="left")
        self._button(row,"Sync Live Now",lambda:self.sync_shadow_mode(manual=True),GOOD).pack(side="left",padx=8)

    def run_robustness_lab(self):
        def worker():
            try:
                from robustness_lab import run
                results=run(); failed=[r for r in results if not r[1]]
                body="\n".join(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}" for name,ok,detail in results)
                self.after(0,lambda:messagebox.showinfo("Robustness Lab",body) if not failed else messagebox.showerror("Robustness Lab",body))
            except Exception as exc:
                self.after(0,lambda:messagebox.showerror("Robustness Lab",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _page_settings(self):
        self._page_header(self.content,"Settings","Run Setup here. Live reads and host email notifications are enabled after setup; all Airbnb/Hospitable writes remain locked off.")
        c=self._card(self.content); c.pack(fill="x",padx=28,pady=8)
        tk.Label(c,text="Guided Setup",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",13)).pack(anchor="w")
        self._button(c,"Run Setup Wizard",self.run_setup_wizard,ACCENT).pack(anchor="w",pady=(10,2))
        tk.Label(c,text="Safe Mode — LOCKED: accept/decline, guest-message sending, pricing writes and remote task writes are disabled.",bg=PANEL,fg=GOOD,font=("Segoe UI Semibold",9),wraplength=950,justify="left").pack(anchor="w",pady=(8,0))

        c2=self._card(self.content); c2.pack(fill="x",padx=28,pady=8)
        tk.Label(c2,text="Host Email Notifications",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",13)).grid(row=0,column=0,columnspan=3,sticky="w")
        tk.Label(c2,text="Approval recipient",bg=PANEL,fg=MUTED).grid(row=1,column=0,sticky="w",pady=10)
        self.recipient_var=tk.StringVar(value=self.db.get_setting("notification_recipient",""))
        tk.Entry(c2,textvariable=self.recipient_var,bg=PANEL_2,fg=TEXT,insertbackground=TEXT,relief="flat",width=42).grid(row=1,column=1,sticky="w",padx=10)
        self._button(c2,"Save",self.save_recipient,ACCENT).grid(row=1,column=2,padx=5)
        self._button(c2,"Send LIVE Test Email",self.send_live_test_email,GOOD).grid(row=2,column=1,sticky="w",padx=10,pady=(2,8))
        oauth_status="Configured" if self.setup_manager.gmail_configured() else "Not configured"
        tk.Label(c2,text=f"Gmail OAuth: {oauth_status}",bg=PANEL,fg=GOOD if oauth_status=="Configured" else WARN).grid(row=3,column=0,columnspan=2,sticky="w",pady=(5,2))

        c3=self._card(self.content); c3.pack(fill="x",padx=28,pady=8)
        tk.Label(c3,text="Hospitable / Airbnb Data",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",13)).grid(row=0,column=0,columnspan=3,sticky="w")
        tk.Label(c3,text="PAT (encrypted for this Windows user)",bg=PANEL,fg=MUTED).grid(row=1,column=0,sticky="w",pady=10)
        self.hospitable_var=tk.StringVar(value=self.secret_store.get_secret("hospitable_pat","") or self.session_hospitable_token)
        tk.Entry(c3,textvariable=self.hospitable_var,show="•",bg=PANEL_2,fg=TEXT,insertbackground=TEXT,relief="flat",width=45).grid(row=1,column=1,sticky="w",padx=10)
        self._button(c3,"Test Connection",self.test_hospitable,ACCENT).grid(row=1,column=2,padx=5)
        self._button(c3,"Sync Live Properties + Reservations",lambda:self.sync_shadow_mode(manual=True),GOOD).grid(row=2,column=1,sticky="w",padx=10,pady=(2,5))
        tk.Label(c3,text="Public API: https://public.api.hospitable.com/v2 • Shadow Mode reads only",bg=PANEL,fg=MUTED,font=("Consolas",8)).grid(row=3,column=0,columnspan=3,sticky="w")

        c4=self._card(self.content); c4.pack(fill="x",padx=28,pady=8)
        tk.Label(c4,text="Developer / Recovery Tools",bg=PANEL,fg=TEXT,font=("Segoe UI Semibold",13)).pack(anchor="w")
        tk.Label(c4,text="These do not represent live Airbnb data.",bg=PANEL,fg=MUTED,font=("Segoe UI",8)).pack(anchor="w",pady=(3,8))
        row=tk.Frame(c4,bg=PANEL); row.pack(anchor="w")
        self._button(row,"Simulate Booking",self.simulate_booking,TEXT).pack(side="left")
        self._button(row,"Write Simulation Email to Outbox",self.send_simulation_test_email,TEXT).pack(side="left",padx=8)
        self._button(row,"Open Data Folder",self.open_data_folder,TEXT).pack(side="left")

    # ------------------------------------------------------------------
    # ACTIONS / LIVE SYNC
    # ------------------------------------------------------------------
    def _approval_action(self, action: str, reservation_id: str):
        try:
            if action=="approve":
                self.approvals.approve(reservation_id)
                messagebox.showinfo("Decision recorded","Approval recorded. Shadow Mode did NOT send an accept action. Open the official request to confirm manually.")
            else:
                self.approvals.decline(reservation_id)
                messagebox.showinfo("Decision recorded","Decline decision recorded. Shadow Mode did NOT send a decline action.")
        except Exception as exc:
            messagebox.showerror("Approval error",str(exc))
        self.show_page("Approvals")

    def send_email_for(self,reservation_id:str):
        recipient=str(self.db.get_setting("notification_recipient","") or "").strip()
        if not recipient:
            messagebox.showwarning("Recipient needed","Set an approval recipient in Settings first."); return
        if not self.setup_manager.gmail_configured():
            messagebox.showerror("Gmail not connected","Run Setup and authorize Gmail first."); return
        row=self.db.get_approval(reservation_id)
        if not row: return
        def worker():
            try:
                msg=NotificationService(self.db,self._gmail()).send_approval_email(row,recipient)
                self.after(0,lambda:messagebox.showinfo("Email sent",f"Live Gmail message sent.\nMessage ID: {msg}"))
            except Exception as exc:
                self.after(0,lambda:messagebox.showerror("Email failed",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def save_recipient(self):
        try:
            self.setup_manager.save_recipient(self.recipient_var.get())
            messagebox.showinfo("Saved","Approval recipient saved.")
        except Exception as exc:
            messagebox.showerror("Recipient",str(exc))

    def send_live_test_email(self):
        recipient=(self.recipient_var.get().strip() if hasattr(self,"recipient_var") else str(self.db.get_setting("notification_recipient","") or "").strip())
        if not recipient:
            messagebox.showwarning("Recipient needed","Enter and save an approval recipient first."); return
        if not self.setup_manager.gmail_configured():
            messagebox.showerror("Gmail not connected","Run Setup Wizard and authorize Gmail first."); return
        def worker():
            try:
                self.setup_manager.save_recipient(recipient)
                msg=self.setup_manager.send_live_test_email()
                self.after(0,self._refresh_connection_summary)
                self.after(0,lambda:messagebox.showinfo("Live Email",f"Sent successfully to {recipient}.\nMessage ID: {msg}"))
            except Exception as exc:
                self.db.log("ERROR","EMAIL","Live Gmail test failed",details={"error":str(exc)})
                self.after(0,lambda:messagebox.showerror("Live Email",str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def send_simulation_test_email(self):
        recipient=str(self.db.get_setting("notification_recipient","") or "test@example.com")
        msg=MockEmailProvider(self.data_dir/"outbox").send(recipient,"Host Command Center — Simulation Outbox","This file is only a developer simulation and was not sent externally.")
        messagebox.showinfo("Simulation Outbox",f"Created:\n{msg}")

    def test_hospitable(self):
        self.session_hospitable_token=self.hospitable_var.get().strip()
        if not self.session_hospitable_token:
            messagebox.showwarning("Token needed","Paste a Hospitable Personal Access Token."); return
        try:
            self.setup_manager.save_hospitable_token(self.session_hospitable_token)
        except Exception as exc:
            messagebox.showerror("Hospitable",str(exc)); return
        self._refresh_connection_summary()
        def worker():
            ok,detail=self.setup_manager.hospitable_health()
            self.db.log("INFO" if ok else "ERROR","HOSPITABLE",f"Connection test: {detail}")
            self.after(0,lambda:messagebox.showinfo("Hospitable",detail) if ok else messagebox.showerror("Hospitable",detail))
        threading.Thread(target=worker,daemon=True).start()

    def sync_shadow_mode(self, manual: bool=False, reservations: bool=True):
        if self._sync_in_progress:
            if manual: messagebox.showinfo("Shadow Sync","A live sync is already running.")
            return
        token=self.secret_store.get_secret("hospitable_pat","") or self.session_hospitable_token
        if not token:
            if manual: messagebox.showwarning("Hospitable","Run Setup and connect Hospitable first.")
            return
        self._sync_in_progress=True; self._last_auto_sync_attempt=datetime.now()
        def worker():
            try:
                svc=self._shadow_service(); pcount=svc.sync_properties(); stats=None
                if reservations: stats=svc.sync_reservations(notify=True)
                detail=f"{pcount} live properties synced"
                if stats is not None:
                    detail += f"\n{stats['fetched']} reservations reconciled\n{stats['pending']} pending • {stats['accepted']} accepted • {stats['emailed']} new approval email(s)"
                self.db.log("INFO","SHADOW", "Shadow Mode live sync completed",details=stats or {"properties":pcount})
                self.after(0,self._refresh_connection_summary)
                if manual: self.after(0,lambda:messagebox.showinfo("Shadow Sync",detail))
                if self.current_page in {"Dashboard","Approvals","Properties","Analytics","Operations"}:
                    self.after(0,lambda:self.show_page(self.current_page))
                self.after(0,self._archive_webhook_inbox)
            except Exception as exc:
                self.db.log("ERROR","SHADOW","Shadow Mode sync failed",details={"error":str(exc)})
                if manual: self.after(0,lambda:messagebox.showerror("Shadow Sync",str(exc)))
            finally:
                self._sync_in_progress=False
        threading.Thread(target=worker,daemon=True).start()

    def sync_live_messages(self):
        token=self.secret_store.get_secret("hospitable_pat","") or self.session_hospitable_token
        if not token:
            messagebox.showwarning("Hospitable","Connect Hospitable first."); return
        reservations=self.db.list_remote_reservations("hospitable",limit=30)
        if not reservations:
            messagebox.showinfo("Messages","Sync live reservations first."); return
        def worker():
            provider=self._hospitable(); total=0; errors=[]
            for r in reservations[:20]:
                rid=str(r.get("id") or r.get("uuid") or r.get("reservation_id") or r.get("code") or "").strip()
                if not rid: continue
                try:
                    msgs=provider.list_messages(rid); total += self.db.save_remote_messages("hospitable",rid,msgs)
                except Exception as exc:
                    errors.append(f"{rid}: {exc}")
                    if len(errors)>=3: break
            self.db.log("INFO" if not errors else "WARN","MESSAGES",f"Read-only message sync stored {total} messages",details={"errors":errors})
            if errors:
                self.after(0,lambda:messagebox.showwarning("Message Sync",f"Stored {total} messages.\n\nSome provider reads failed:\n"+"\n".join(errors)))
            else:
                self.after(0,lambda:messagebox.showinfo("Message Sync",f"Stored {total} live messages."))
            if self.current_page=="Guests": self.after(0,lambda:self.show_page("Guests"))
        threading.Thread(target=worker,daemon=True).start()

    def simulate_booking(self):
        props=self._visible_profiles()
        if not props:
            messagebox.showwarning("Simulation","No property profile exists."); return
        prop=random.choice(props)
        booking=self.engine.simulated_booking(prop,random.choice(["Demo Guest","Taylor","Jordan","Morgan"]))
        analysis=self.engine.ingest_booking_request(booking,prop)
        if analysis: messagebox.showinfo("Developer Simulation",f"Created {booking.reservation_id}\nRecommendation: {analysis.recommendation.value} ({analysis.score}/100)\n\nThis is NOT live Airbnb data.")
        self.show_page("Approvals")

    def _maybe_start_shadow_mode(self):
        if self.secret_store.get_secret("hospitable_pat",""):
            self.sync_shadow_mode(manual=False)
        interval=int(self.db.get_setting("shadow_poll_seconds",300) or 300)
        interval=max(60,min(3600,interval))
        self.after(interval*1000,self._maybe_start_shadow_mode)

    def _check_webhook_inbox(self):
        inbox=self.data_dir/"webhook_inbox"
        if inbox.exists() and any(inbox.glob("*.json")) and not self._sync_in_progress:
            # Webhooks are treated as a trigger; reconciliation fetches current
            # provider state instead of trusting an incomplete/stale payload.
            self.db.log("INFO","WEBHOOK","Webhook file detected; triggering provider reconciliation")
            self.sync_shadow_mode(manual=False)
        self.after(10000,self._check_webhook_inbox)

    def _archive_webhook_inbox(self):
        inbox=self.data_dir/"webhook_inbox"; done=self.data_dir/"webhook_processed"; done.mkdir(parents=True,exist_ok=True)
        if not inbox.exists(): return
        for p in inbox.glob("*.json"):
            try: p.replace(done/p.name)
            except OSError: pass

    # ------------------------------------------------------------------
    # MISC
    # ------------------------------------------------------------------
    def copy_ai_prompt(self,prop:PropertyProfile):
        prompt=f"""You are optimizing an Airbnb listing for this real synced property. Keep every claim accurate and do not invent amenities.\n\nProperty: {prop.name}\nAddress/market: {prop.address}\nBedrooms: {prop.bedrooms or 'unknown'}\nBathrooms: {prop.bathrooms or 'unknown'}\nMax guests: {prop.max_guests or 'unknown'}\nConfigured ADR floor/target/ceiling: ${prop.nightly_floor:.0f} / ${prop.nightly_target:.0f} / ${prop.nightly_ceiling:.0f}\n\nTask:\n1. Ask me for the current Airbnb title, description, amenities, photos, and recent performance if missing.\n2. Propose stronger title options and a revised description.\n3. Explain meaningful changes.\n4. Do not exaggerate or invent features.\n5. Design a measurable KEEP/REVERT test using views, conversion, ADR, occupancy and revenue.\n"""
        self.clipboard_clear(); self.clipboard_append(prompt); self.update(); messagebox.showinfo("Copied",f"Optimization prompt for {prop.name} copied.")

    def open_chatgpt(self): webbrowser.open("https://chatgpt.com/")

    def open_data_folder(self):
        path=str(self.data_dir.resolve())
        if sys.platform.startswith("win"): os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform=="darwin": subprocess.Popen(["open",path])
        else: subprocess.Popen(["xdg-open",path])

    def run_setup_wizard(self): SetupWizard(self,self.setup_manager,on_change=self._refresh_connection_summary)

    def _refresh_connection_summary(self):
        gmail=self.setup_manager.gmail_configured(); hosp=bool(self.secret_store.get_secret("hospitable_pat","") or self.session_hospitable_token)
        props=len(self._live_profiles()); res=len(self.db.list_remote_reservations("hospitable")); last=self.db.get_setting("last_reservation_sync_at","")
        lines=[
            f"{'●' if hosp and props else '○'} Shadow {'LIVE-READ' if hosp and props else 'setup'}",
            f"{'●' if hosp else '○'} Hospitable {'connected' if hosp else 'offline'}",
            f"{'●' if gmail else '○'} Gmail {'configured' if gmail else 'offline'}",
            f"{'●' if props else '○'} Live properties {props}",
            f"{'●' if res else '○'} Reservations {res}",
        ]
        if last: lines.append(f"Last sync {str(last)[11:19] if len(str(last))>19 else last}")
        if hasattr(self,"conn_summary"): self.conn_summary.configure(text="\n".join(lines))
        if hasattr(self,"status_label"):
            self.status_label.configure(text="● LIVE SHADOW MODE" if hosp and props else "● SAFE SETUP MODE",fg=GOOD if hosp and props else WARN)

    def _tick(self):
        self.clock_label.configure(text=datetime.now().strftime("%a %b %d  •  %I:%M:%S %p"))
        self.after(1000,self._tick)
