"""W5500 page reusing the existing OTA Sender list and logs."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from ota_send_gui import OtaSenderApp
from ota_w5500 import (scan_devices, send_w5500_ota, TRANSFER_PORT,
                       send_camera_ota, CAMERA_TRANSFER_PORT)


class W5500SenderApp(OtaSenderApp):
    def __init__(self, root, parent, thermal_camera=False):
        self.thermal_camera = thermal_camera
        self.transfer_port = CAMERA_TRANSFER_PORT if thermal_camera else TRANSFER_PORT
        self.scan_worker = None
        self.scan_events = queue.Queue()
        super().__init__(root, parent)
        self.port_var.set(str(self.transfer_port))
        self.port_entry.configure(state="readonly")
        self.server_button.configure(text="SCAN", command=self.scan)
        self.list_refresh_button.configure(command=self.scan)
        self.server_button.master.configure(text="1. W5500 NETWORK OTA")
        self.server_status_var.set("UDP DISC: 9300 / TCP OTA: 9301")
        self.connection_var.set("Discovered devices: 0")
        self.logs.tab(1, text="TX  PC → ZCU")
        self.logs.tab(2, text="RX  ZCU → PC")
        if thermal_camera:
            self.server_button.master.configure(text="1. W5500 열화상 카메라 OTA")
            self.server_status_var.set("UDP DISC: 9300 / UDP OTA: 9303")
            self.logs.tab(1, text="TX  PC → 카메라")
            self.logs.tab(2, text="RX  카메라 → PC")

    def update_action_buttons(self):
        super().update_action_buttons()
        busy = self.scan_worker is not None or self.ota_worker is not None
        self.server_button.configure(state="disabled" if busy else "normal")
        self.list_refresh_button.configure(state="disabled" if busy else "normal")
        self.host_combo.configure(state="disabled" if busy else "normal")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.start_button.configure(state="disabled")

    def scan(self):
        if self.scan_worker is not None or self.ota_worker is not None:
            return
        local_ip = self.host_var.get().strip()
        self.scan_worker = threading.Thread(target=self.worker_scan, args=(local_ip,), daemon=True)
        self.update_action_buttons()
        self.scan_worker.start()

    def worker_scan(self, local_ip):
        try:
            devices = scan_devices(local_ip, self.log, self.tx_data, self.rx_data)
            self.scan_events.put(("scan_result", devices))
        except Exception as exc:
            self.log(f"SCAN ERROR : {type(exc).__name__}: {exc}")
        finally:
            self.scan_events.put(("scan_done", None))

    def apply_scan(self, devices):
        self.clients.clear()
        for ip, info in self.device_rows.items():
            info["ready"] = False
            self.set_device_status(info, "Not found")
        for ip, version in devices.items():
            info = self.device_rows.get(ip)
            if info is None:
                item = self.device_tree.insert("", "end", image=self.check_images["off"],
                    values=(ip, self.transfer_port, version, "Discovered", self.update_history.get(ip, "-")))
                info = dict(client=ip, peer=(ip, self.transfer_port), item=item,
                            selected=False, ready=True, querying=False)
                self.device_rows[ip] = info
            info.update(client=ip, ready=True, querying=False)
            self.clients[ip] = info
            self.device_tree.set(info["item"], "version", version)
            self.set_device_status(info, "Version read")
        self.connection_var.set(f"Discovered devices: {len(self.clients)}")
        self.sort_devices()

    def start_update(self):
        if self.scan_worker is not None:
            return
        # Snapshot the chosen interface before starting the worker.
        self.update_local_ip = self.host_var.get().strip()
        super().start_update()
        if self.ota_worker is not None:
            self.update_action_buttons()

    def worker_send_ota(self, selected, bin_path):
        all_successful = True
        for index, ip in enumerate(selected, 1):
            self.events.put(("device_status", (ip, "Updating")))
            self.set_status(f"Device {index}/{len(selected)}")
            self.progress(0.0)
            for attempt in range(2):
                if attempt:
                    self.events.put(("device_status", (ip, "Retry wait 5s")))
                    self.log(f"[{ip}] RETRY: waiting 5 seconds")
                    time.sleep(5)
                    self.events.put(("device_status", (ip, "Retrying")))
                self.log(f"[{ip}] ATTEMPT {attempt + 1}/2")
                try:
                    sender = send_camera_ota if self.thermal_camera else send_w5500_ota
                    sender(self.update_local_ip, ip, bin_path,
                                  self.progress, self.log, self.tx_data, self.rx_data)
                    self.events.put(("device_status", (ip, "Success")))
                    break
                except Exception as exc:
                    self.log(f"[{ip}] ATTEMPT FAILED: {exc}")
                    if attempt:
                        self.events.put(("device_status", (ip, "OTA Failed")))
                        all_successful = False
        self.set_status("Done" if all_successful else "Done with failures")
        self.events.put(("done", all_successful))

    def start_version_query(self, info, delay_ms=0):
        # Camera VER? frames are not part of the ZCU protocol.
        self.device_tree.set(info["item"], "version", "-")
        self.device_tree.set(info["item"], "status", "전송 성공 / SCAN으로 버전 재조회")

    def process_events(self):
        while True:
            try:
                kind, value = self.scan_events.get_nowait()
            except queue.Empty:
                break
            if kind == "scan_result":
                self.apply_scan(value)
            elif kind == "scan_done":
                self.scan_worker = None
                self.update_action_buttons()
        super().process_events()


def build_pages(root):
    navigation = ttk.Frame(root, padding=(16, 6))
    navigation.pack(fill="x")
    container = ttk.Frame(root)
    container.pack(fill="both", expand=True)
    camera_frame = ttk.Frame(container)
    w5500_frame = ttk.Frame(container)
    thermal_frame = ttk.Frame(container)
    camera = OtaSenderApp(root, camera_frame)
    w5500 = W5500SenderApp(root, w5500_frame)
    thermal = W5500SenderApp(root, thermal_frame, thermal_camera=True)

    def show(frame):
        camera_frame.pack_forget()
        w5500_frame.pack_forget()
        thermal_frame.pack_forget()
        frame.pack(fill="both", expand=True)

    ttk.Button(navigation, text="바이패스", command=lambda: show(camera_frame)).pack(side="left")
    ttk.Button(navigation, text="W5500 구역제어기", command=lambda: show(w5500_frame)).pack(side="left", padx=8)
    ttk.Button(navigation, text="W5500 열화상 카메라", command=lambda: show(thermal_frame)).pack(side="left")
    show(camera_frame)

    def close():
        camera.server_running = False
        for client in list(camera.clients):
            camera.close_socket(client)
        camera.close_socket(camera.server_socket)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    return camera, w5500, thermal
