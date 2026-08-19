import queue
import select
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ota_send import (
    DEFAULT_PORT,
    TIMEOUT_SEC,
    SocketPort,
    get_local_ipv4_addresses,
    send_ota,
)


class OtaSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TCP OTA Sender")
        self.root.geometry("980x760")
        self.root.minsize(820, 650)

        self.events = queue.Queue()
        self.server_socket = None
        self.client_socket = None
        self.accept_worker = None
        self.ota_worker = None
        self.server_running = False

        addresses = get_local_ipv4_addresses()
        self.host_var = tk.StringVar(value=addresses[0])
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.bin_var = tk.StringVar()
        self.progress_var = tk.DoubleVar(value=0.0)
        self.server_status_var = tk.StringVar(value="Stopped")
        self.connection_var = tk.StringVar(value="Disconnected")
        self.status_var = tk.StringVar(value="Ready")

        self.build_ui(addresses)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.process_events)

    def build_ui(self, addresses):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        server_frame = ttk.LabelFrame(root, text="TCP OTA Server", padding=12)
        server_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
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
            server_frame, text="Refresh IP", command=self.refresh_ips
        )
        self.refresh_button.grid(row=0, column=2, padx=(0, 16))

        ttk.Label(server_frame, text="Port").grid(row=0, column=3, sticky="w")
        self.port_entry = ttk.Entry(
            server_frame, textvariable=self.port_var, width=9
        )
        self.port_entry.grid(row=0, column=4, padx=(8, 12))

        self.server_button = ttk.Button(
            server_frame, text="Start Server", command=self.toggle_server
        )
        self.server_button.grid(row=0, column=5)

        ttk.Label(server_frame, text="Server").grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        ttk.Label(server_frame, textvariable=self.server_status_var).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0)
        )
        ttk.Label(server_frame, text="SoC").grid(
            row=1, column=3, sticky="w", pady=(12, 0)
        )
        ttk.Label(server_frame, textvariable=self.connection_var).grid(
            row=1, column=4, columnspan=2, sticky="w", padx=(8, 0), pady=(12, 0)
        )

        file_frame = ttk.LabelFrame(root, text="APP Firmware", padding=12)
        file_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        file_frame.columnconfigure(0, weight=1)

        ttk.Entry(file_frame, textvariable=self.bin_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(file_frame, text="Browse", command=self.browse_bin).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.start_button = ttk.Button(
            file_frame,
            text="Start Update",
            command=self.start_update,
            state="disabled",
        )
        self.start_button.grid(row=0, column=2, padx=(8, 0))

        progress_frame = ttk.Frame(root, padding=(12, 0, 12, 8))
        progress_frame.grid(row=2, column=0, sticky="ew")
        progress_frame.columnconfigure(0, weight=1)

        ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100.0,
            mode="determinate",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.status_var, width=20).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Separator(root).grid(row=3, column=0, sticky="ew", padx=12)

        logs = ttk.Panedwindow(root, orient="vertical")
        logs.grid(row=4, column=0, sticky="nsew", padx=12, pady=12)

        status_log = ttk.LabelFrame(logs, text="OTA Progress", padding=6)
        status_log.columnconfigure(0, weight=1)
        status_log.rowconfigure(0, weight=1)
        self.log_text = self.build_log_text(status_log, height=9)
        logs.add(status_log, weight=1)

        traffic = ttk.Panedwindow(logs, orient="horizontal")
        tx_frame = ttk.LabelFrame(traffic, text="TX  PC → SoC", padding=6)
        tx_frame.columnconfigure(0, weight=1)
        tx_frame.rowconfigure(0, weight=1)
        self.tx_log_text = self.build_log_text(tx_frame, height=12)

        rx_frame = ttk.LabelFrame(traffic, text="RX  SoC → PC", padding=6)
        rx_frame.columnconfigure(0, weight=1)
        rx_frame.rowconfigure(0, weight=1)
        self.rx_log_text = self.build_log_text(rx_frame, height=12)

        traffic.add(tx_frame, weight=1)
        traffic.add(rx_frame, weight=1)
        logs.add(traffic, weight=2)

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

    def browse_bin(self):
        filename = filedialog.askopenfilename(
            title="Select APP firmware BIN",
            filetypes=[("BIN files", "*.bin"), ("All files", "*.*")],
        )
        if filename:
            self.bin_var.set(filename)

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
            server.listen(1)
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
        self.connection_var.set("Waiting for SoC")
        self.server_button.configure(text="Stop Server")
        self.host_combo.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.log(f"LISTEN     : {host}:{port}")
        self.log("WAIT       : waiting for SoC connection")

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
            self.client_socket = client
            self.events.put(("connected", peer))
            self.monitor_client(client)

    def monitor_client(self, client):
        while self.server_running and self.client_socket is client:
            try:
                readable, _, exceptional = select.select([client], [], [client], 0.5)
                if exceptional:
                    break
                if readable and client.recv(1, socket.MSG_PEEK) == b"":
                    break
            except (OSError, ValueError):
                break

        self.events.put(("connection_lost", client))

    def stop_server(self):
        self.server_running = False
        self.close_socket(self.client_socket)
        self.client_socket = None
        self.close_socket(self.server_socket)
        self.server_socket = None
        self.server_status_var.set("Stopped")
        self.connection_var.set("Disconnected")
        self.server_button.configure(text="Start Server")
        self.host_combo.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.refresh_button.configure(state="normal")
        self.start_button.configure(state="disabled")
        self.status_var.set("Ready")
        self.log("SERVER     : stopped")

    def start_update(self):
        if self.ota_worker and self.ota_worker.is_alive():
            return
        if self.client_socket is None:
            messagebox.showerror("SoC not connected", "Wait for the SoC connection first.")
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

        client = self.client_socket
        self.ota_worker = threading.Thread(
            target=self.worker_send_ota,
            args=(client, bin_path),
            daemon=True,
        )
        self.ota_worker.start()

    def worker_send_ota(self, client, bin_path):
        try:
            peer = client.getpeername()
            self.log(f"CONNECTED  : {peer[0]}:{peer[1]}")
            self.set_status("FWU / FWH / FWD")
            send_ota(
                SocketPort(
                    client,
                    tx_callback=self.tx_data,
                    rx_callback=self.rx_data,
                ),
                bin_path,
                progress_callback=self.progress,
                log_callback=self.log,
            )
            self.set_status("Done")
            self.progress(100.0)
            self.events.put(("done", True))
        except Exception as exc:
            self.log("")
            self.log(f"ERROR      : {exc}")
            self.set_status("Error")
            self.events.put(("connection_lost", client))
            self.events.put(("done", False))

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
                self.connection_var.set(f"Connected: {value[0]}:{value[1]}")
                self.start_button.configure(state="normal")
                self.append_log(f"CONNECTED  : {value[0]}:{value[1]}")
            elif kind == "connection_lost":
                if self.client_socket is value:
                    self.close_socket(self.client_socket)
                    self.client_socket = None
                    self.connection_var.set(
                        "Waiting for SoC" if self.server_running else "Disconnected"
                    )
                    self.start_button.configure(state="disabled")
            elif kind == "done":
                if value:
                    self.start_button.configure(
                        state="normal" if self.client_socket else "disabled"
                    )
                    messagebox.showinfo("TCP OTA Sender", "Firmware update finished.")
                else:
                    messagebox.showerror(
                        "TCP OTA Sender", "Firmware update failed. Check the log."
                    )

        self.root.after(100, self.process_events)

    def on_close(self):
        self.server_running = False
        self.close_socket(self.client_socket)
        self.close_socket(self.server_socket)
        self.root.destroy()


def main():
    root = tk.Tk()
    OtaSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
