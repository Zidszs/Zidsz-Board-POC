"""
Scout Gate — GUI cliente (MITM + redireccionamentos).
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk

try:
    import requests
except ImportError:
    requests = None

if sys.platform == "win32":
    try:
        import winsound
    except ImportError:
        winsound = None
else:
    winsound = None

from scout.client.ws_client import ScoutRemoteClient, check_backend_http


class ScoutGateApp:
    UI_MS = 100

    def __init__(self, root):
        self.root = root
        self.root.title("Scout Gate v5.2")
        self.root.geometry("1200x800")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.backend_url = os.environ.get(
            "SCOUT_BACKEND_URL", "ws://127.0.0.1:8765/ws"
        )
        self.remote_client = None
        self.ui_queue = queue.Queue()
        self._redir_rows = {}
        self._redir_mode_vars = {}
        self._redir_enabled_vars = {}
        self._redir_snapshot = {}
        self._redir_editing = {}
        self._traffic_index = {}
        self._client_index = {}
        self._mode_options = ("tcp", "http", "https")

        self.build_ui()
        self.root.after(self.UI_MS, self.process_ui_queue)
        self.root.after(600, self.connect_remote)

    def process_ui_queue(self):
        try:
            for _ in range(60):
                if self.ui_queue.empty():
                    break
                self.ui_queue.get_nowait()()
        except Exception:
            pass
        finally:
            self.root.after(self.UI_MS, self.process_ui_queue)

    def enqueue(self, fn):
        self.ui_queue.put(fn)

    def _n8groker_supervisor_active(self) -> bool:
        root = os.environ.get("N8GROKER_ROOT", "").strip()
        if not root or os.environ.get("SCOUT_DOCKER_MANAGED") != "1":
            return False
        return (Path(root) / ".n8groker.supervisor.lock").is_file()

    def _request_n8groker_shutdown(self) -> bool:
        root = os.environ.get("N8GROKER_ROOT", "").strip()
        if not root:
            return False
        payload = {
            "source": "scout_gui",
            "at": datetime.now(timezone.utc).isoformat(),
        }
        path = Path(root) / ".n8groker.shutdown.request"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return True

    def on_close(self):
        if self._n8groker_supervisor_active():
            choice = messagebox.askyesnocancel(
                "Encerrar",
                "Deseja encerrar toda a infraestrutura N8Groker?\n"
                "(Ngrok, n8n, Scout e Porteiro)\n\n"
                "Sim = encerrar tudo\n"
                "Nao = fechar apenas esta janela\n"
                "Cancelar = voltar",
                parent=self.root,
            )
            if choice is None:
                return
            if choice:
                self._request_n8groker_shutdown()
        if self.remote_client:
            self.remote_client.stop()
        self.root.destroy()

    def build_ui(self):
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=10, pady=8)
        tk.Label(hdr, text="SCOUT GATE", font=("Consolas", 18, "bold")).pack()
        self.lbl_status = tk.Label(hdr, text="Backend: a ligar...", fg="#888")
        self.lbl_status.pack()
        self.lbl_summary = tk.Label(hdr, text="", fg="#00ccff", font=("Consolas", 10))
        self.lbl_summary.pack()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both", padx=10, pady=5)

        self.tab_redir = ttk.Frame(self.notebook)
        self.tab_traffic = ttk.Frame(self.notebook)
        self.tab_clients = ttk.Frame(self.notebook)
        self.tab_config = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_redir, text="Redireccionamentos")
        self.notebook.add(self.tab_traffic, text="Tráfego")
        self.notebook.add(self.tab_clients, text="Clientes")
        self.notebook.add(self.tab_config, text="Config")

        self._setup_redir_tab()
        self._setup_traffic_tab()
        self._setup_clients_tab()
        self._setup_config_tab()

        logf = ttk.Frame(self.root)
        logf.pack(fill="x", padx=10, pady=6)
        self.log_area = scrolledtext.ScrolledText(logf, height=4, font=("Consolas", 9))
        self.log_area.pack(fill="x")

    def _setup_redir_tab(self):
        f = ttk.Frame(self.tab_redir)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        ctrl = ttk.Frame(f)
        ctrl.pack(fill="x", pady=4)
        ttk.Button(ctrl, text="Sync Docker", command=self._sync_docker).pack(side="left", padx=4)
        ttk.Button(ctrl, text="+ Porta manual", command=self._add_manual).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Sync Firewall", command=self._firewall_sync).pack(side="left", padx=4)

        ttk.Label(
            f,
            text=(
                "Rotas activas: selector de método (tcp/http/https). "
                "Portas manuais: edite nome, porta Scout e upstream (host:porta) — Enter ou sair do campo grava."
            ),
            wraplength=900,
        ).pack(anchor="w", pady=(0, 6))

        table_wrap = ttk.Frame(f)
        table_wrap.pack(fill="both", expand=True)

        self._redir_canvas = tk.Canvas(table_wrap, highlightthickness=0)
        sb = ttk.Scrollbar(table_wrap, orient="vertical", command=self._redir_canvas.yview)
        self.redir_inner = ttk.Frame(self._redir_canvas)
        self.redir_inner.bind(
            "<Configure>",
            lambda e: self._redir_canvas.configure(scrollregion=self._redir_canvas.bbox("all")),
        )
        self._redir_canvas.create_window((0, 0), window=self.redir_inner, anchor="nw")
        self._redir_canvas.configure(yscrollcommand=sb.set)
        self._redir_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        headers = ("Modo", "Nome", "Origem", "Porta Scout", "Upstream", "Activo", "Estado")
        for col, text in enumerate(headers):
            ttk.Label(self.redir_inner, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=col, padx=4, pady=4, sticky="w"
            )

    def _setup_traffic_tab(self):
        f = ttk.Frame(self.tab_traffic)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("time", "client", "route", "mode", "upstream", "bytes", "blocked")
        self.tree_traffic = ttk.Treeview(f, columns=cols, show="headings", height=14)
        for c, h in zip(cols, ("Hora", "Cliente", "Rota", "Modo", "Upstream", "Bytes", "Bloq.")):
            self.tree_traffic.heading(c, text=h)
            self.tree_traffic.column(c, width=100 if c != "client" else 140)
        self.tree_traffic.pack(fill="both", expand=True)
        self.tree_traffic.bind("<Button-3>", self._traffic_menu)

        af = ttk.LabelFrame(f, text="Alertas")
        af.pack(fill="x", pady=6)
        self.alert_area = scrolledtext.ScrolledText(af, height=5, font=("Consolas", 9), bg="#1a0000", fg="#ff6666")
        self.alert_area.pack(fill="x")

    def _setup_clients_tab(self):
        f = ttk.Frame(self.tab_clients)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(
            f,
            text=(
                "IPs vistos na borda — persistidos em known_clients.json. "
                "Clique direito para alias, bloquear ou remover da lista."
            ),
            wraplength=900,
        ).pack(anchor="w", pady=(0, 6))
        cols = ("ip", "alias", "last_seen", "mode", "route", "sessions", "bytes", "blocked")
        self.tree_clients = ttk.Treeview(f, columns=cols, show="headings", height=18)
        headers = ("IP", "Alias", "Última interacção", "Modo", "Rota", "Sessões", "Bytes", "Bloq.")
        widths = (120, 120, 150, 60, 100, 70, 90, 50)
        for c, h, w in zip(cols, headers, widths):
            self.tree_clients.heading(c, text=h)
            self.tree_clients.column(c, width=w)
        self.tree_clients.tag_configure("unknown", foreground="#ffaa44")
        self.tree_clients.tag_configure("trusted", foreground="#66cc66")
        self.tree_clients.pack(fill="both", expand=True)
        self.tree_clients.bind("<Button-3>", self._clients_menu)

    def _setup_config_tab(self):
        f = ttk.Frame(self.tab_config)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(f, text="Backend WebSocket:").pack(anchor="w")
        self.ent_backend = ttk.Entry(f, width=50)
        self.ent_backend.insert(0, self.backend_url)
        self.ent_backend.pack(anchor="w", pady=4)
        ttk.Button(f, text="Religar", command=self.connect_remote).pack(anchor="w", pady=4)
        self.txt_config = scrolledtext.ScrolledText(f, height=12, font=("Consolas", 10))
        self.txt_config.pack(fill="both", expand=True, pady=8)
        ttk.Button(f, text="Health Check", command=self._health_check).pack(anchor="w")

    def log(self, msg):
        self.log_area.insert(tk.END, msg + "\n")
        self.log_area.see(tk.END)

    def connect_remote(self):
        if self.remote_client:
            self.remote_client.stop()
        self.backend_url = self.ent_backend.get().strip() if hasattr(self, "ent_backend") else self.backend_url
        try:
            self.remote_client = ScoutRemoteClient(
                url=self.backend_url,
                on_tick=lambda d: self.enqueue(lambda: self._render_tick(d)),
                on_event=lambda e: self.enqueue(lambda: self._on_event(e)),
            )
            self.remote_client.start()
            self.log("A ligar ao Scout Gate...")
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _on_event(self, ev):
        t = ev.get("type")
        if t == "connected":
            self.lbl_status.config(text="Backend: ONLINE", fg="#00ff00")
            self.log("Ligado.")
        elif t == "disconnected":
            self.lbl_status.config(text="Backend: OFFLINE", fg="#cc4400")

    def _render_tick(self, data):
        st = data.get("stats", {})
        sm = st.get("summary", {})
        self.lbl_summary.config(
            text=f"Activos: {sm.get('active', 0)} | Total: {sm.get('total', 0)} | Novos: {sm.get('new', 0)}"
        )
        self._render_redirections(data.get("redirections", []))
        self._render_traffic(data.get("traffic", []))
        self._render_clients(data.get("clients", []))
        for a in data.get("alerts", []):
            line = f"[{a.get('time', '')}] {a.get('msg', '')}\n"
            if line not in self.alert_area.get("1.0", tk.END):
                self.alert_area.insert(tk.END, line)
                self.alert_area.see(tk.END)
                if winsound:
                    try:
                        winsound.MessageBeep()
                    except Exception:
                        pass
        cfg = (
            f"Ngrok: {st.get('ngrok_url') or '(não definido)'}\n"
            f"Porta pública Scout: {st.get('public_port')}\n"
            f"Admin API: {st.get('admin_port')}\n"
            f"Firewall: {'activo' if st.get('enforce_firewall') else 'inactivo'}\n"
            f"Docker resolver: {'OK' if st.get('docker_ok') else 'indisponível'}\n"
            f"Containers: {st.get('containers', 0)}\n"
        )
        self.txt_config.delete("1.0", tk.END)
        self.txt_config.insert(tk.END, cfg)

    def _api_base(self):
        return self.backend_url.replace("ws://", "http://").replace("/ws", "")

    def _render_redirections(self, rows):
        seen = set()
        for idx, row in enumerate(rows, start=1):
            eid = row.get("id", "")
            if not eid:
                continue
            seen.add(eid)
            self._ensure_redir_row(idx, eid, row)

        stale = [k for k in self._redir_rows if k not in seen]
        for eid in stale:
            widgets = self._redir_rows.pop(eid, {})
            self._redir_mode_vars.pop(eid, None)
            self._redir_enabled_vars.pop(eid, None)
            self._redir_snapshot.pop(eid, None)
            self._redir_editing.pop(eid, None)
            frame = widgets.get("frame")
            if frame:
                frame.destroy()

    def _ensure_redir_row(self, grid_row: int, eid: str, row: dict):
        enabled = row.get("enabled", False)
        is_docker = row.get("source") == "docker"
        is_manual = row.get("source") == "manual"
        is_new = row.get("is_new", False)
        upstream = f"{row.get('upstream_host')}:{row.get('upstream_port')}"
        status = "NOVO" if is_new else ("ACTIVO" if enabled else "INACTIVO")
        mode = row.get("mode", "tcp")
        if mode not in self._mode_options:
            mode = "tcp"

        if eid not in self._redir_rows:
            frame = ttk.Frame(self.redir_inner)
            mode_var = tk.StringVar(value=mode)
            enabled_var = tk.BooleanVar(value=enabled)

            mode_cb = ttk.Combobox(
                frame,
                textvariable=mode_var,
                values=self._mode_options,
                width=8,
                state="readonly",
            )
            mode_cb.bind(
                "<<ComboboxSelected>>",
                lambda _e, entry_id=eid, var=mode_var: self._on_mode_change(entry_id, var.get()),
            )

            ent_name = ttk.Entry(frame, width=14)
            lbl_source = ttk.Label(frame, text="", width=8)
            ent_listen = ttk.Entry(frame, width=8)
            ent_upstream = ttk.Entry(frame, width=18)
            chk = ttk.Checkbutton(
                frame,
                variable=enabled_var,
                command=lambda entry_id=eid, var=enabled_var: self._on_toggle(entry_id, var.get()),
            )
            lbl_status = ttk.Label(frame, text="", width=10)

            mode_cb.grid(row=0, column=0, padx=4, pady=2)
            ent_name.grid(row=0, column=1, padx=4, sticky="w")
            lbl_source.grid(row=0, column=2, padx=4, sticky="w")
            ent_listen.grid(row=0, column=3, padx=4, sticky="w")
            ent_upstream.grid(row=0, column=4, padx=4, sticky="w")
            chk.grid(row=0, column=5, padx=4)
            lbl_status.grid(row=0, column=6, padx=4, sticky="w")

            frame.grid(row=grid_row, column=0, sticky="ew", pady=1)
            frame.bind("<Button-3>", lambda e, entry_id=eid: self._redir_menu(e, entry_id))

            for field, widget in (
                ("name", ent_name),
                ("listen", ent_listen),
                ("upstream", ent_upstream),
            ):
                widget.bind("<FocusIn>", lambda _e, entry_id=eid, f=field: self._mark_editing(entry_id, f, True))
                widget.bind(
                    "<FocusOut>",
                    lambda _e, entry_id=eid, f=field: self._on_manual_focus_out(entry_id, f),
                )
                widget.bind("<Return>", lambda _e, entry_id=eid: self._commit_manual_row(entry_id))

            self._redir_rows[eid] = {
                "frame": frame,
                "mode_cb": mode_cb,
                "ent_name": ent_name,
                "lbl_source": lbl_source,
                "ent_listen": ent_listen,
                "ent_upstream": ent_upstream,
                "lbl_status": lbl_status,
                "chk": chk,
            }
            self._redir_mode_vars[eid] = mode_var
            self._redir_enabled_vars[eid] = enabled_var
        else:
            widgets = self._redir_rows[eid]
            widgets["frame"].grid(row=grid_row, column=0, sticky="ew", pady=1)
            mode_var = self._redir_mode_vars[eid]
            enabled_var = self._redir_enabled_vars[eid]
            if mode_var.get() != mode:
                mode_var.set(mode)
            if enabled_var.get() != enabled:
                enabled_var.set(enabled)

        widgets = self._redir_rows[eid]
        widgets["is_manual"] = is_manual
        widgets["lbl_source"].config(text=row.get("source", ""))
        widgets["lbl_status"].config(text=status)

        editing = self._redir_editing.get(eid, set())
        snap = {
            "name": row.get("name", ""),
            "listen_port": row.get("listen_port", ""),
            "upstream": upstream,
        }
        self._redir_snapshot[eid] = snap

        if is_manual:
            widgets["ent_name"].config(state="normal")
            widgets["ent_listen"].config(state="normal")
            widgets["ent_upstream"].config(state="normal")
            if "name" not in editing and widgets["ent_name"].get() != snap["name"]:
                widgets["ent_name"].delete(0, tk.END)
                widgets["ent_name"].insert(0, snap["name"])
            if "listen" not in editing:
                listen_txt = str(snap["listen_port"])
                if widgets["ent_listen"].get() != listen_txt:
                    widgets["ent_listen"].delete(0, tk.END)
                    widgets["ent_listen"].insert(0, listen_txt)
            if "upstream" not in editing and widgets["ent_upstream"].get() != upstream:
                widgets["ent_upstream"].delete(0, tk.END)
                widgets["ent_upstream"].insert(0, upstream)
        else:
            for ent in (widgets["ent_name"], widgets["ent_listen"], widgets["ent_upstream"]):
                ent.config(state="normal")
            widgets["ent_name"].delete(0, tk.END)
            widgets["ent_name"].insert(0, row.get("name", ""))
            widgets["ent_listen"].delete(0, tk.END)
            widgets["ent_listen"].insert(0, str(row.get("listen_port", "")))
            widgets["ent_upstream"].delete(0, tk.END)
            widgets["ent_upstream"].insert(0, upstream)
            for ent in (widgets["ent_name"], widgets["ent_listen"], widgets["ent_upstream"]):
                ent.config(state="readonly")

        mode_cb = widgets["mode_cb"]
        if enabled and (is_docker or is_manual):
            mode_cb.config(state="readonly")
        else:
            mode_cb.config(state="disabled")
            self._redir_mode_vars[eid].set(mode)

    def _mark_editing(self, eid: str, field: str, active: bool):
        if active:
            self._redir_editing.setdefault(eid, set()).add(field)
        else:
            fields = self._redir_editing.get(eid)
            if fields:
                fields.discard(field)
                if not fields:
                    self._redir_editing.pop(eid, None)

    def _on_manual_focus_out(self, eid: str, field: str):
        self._mark_editing(eid, field, False)
        self._commit_manual_row(eid)

    def _parse_upstream(self, text: str):
        text = text.strip()
        if not text:
            raise ValueError("upstream vazio")
        if ":" not in text:
            raise ValueError("upstream deve ser host:porta")
        host, _, port_s = text.rpartition(":")
        host = host.strip()
        port_s = port_s.strip()
        if not host:
            raise ValueError("host inválido")
        port = int(port_s)
        if port < 1 or port > 65535:
            raise ValueError("porta inválida")
        return host, port

    def _commit_manual_row(self, eid: str):
        widgets = self._redir_rows.get(eid)
        if not widgets or not widgets.get("is_manual"):
            return
        snap = self._redir_snapshot.get(eid, {})
        name = widgets["ent_name"].get().strip()
        listen_s = widgets["ent_listen"].get().strip()
        upstream_s = widgets["ent_upstream"].get().strip()

        payload = {}
        if name and name != snap.get("name"):
            payload["name"] = name
        try:
            listen_port = int(listen_s)
            if listen_port < 1 or listen_port > 65535:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Porta Scout", "Porta Scout inválida (1–65535).", parent=self.root)
            widgets["ent_listen"].delete(0, tk.END)
            widgets["ent_listen"].insert(0, str(snap.get("listen_port", "")))
            return
        if str(listen_port) != str(snap.get("listen_port")):
            payload["listen_port"] = listen_port
        try:
            host, port = self._parse_upstream(upstream_s)
        except ValueError as e:
            messagebox.showerror("Upstream", str(e), parent=self.root)
            widgets["ent_upstream"].delete(0, tk.END)
            widgets["ent_upstream"].insert(0, snap.get("upstream", ""))
            return
        if (
            self._n8groker_supervisor_active()
            and host in ("127.0.0.1", "localhost")
            and port in (5677, 5678)
        ):
            messagebox.showwarning(
                "Upstream N8Groker",
                "Dentro do container Scout, 127.0.0.1 nao alcanca o Porteiro/n8n no host.\n"
                "Use host.docker.internal:" + str(port),
                parent=self.root,
            )
            host = "host.docker.internal"
            upstream_s = f"{host}:{port}"
            widgets["ent_upstream"].delete(0, tk.END)
            widgets["ent_upstream"].insert(0, upstream_s)
        if upstream_s != snap.get("upstream"):
            payload["upstream_host"] = host
            payload["upstream_port"] = port
        if not payload:
            return
        if not requests:
            return
        base = self._api_base()
        try:
            r = requests.patch(f"{base}/redirections/{eid}", json=payload, timeout=5)
            if r.ok:
                entry = r.json().get("entry", {})
                new_snap = {
                    "name": entry.get("name", name),
                    "listen_port": entry.get("listen_port", listen_port),
                    "upstream": f"{entry.get('upstream_host')}:{entry.get('upstream_port')}",
                }
                self._redir_snapshot[eid] = new_snap
                self.log(f"Manual {eid} actualizado: {payload}")
            else:
                self.log(f"Erro ao actualizar {eid}: {r.text}")
                messagebox.showerror("Actualizar", r.text, parent=self.root)
        except Exception as e:
            messagebox.showerror("Actualizar", str(e), parent=self.root)

    def _on_mode_change(self, eid: str, mode: str):
        if mode not in self._mode_options:
            return
        base = self._api_base()
        if not requests:
            return
        try:
            r = requests.patch(
                f"{base}/redirections/{eid}",
                json={"mode": mode},
                timeout=5,
            )
            if r.ok:
                self.log(f"Modo {eid} → {mode}")
            else:
                self.log(f"Erro ao alterar modo: {r.text}")
        except Exception as e:
            messagebox.showerror("Modo", str(e))

    def _on_toggle(self, eid: str, enabled: bool):
        if enabled and os.environ.get("SCOUT_DOCKER_MANAGED") == "1":
            if "n8n_app" in eid:
                if not messagebox.askyesno(
                    "Aviso N8Groker",
                    "Activar n8n_app expoe o n8n directamente e ignora a fila do Porteiro.\n\n"
                    "Para acesso normal use apenas a rota porteiro.\n\nContinuar?",
                    parent=self.root,
                ):
                    var = self._redir_enabled_vars.get(eid)
                    if var:
                        var.set(False)
                    return
            if eid != "porteiro-manual" and "ngrok_service" in eid:
                messagebox.showwarning(
                    "Aviso",
                    "Nao e recomendado activar proxy para ngrok_service.",
                    parent=self.root,
                )
                var = self._redir_enabled_vars.get(eid)
                if var:
                    var.set(False)
                return
        if self.remote_client:
            self.remote_client.toggle_redirection(eid, enabled)
            self.log(f"Toggle {eid} -> {'ON' if enabled else 'OFF'}")

    def _render_traffic(self, rows):
        for row in rows[:80]:
            key = (row.get("client_ip"), row.get("redirection_id"), row.get("time"))
            vals = (
                row.get("time", ""),
                row.get("client_label") or row.get("client_ip", ""),
                row.get("route_name", ""),
                row.get("mode", "tcp"),
                row.get("upstream", ""),
                row.get("bytes_total", 0),
                "SIM" if row.get("blocked") else "—",
            )
            tid = self._traffic_index.get(key)
            if tid and self.tree_traffic.exists(tid):
                self.tree_traffic.item(tid, values=vals)
            else:
                self._traffic_index[key] = self.tree_traffic.insert("", 0, values=vals)

    def _render_clients(self, rows):
        seen = set()
        for row in rows:
            ip = row.get("ip", "")
            if not ip:
                continue
            seen.add(ip)
            alias = row.get("alias") or "—"
            blocked = "SIM" if row.get("blocked") else "—"
            tag = "trusted" if row.get("trusted") else "unknown"
            vals = (
                ip,
                alias,
                row.get("last_seen", ""),
                row.get("last_mode", "tcp"),
                row.get("last_route", ""),
                row.get("session_count", 0),
                row.get("bytes_total", 0),
                blocked,
            )
            tid = self._client_index.get(ip)
            if tid and self.tree_clients.exists(tid):
                self.tree_clients.item(tid, values=vals, tags=(tag,))
            else:
                self._client_index[ip] = self.tree_clients.insert("", 0, values=vals, tags=(tag,))
        stale = [ip for ip in self._client_index if ip not in seen]
        for ip in stale:
            tid = self._client_index.pop(ip)
            if self.tree_clients.exists(tid):
                self.tree_clients.delete(tid)

    def _redir_menu(self, event, eid: str):
        menu = tk.Menu(self.root, tearoff=0)
        var = self._redir_enabled_vars.get(eid)
        if var:
            label = "Desactivar" if var.get() else "Activar"
            menu.add_command(label=label, command=lambda: self._on_toggle(eid, not var.get()))
        widgets = self._redir_rows.get(eid)
        if widgets and widgets.get("is_manual"):
            menu.add_command(label="Apagar", command=lambda: self._delete_redir(eid))
        menu.tk_popup(event.x_root, event.y_root)

    def _delete_redir(self, eid):
        base = self._api_base()
        if requests:
            try:
                requests.delete(f"{base}/redirections/{eid}", timeout=3)
                self.log(f"Removido {eid}")
            except Exception as e:
                messagebox.showerror("Erro", str(e))

    def _traffic_menu(self, event):
        item = self.tree_traffic.identify_row(event.y)
        if not item:
            return
        self.tree_traffic.selection_set(item)
        val = self.tree_traffic.item(item)["values"]
        client = str(val[1])
        ip = client.split("(")[-1].rstrip(")") if "(" in client else client
        self._show_ip_menu(event, ip)

    def _clients_menu(self, event):
        item = self.tree_clients.identify_row(event.y)
        if not item:
            return
        self.tree_clients.selection_set(item)
        ip = str(self.tree_clients.item(item)["values"][0])
        self._show_ip_menu(event, ip, allow_remove=True)

    def _show_ip_menu(self, event, ip, allow_remove=False):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Alias...", command=lambda: self._set_alias(ip))
        menu.add_command(label="Bloquear IP", command=lambda: self._block_ip(ip))
        if allow_remove:
            menu.add_command(label="Remover da lista", command=lambda: self._remove_client(ip))
        menu.tk_popup(event.x_root, event.y_root)

    def _set_alias(self, ip):
        alias = simpledialog.askstring("Alias", f"Alias para {ip}:", parent=self.root)
        if alias is not None and self.remote_client:
            self.remote_client.set_alias(ip, alias)
            self.log(f"Alias {ip} → {alias}")

    def _block_ip(self, ip):
        if self.remote_client:
            self.remote_client.block_ip(ip)
            self.log(f"Bloqueado {ip}")

    def _remove_client(self, ip):
        if self.remote_client:
            self.remote_client.remove_client(ip)
            self.log(f"Removido da lista: {ip}")

    def _sync_docker(self):
        if self.remote_client:
            self.remote_client.sync_docker()
            self.log("Sync Docker pedido.")

    def _firewall_sync(self):
        if self.remote_client:
            self.remote_client.firewall_sync()
            self.log("Sync firewall pedido.")

    def _add_manual(self):
        name = simpledialog.askstring("Manual", "Nome:", parent=self.root)
        if not name:
            return
        port = simpledialog.askinteger("Manual", "Porta upstream:", parent=self.root, minvalue=1, maxvalue=65535)
        if not port:
            return
        base = self._api_base()
        if requests:
            try:
                r = requests.post(
                    f"{base}/redirections/manual",
                    json={"name": name, "upstream_port": port, "upstream_host": "host.docker.internal", "enabled": False},
                    timeout=5,
                )
                self.log(f"Manual: {r.json()}")
            except Exception as e:
                messagebox.showerror("Erro", str(e))

    def _health_check(self):
        h = check_backend_http(self._api_base())
        if h:
            self.lbl_status.config(text=f"Health OK — {h.get('summary', {})}", fg="#00ff00")
        else:
            self.lbl_status.config(text="Backend offline", fg="#cc4400")


def run():
    root = tk.Tk()
    ScoutGateApp(root)
    root.mainloop()
