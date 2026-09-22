import json
import queue
import select
import socket
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ota_send import (
    DEFAULT_PORT,
    TIMEOUT_SEC,
    SocketPort,
    build_frame,
    get_local_ipv4_addresses,
    read_frame,
    send_ota,
)

class OtaSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("OTA Firmware Update Manager")
        self.root.geometry("1080x820")
        self.root.minsize(920, 720)
        self.root.configure(bg="#eef2f6")

        self.events = queue.Queue()
        self.clients = {}
        self.device_rows = {}
        self.version_query_lock = threading.Lock()
        self.server_socket = None
        self.accept_worker = None
        self.ota_worker = None
        self.server_running = False
        self.sort_column = None
        self.sort_descending = False
        self.batch = {}
        self.batch_clients = {}
        self.history_path = Path(sys.executable if getattr(sys, "frozen", False)
                                 else __file__).resolve().parent / "update_history.json"
        self.update_history = self.load_update_history()

        addresses = get_local_ipv4_addresses()
        self.host_var = tk.StringVar(value=addresses[0])
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.bin_var = tk.StringVar()
        self.progress_var = tk.DoubleVar(value=0.0)
        self.server_status_var = tk.StringVar(value="Stopped")
        self.connection_var = tk.StringVar(value="Connected devices: 0")
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="업데이트 대상 0대 · 성공 0대 · 실패 0대 · 대기 0대")
        self.failed_var = tk.StringVar(value="실패 장비: 없음")
        self.selection_var = tk.StringVar(value="선택 0대")

        self.setup_styles()
        self.build_ui(addresses)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_events)

    def setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Card.TLabelframe", background="#ffffff", borderwidth=1,
                        relief="solid")
        style.configure("Card.TLabelframe.Label", background="#eef2f6",
                        foreground="#334155", font=("Segoe UI Semibold", 10))
        style.configure("Header.TLabel", background="#18324a", foreground="#ffffff",
                        font=("Segoe UI Semibold", 18))
        style.configure("SubHeader.TLabel", background="#18324a", foreground="#cbd5e1")
        style.configure("Primary.TButton", background="#1677c8", foreground="#ffffff",
                        padding=(16, 8), font=("Segoe UI Semibold", 9))
        style.map("Primary.TButton", background=[("active", "#0f65ad"),
                                                  ("disabled", "#94a3b8")])
        style.configure("Secondary.TButton", padding=(13, 7))
        style.configure("Treeview", rowheight=36, font=("Segoe UI", 11), background="#ffffff",
                        fieldbackground="#ffffff", foreground="#1e293b")
        style.configure("Treeview.Heading", background="#dce6ef", foreground="#26384a",
                        padding=(8, 8), font=("Segoe UI Semibold", 9), relief="flat")
        style.configure("Status.Horizontal.TProgressbar", troughcolor="#dbe4ec",
                        background="#1d9b67")

    def build_ui(self, addresses):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        header = tk.Frame(root, bg="#18324a", padx=14, pady=7)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 8))
        ttk.Label(header, text="OTA Firmware Update Manager",
                  style="Header.TLabel").pack(anchor="w")

        server_frame = ttk.LabelFrame(root, text="1. TCP OTA SERVER", padding=8,
                                      style="Card.TLabelframe")
        server_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        server_frame.columnconfigure(1, weight=1)

        ttk.Label(server_frame, text="Local PC IP").grid(row=0, column=0, sticky="w")
        self.host_combo = ttk.Combobox(
            server_frame,
            textvariable=self.host_var,
            values=addresses,
            state="normal",
            width=22,
        )
        self.host_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.refresh_button = ttk.Button(
            server_frame, text="Refresh IP", command=self.refresh_ips,
            style="Secondary.TButton"
        )
        self.refresh_button.grid(row=0, column=2, padx=(0, 16))

        ttk.Label(server_frame, text="Port").grid(row=0, column=3, sticky="w")
        self.port_entry = ttk.Entry(
            server_frame, textvariable=self.port_var, width=9
        )
        self.port_entry.grid(row=0, column=4, padx=(8, 12))

        self.server_button = ttk.Button(
            server_frame, text="Start Server", command=self.toggle_server,
            style="Primary.TButton"
        )
        self.server_button.grid(row=0, column=5)

        self.list_refresh_button = ttk.Button(
            server_frame,
            text="Refresh List",
            command=self.refresh_device_list,
            state="disabled",
            style="Secondary.TButton",
        )
        self.list_refresh_button.grid(row=0, column=6, padx=(8, 0))

        ttk.Label(server_frame, text="Server").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        ttk.Label(server_frame, textvariable=self.server_status_var).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0)
        )
        ttk.Label(server_frame, text="Result").grid(
            row=1, column=3, sticky="w", pady=(12, 0)
        )
        ttk.Label(server_frame, textvariable=self.connection_var).grid(
            row=1, column=4, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0)
        )

        file_frame = ttk.LabelFrame(root, text="2. FIRMWARE IMAGE", padding=8,
                                    style="Card.TLabelframe")
        file_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        file_frame.columnconfigure(0, weight=1)

        ttk.Entry(file_frame, textvariable=self.bin_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(file_frame, text="Browse BIN", command=self.browse_bin,
                   style="Secondary.TButton").grid(
            row=0, column=1, padx=(8, 0)
        )
        self.start_button = ttk.Button(
            file_frame,
            text="Start Update",
            command=self.start_update,
            state="disabled",
            style="Primary.TButton",
        )
        self.start_button.grid(row=0, column=2, padx=(8, 0))

        self.content_panes = ttk.Panedwindow(root, orient="vertical")
        self.content_panes.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 8))
        device_frame = ttk.LabelFrame(self.content_panes, text="3. TARGET DEVICES", padding=10,
                                      style="Card.TLabelframe")
        self.content_panes.add(device_frame, weight=4)
        device_frame.columnconfigure(0, weight=1)
        device_frame.rowconfigure(3, weight=1)
        ttk.Label(device_frame, textvariable=self.summary_var,
                  font=("Malgun Gothic", 11, "bold")).grid(row=0, column=0, sticky="w")
        failed_label = ttk.Label(device_frame, textvariable=self.failed_var,
                                 foreground="#b42318")
        failed_label.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        failed_label.bind("<Configure>", lambda event: failed_label.configure(
            wraplength=max(100, event.width)))
        toolbar = ttk.Frame(device_frame)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(toolbar, textvariable=self.selection_var).pack(side="left")
        self.incomplete_button = ttk.Button(
            toolbar, text="미완료 장비만 선택", command=self.select_incomplete_devices,
            state="disabled")
        self.incomplete_button.pack(side="left", padx=(12, 0))
        ttk.Label(toolbar, text="열 제목 클릭: 정렬  |  화면 순서대로 선택 장비 업데이트").pack(side="right")
        self.check_images = {}
        for state in ("off", "on", "mixed"):
            icon = tk.PhotoImage(width=22, height=22)
            icon.put("#64748b", to=(1, 1, 21, 21))
            icon.put("#ffffff", to=(3, 3, 19, 19))
            if state == "on":
                icon.put("#1677c8", to=(3, 3, 19, 19))
                for x, y in ((5, 10), (7, 12), (9, 14), (11, 12), (13, 10), (15, 8)):
                    icon.put("#ffffff", to=(x, y, x + 3, y + 3))
            elif state == "mixed":
                icon.put("#1677c8", to=(5, 9, 17, 13))
            self.check_images[state] = icon
        self.device_tree = ttk.Treeview(
            device_frame,
            columns=("ip", "port", "version", "status", "updated_at"),
            show="tree headings", height=12, selectmode="none"
        )
        self.column_titles = dict((("ip", "Device IP"),
                              ("port", "Port"), ("version", "Current Version"),
                              ("status", "OTA Status"),
                              ("updated_at", "마지막 성공 일시")))
        for column, title in self.column_titles.items():
            self.device_tree.heading(column, text=title,
                                     command=lambda key=column: self.sort_devices(key))
            self.device_tree.column(column, anchor="center")
        self.device_tree.heading("#0", text="전체 선택", image=self.check_images["off"],
                                 command=self.toggle_all_selection)
        self.device_tree.column("#0", width=100, stretch=False, anchor="center")
        self.device_tree.column("ip", width=170, minwidth=130)
        self.device_tree.column("port", width=75, stretch=False)
        self.device_tree.column("version", width=130, stretch=False)
        self.device_tree.column("status", width=180)
        self.device_tree.column("updated_at", width=180, stretch=False)
        self.device_tree.grid(row=3, column=0, sticky="nsew")
        device_scroll = ttk.Scrollbar(device_frame, orient="vertical",
                                      command=self.device_tree.yview)
        device_scroll.grid(row=3, column=1, sticky="ns")
        device_xscroll = ttk.Scrollbar(device_frame, orient="horizontal",
                                       command=self.device_tree.xview)
        device_xscroll.grid(row=4, column=0, sticky="ew")
        self.device_tree.configure(xscrollcommand=device_xscroll.set)
        self.device_tree.configure(yscrollcommand=device_scroll.set)
        self.device_tree.tag_configure("connected", foreground="#2563a8")
        self.device_tree.tag_configure("working", background="#fff59d", foreground="#854d0e")
        self.device_tree.tag_configure("success", background="#edf9f1", foreground="#13764e")
        self.device_tree.tag_configure("failed", background="#fff59d", foreground="#b42318")
        self.device_tree.bind("<Button-1>", self.toggle_device_selection)

        progress_frame = ttk.Frame(root, padding=(12, 0, 12, 8))
        progress_frame.grid(row=4, column=0, sticky="ew", padx=8)
        progress_frame.columnconfigure(0, weight=1)

        ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100.0,
            mode="determinate",
            style="Status.Horizontal.TProgressbar",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.status_var, width=20).grid(
            row=0, column=1, padx=(8, 0)
        )

        self.logs_button = ttk.Button(progress_frame, text="로그 펼치기 ▴",
                                      command=self.toggle_logs)
        self.logs_button.grid(row=0, column=2, padx=(8, 0))
        self.logs = ttk.Notebook(self.content_panes)
        for title, attribute in (("진행 로그", "log_text"),
                                 ("TX  PC → SoC", "tx_log_text"),
                                 ("RX  SoC → PC", "rx_log_text")):
            frame = ttk.Frame(self.logs, padding=6)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            setattr(self, attribute, self.build_log_text(frame, height=7))
            self.logs.add(frame, text=title)

    @staticmethod
    def build_log_text(parent, height):
        text = tk.Text(
            parent,
            height=height,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
        )
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        return text

    def refresh_ips(self):
        addresses = get_local_ipv4_addresses()
        self.host_combo.configure(values=addresses)
        if self.host_var.get().strip() not in addresses:
            self.host_var.set(addresses[0])

    def load_update_history(self):
        if not self.history_path.exists():
            return {}
        try:
            history = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(history, dict) or not all(
                    isinstance(ip, str) and isinstance(stamp, str)
                    for ip, stamp in history.items()):
                raise ValueError("Invalid update history format")
            return history
        except (OSError, ValueError) as exc:
            messagebox.showerror("업데이트 이력 읽기 실패", str(exc))
            return {}

    def save_update_history(self, ip, timestamp):
        self.update_history[ip] = timestamp
        try:
            temporary = self.history_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.update_history, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
            temporary.replace(self.history_path)
        except OSError as exc:
            self.log(f"HISTORY    : {exc}")
            messagebox.showerror("업데이트 일자 저장 실패", str(exc))

    def toggle_logs(self):
        if str(self.logs) in self.content_panes.panes():
            self.content_panes.forget(self.logs)
            self.logs_button.configure(text="로그 펼치기 ▴")
        else:
            self.content_panes.add(self.logs, weight=1)
            self.root.update_idletasks()
            self.content_panes.sashpos(0, int(self.content_panes.winfo_height() * 0.7))
            self.logs_button.configure(text="로그 접기 ▾")

    def sort_devices(self, column=None):
        if self.ota_worker is not None:
            return
        if column is not None:
            self.sort_descending = not self.sort_descending if column == self.sort_column else False
            self.sort_column = column
        if self.sort_column is None:
            return
        column = self.sort_column
        def key(item):
            value = self.device_tree.set(item, column)
            if column == "ip":
                return tuple(int(part) for part in value.split("."))
            if column in ("port", "version"):
                return int(value) if value.isdigit() else -1
            return value
        for index, item in enumerate(sorted(self.device_tree.get_children(), key=key,
                                            reverse=self.sort_descending)):
            self.device_tree.move(item, "", index)
        for name, title in self.column_titles.items():
            arrow = (" ▼" if self.sort_descending else " ▲") if name == column else ""
            self.device_tree.heading(name, text=title + arrow)

    def selected_clients_in_order(self):
        by_item = {info["item"]: client for client, info in self.clients.items()
                   if info["selected"] and info["ready"]}
        return [by_item[item] for item in self.device_tree.get_children() if item in by_item]

    def update_selection_display(self):
        selectable = [info for info in self.clients.values() if info["ready"]]
        count = sum(info["selected"] for info in selectable)
        state = "off" if not count else "on" if count == len(selectable) else "mixed"
        self.device_tree.heading("#0", image=self.check_images[state])
        self.selection_var.set(f"선택 {count}대 / 선택 가능 {len(selectable)}대")
        for info in self.device_rows.values():
            self.device_tree.item(info["item"], image=self.check_images[
                "on" if info["selected"] else "off"])

    def toggle_all_selection(self):
        if self.ota_worker is not None:
            return
        selectable = [info for info in self.clients.values() if info["ready"]]
        selected = not all(info["selected"] for info in selectable)
        for info in selectable:
            info["selected"] = selected
        self.update_selection_display()

    def select_incomplete_devices(self):
        if self.ota_worker is not None or not self.batch:
            return
        for ip, info in self.device_rows.items():
            entry = self.batch.get(ip)
            info["selected"] = bool(entry and entry["status"] != "Success"
                                    and info["ready"] and info["client"] in self.clients)
        self.update_selection_display()

    def update_batch_summary(self):
        states = [entry["status"] for entry in self.batch.values()]
        success = states.count("Success")
        failed = states.count("OTA Failed")
        waiting = states.count("Waiting")
        active = len(states) - success - failed - waiting
        self.summary_var.set(f"업데이트 대상 {len(states)}대 · 성공 {success}대 · "
                             f"실패 {failed}대 · 진행 {active}대 · 대기 {waiting}대")
        failed_numbers = [f"{ip.rsplit('.', 1)[-1]}번" for ip, entry in self.batch.items()
                          if entry["status"] == "OTA Failed"]
        self.failed_var.set("실패 장비: " + (", ".join(failed_numbers) or "없음"))

    def browse_bin(self):
        filename = filedialog.askopenfilename(
            title="Select APP firmware BIN",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        if filename:
            self.bin_var.set(filename)

    def toggle_device_selection(self, event):
        if self.ota_worker is not None:
            return "break"
        if self.device_tree.identify_region(event.x, event.y) == "heading":
            return None
        if self.device_tree.identify_column(event.x) != "#0":
            return None
        item = self.device_tree.identify_row(event.y)
        for info in self.clients.values():
            if info["item"] == item and info["ready"]:
                info["selected"] = not info["selected"]
                self.update_selection_display()
                return "break"
        return None

    def set_device_status(self, info, status):
        entry = self.batch.get(info["peer"][0])
        batch_statuses = ("Waiting", "Updating", "Retry wait 5s", "Retrying", "Success", "OTA Failed")
        if entry and status in batch_statuses:
            entry["status"] = status
            self.update_batch_summary()
        result = entry["status"] if entry else None
        if result == "Success" or status == "Success":
            tag = "success"
        elif result == "OTA Failed" or status == "OTA Failed":
            tag = "failed"
        elif result in ("Waiting", "Updating", "Retry wait 5s", "Retrying"):
            tag = "working"
        else:
            tag = "connected"
        display = status
        if result and status not in batch_statuses:
            display = f"{result} / {status}"
        if result == "Success" or status == "Success":
            detail = {"Success": "버전 조회 대기", "Reading version": "버전 조회 중",
                      "Version read": "버전 조회 완료", "Version unavailable": "버전 확인 불가",
                      "Disconnected": "연결 끊김 · 버전 확인 불가"}.get(status, status)
            display = f"전송 성공 / {detail}"
        self.device_tree.set(info["item"], "status", display)
        if status == "Success":
            info["version_not_before"] = time.monotonic() + 3.0
            self.device_tree.set(info["item"], "version", "-")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.device_tree.set(info["item"], "updated_at", timestamp)
            self.save_update_history(info["peer"][0], timestamp)
        self.device_tree.item(info["item"], tags=(tag,))

    def update_action_buttons(self):
        self.update_selection_display()
        ota_running = self.ota_worker is not None
        self.incomplete_button.configure(
            state="normal" if self.batch and not ota_running else "disabled")
        query_running = any(info["querying"] for info in self.clients.values())
        ready = any(info["ready"] for info in self.clients.values())
        self.start_button.configure(
            state="normal" if ready and not query_running and not ota_running else "disabled"
        )
        self.list_refresh_button.configure(
            state="normal" if self.clients and not query_running and not ota_running else "disabled"
        )

    def refresh_device_list(self):
        if self.ota_worker is not None:
            return
        for client, info in list(self.clients.items()):
            if info["querying"]:
                continue
            self.start_version_query(info)
        self.update_action_buttons()

    def start_version_query(self, info, delay_ms=0):
        client = info["client"]
        if client not in self.clients:
            self.set_device_status(info, "Disconnected")
            return
        query_id = info.get("version_query_id", 0) + 1
        info["version_query_id"] = query_id
        info["ready"] = False
        info["querying"] = True
        self.device_tree.set(info["item"], "version", "-")
        delay_ms = max(delay_ms, int(max(0, info.get("version_not_before", 0)
                                         - time.monotonic()) * 1000))

        def begin():
            if (self.clients.get(client) is not info or info["client"] is not client
                    or info.get("version_query_id") != query_id):
                return
            self.set_device_status(info, "Reading version")
            threading.Thread(target=self.query_device_version,
                             args=(client, info["peer"], query_id), daemon=True).start()

        if delay_ms:
            self.root.after(delay_ms, begin)
        else:
            begin()
        self.update_action_buttons()

    def get_server_address(self):
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Local PC IP is required.")
        try:
            socket.inet_aton(host)
        except OSError as exc:
            raise ValueError("Local PC IP must be a valid IPv4 address.") from exc

        try:
            port = int(self.port_var.get().strip())
        except ValueError as exc:
            raise ValueError("Port must be a number.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        return host, port

    def toggle_server(self):
        if self.server_running:
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        try:
            host, port = self.get_server_address()
        except ValueError as exc:
            messagebox.showerror("Invalid server setting", str(exc))
            return

        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen()
            server.settimeout(0.5)
        except OSError as exc:
            try:
                server.close()
            except UnboundLocalError:
                pass
            messagebox.showerror("Server start failed", str(exc))
            return

        self.server_socket = server
        self.server_running = True
        self.server_status_var.set(f"Listening on {host}:{port}")
        self.connection_var.set("Connected devices: 0")
        self.server_button.configure(text="Stop Server")
        self.host_combo.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.log(f"LISTEN     : {host}:{port}")
        self.accept_worker = threading.Thread(target=self.accept_loop, daemon=True)
        self.accept_worker.start()

    def accept_loop(self):
        while self.server_running:
            try:
                client, peer = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.settimeout(TIMEOUT_SEC)
            self.events.put(("connected", (client, peer)))

    def monitor_client(self, client):
        while self.server_running and client in self.clients:
            try:
                readable, _, exceptional = select.select([client], [], [client], 0.5)
                if exceptional:
                    break
                if readable and client.recv(1, socket.MSG_PEEK) == b"":
                    break
            except (OSError, ValueError):
                break
        self.events.put(("connection_lost", client))

    def query_device_version(self, client, peer, query_id):
        with self.version_query_lock:
            info = self.clients.get(client)
            if not info or info.get("version_query_id") != query_id:
                return
            self._query_device_version_locked(client, peer, query_id)

    def _query_device_version_locked(self, client, peer, query_id):
        version = "-"
        last_error = None
        original_timeout = client.gettimeout()
        try:
            client.settimeout(TIMEOUT_SEC)
            for attempt in range(2):
                time.sleep(1)
                if attempt:
                    self.log(f"VERSION    : {peer[0]} - retry")
                try:
                    port = SocketPort(client, self.tx_data, self.rx_data)
                    port.write(build_frame("VER?"))
                    port.flush()
                    deadline = time.monotonic() + TIMEOUT_SEC
                    while time.monotonic() < deadline:
                        cmd, payload, _ = read_frame(port)
                        if cmd == "DAT=":
                            continue
                        if cmd != "VER=":
                            raise RuntimeError(
                                f"unexpected response cmd: {cmd}, expected VER="
                            )
                        if len(payload) != 2:
                            raise ValueError(
                                f"invalid VER payload length: {len(payload)}"
                            )
                        version = str(int.from_bytes(payload, "little"))
                        break
                    if version != "-":
                        break
                    raise TimeoutError("timeout waiting for VER response")
                except Exception as exc:
                    last_error = exc
            if version != "-":
                self.log(f"VERSION    : {peer[0]} - {version}")
            else:
                self.log(f"VERSION    : {peer[0]} - unavailable ({last_error})")
        finally:
            try:
                client.settimeout(original_timeout)
            except OSError:
                pass
        self.events.put(("version_result", (client, version, query_id)))

    def stop_server(self):
        self.server_running = False
        for client in list(self.clients):
            self.close_socket(client)
        self.clients.clear()
        self.device_rows.clear()
        if not (self.ota_worker is not None):
            self.batch.clear()
            self.batch_clients.clear()
        self.close_socket(self.server_socket)
        self.server_socket = None
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)
        self.server_status_var.set("Stopped")
        self.connection_var.set("Connected devices: 0")
        self.server_button.configure(text="Start Server")
        self.host_combo.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.refresh_button.configure(state="normal")
        self.start_button.configure(state="disabled")
        self.list_refresh_button.configure(state="disabled")
        self.status_var.set("Ready")
        self.incomplete_button.configure(state="disabled")
        self.log("SERVER     : stopped")
        self.update_selection_display()
        self.update_batch_summary()

    def start_update(self):
        if self.ota_worker is not None:
            return
        selected = self.selected_clients_in_order()
        if not selected:
            messagebox.showerror("No SoC selected", "Select at least one connected SoC.")
            return

        bin_path = Path(self.bin_var.get().strip())
        if not bin_path.is_file():
            messagebox.showerror("Invalid BIN", "Select a valid APP firmware BIN file.")
            return

        self.progress_var.set(0.0)
        self.status_var.set("Starting")
        self.clear_log()
        self.clear_traffic_logs()
        self.start_button.configure(state="disabled")
        self.list_refresh_button.configure(state="disabled")
        self.server_button.configure(state="disabled")
        self.incomplete_button.configure(state="disabled")
        self.batch = {self.clients[client]["peer"][0]: {"status": "Waiting"}
                      for client in selected}
        self.batch_clients = {client: self.clients[client] for client in selected}
        for info in self.device_rows.values():
            if info["peer"][0] in self.batch:
                self.set_device_status(info, "Waiting")
            else:
                self.device_tree.item(info["item"], tags=("connected",))

        self.ota_worker = threading.Thread(
            target=self.worker_send_ota,
            args=(selected, bin_path),
            daemon=True,
        )
        self.ota_worker.start()

    def worker_send_ota(self, selected, bin_path):
        all_successful = True
        for index, client in enumerate(selected, 1):
            info = self.batch_clients.get(client)
            if not info:
                continue
            peer = info["peer"]
            self.events.put(("device_status", (client, "Updating")))
            self.set_status(f"Device {index}/{len(selected)}")
            self.progress(0.0)
            for attempt in range(2):
                if attempt:
                    self.events.put(("device_status", (client, "Retry wait 5s")))
                    self.log("RETRY      : waiting 5 seconds")
                    time.sleep(5)
                    self.events.put(("device_status", (client, "Retrying")))
                try:
                    send_ota(SocketPort(client, self.tx_data, self.rx_data), bin_path,
                             self.progress, self.log)
                    self.events.put(("device_status", (client, "Success")))
                    break
                except Exception as exc:
                    self.log(f"ERROR      : {peer[0]}:{peer[1]} - {exc}")
                    if attempt:
                        self.events.put(("device_status", (client, "OTA Failed")))
                        all_successful = False
        self.set_status("Done" if all_successful else "Done with failures")
        self.events.put(("done", all_successful))

    @staticmethod
    def close_socket(sock):
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def log(self, message):
        self.events.put(("log", message))

    def progress(self, value):
        self.events.put(("progress", value))

    def set_status(self, value):
        self.events.put(("status", value))

    def tx_data(self, data):
        self.events.put(("tx_data", bytes(data)))

    def rx_data(self, data):
        self.events.put(("rx_data", bytes(data)))

    def clear_log(self):
        self.clear_text(self.log_text)

    def clear_traffic_logs(self):
        self.clear_text(self.tx_log_text)
        self.clear_text(self.rx_log_text)

    @staticmethod
    def clear_text(widget):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def append_hex_log(widget, data):
        widget.configure(state="normal")
        widget.insert("end", f"[{len(data)} bytes]\n")
        for offset in range(0, len(data), 16):
            chunk = data[offset : offset + 16]
            hex_text = " ".join(f"{byte:02X}" for byte in chunk)
            widget.insert("end", f"{offset:04X}  {hex_text}\n")
        widget.insert("end", "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def process_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self.append_log(value)
            elif kind == "progress":
                self.progress_var.set(value)
            elif kind == "status":
                self.status_var.set(value)
            elif kind == "tx_data":
                self.append_hex_log(self.tx_log_text, value)
            elif kind == "rx_data":
                self.append_hex_log(self.rx_log_text, value)
            elif kind == "connected":
                client, peer = value
                info = self.device_rows.get(peer[0])
                if info:
                    old_client = info.get("client")
                    if old_client is not None and old_client is not client:
                        self.clients.pop(old_client, None)
                        self.close_socket(old_client)
                    info["client"] = client
                    info["peer"] = peer
                    info["ready"] = False
                    info["querying"] = True
                    self.device_tree.set(info["item"], "port", peer[1])
                    self.device_tree.set(info["item"], "version", "-")
                    self.set_device_status(info, "Reading version")
                else:
                    item = self.device_tree.insert(
                        "", "end",
                        image=self.check_images["off"],
                        values=(peer[0], peer[1], "-", "Reading version",
                                self.update_history.get(peer[0], "-")),
                        tags=("connected",),
                    )
                    info = {
                        "client": client,
                        "peer": peer,
                        "item": item,
                        "selected": False,
                        "ready": False,
                        "querying": True,
                    }
                    self.device_rows[peer[0]] = info
                self.clients[client] = info
                self.connection_var.set(f"Connected devices: {len(self.clients)}")
                self.append_log(f"CONNECTED  : {peer[0]}:{peer[1]}")
                self.start_version_query(info)
                self.update_action_buttons()
                self.sort_devices()
            elif kind == "version_result":
                client, version, query_id = value
                info = self.clients.get(client)
                if (info and info["client"] is client
                        and info.get("version_query_id") == query_id):
                    info["ready"] = True
                    info["querying"] = False
                    self.device_tree.set(info["item"], "version", version)
                    self.set_device_status(info, "Version read" if version != "-"
                                           else "Version unavailable")
                    if info.get("monitored_client") is not client:
                        info["monitored_client"] = client
                        threading.Thread(
                            target=self.monitor_client, args=(client,), daemon=True
                        ).start()
                    self.update_action_buttons()
                    self.sort_devices()
            elif kind == "connection_lost":
                info = self.clients.pop(value, None)
                if info and info["client"] is value:
                    self.close_socket(value)
                    info["client"] = None
                    info["ready"] = False
                    info["querying"] = False
                    self.set_device_status(info, "Disconnected")
                    self.connection_var.set(
                        f"Connected devices: {len(self.clients)}"
                    )
                    self.update_action_buttons()
            elif kind == "device_status":
                client, status = value
                info = self.batch_clients.get(client)
                if info:
                    self.set_device_status(info, status)
                    if status == "Success":
                        # MCU resets about 3 seconds after FWD=SUCC (OTA document).
                        self.start_version_query(info, delay_ms=3000)
                    self.start_button.configure(state="disabled")
            elif kind == "done":
                self.ota_worker = None
                self.server_button.configure(state="normal")
                self.update_batch_summary()
                self.sort_devices()
                if value:
                    self.update_action_buttons()
                    messagebox.showinfo("TCP OTA Sender", "전체 대상 전송이 성공했습니다.\n"
                                        "버전 재조회 결과는 목록에서 별도로 확인하세요.")
                else:
                    self.update_action_buttons()
                    messagebox.showerror(
                        "TCP OTA Sender", "전체 대상 처리가 끝났습니다. 실패 장비가 있습니다.\n"
                        "상단 실패 목록을 확인하고 '미완료 장비만 선택'으로 재시도할 수 있습니다."
                    )

        self.root.after(100, self.process_events)

    def on_close(self):
        self.server_running = False
        for client in list(self.clients):
            self.close_socket(client)
        self.close_socket(self.server_socket)
        self.root.destroy()


def main():
    root = tk.Tk()
    OtaSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
