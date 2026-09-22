"""GUI regression checks using real Tk widgets and isolated update history.

Run: python -m unittest -v test_ota_send_gui
No device connection or firmware transfer is performed.
"""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ota_send_gui as gui


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(gui, "get_local_ipv4_addresses", return_value=["127.0.0.1"]), \
                patch.object(gui.OtaSenderApp, "load_update_history", return_value={}):
            self.app = gui.OtaSenderApp(self.root)
        self.app.history_path = Path(self.directory.name) / "update_history.json"
        self.bin_path = Path(self.directory.name) / "test.bin"
        self.bin_path.write_bytes(b"test firmware")
        self.app.bin_var.set(str(self.bin_path))
        for number, version in ((10, "718"), (2, "99"), (30, "-")):
            self.add_device(number, version)

    def add_device(self, number, version):
        client = Mock(name=f"device_{number}")
        ip = f"192.168.1.{number}"
        item = self.app.device_tree.insert("", "end", image=self.app.check_images["off"],
                                          values=(ip, 9001, version, "Connected", "-"))
        info = dict(client=client, peer=(ip, 9001), item=item,
                    selected=False, ready=True, querying=False)
        self.app.clients[client] = info
        self.app.device_rows[ip] = info
        return client

    def ips(self):
        return [self.app.device_tree.set(item, "ip")
                for item in self.app.device_tree.get_children()]

    def drain(self):
        with patch.object(gui.messagebox, "showinfo"), patch.object(gui.messagebox, "showerror"):
            self.app.process_events()

    def prepare_batch(self):
        self.app.toggle_all_selection()
        with patch.object(gui.threading, "Thread") as thread:
            self.app.start_update()
            return thread.call_args.kwargs["args"][0]

    def test_numeric_sort_and_selection_survive_reordering(self):
        self.app.toggle_all_selection()
        self.app.sort_devices("ip")
        self.assertEqual([ip.rsplit(".", 1)[1] for ip in self.ips()], ["2", "10", "30"])
        self.app.sort_devices("ip")
        self.assertEqual([ip.rsplit(".", 1)[1] for ip in self.ips()], ["30", "10", "2"])
        self.app.sort_devices("version")
        self.assertEqual([ip.rsplit(".", 1)[1] for ip in self.ips()], ["30", "2", "10"])
        self.assertTrue(all(info["selected"] for info in self.app.clients.values()))

    def test_select_all_only_ready_devices_and_mixed_header(self):
        infos = list(self.app.clients.values())
        infos[-1]["ready"] = False
        self.app.toggle_all_selection()
        self.assertEqual([info["selected"] for info in infos], [True, True, False])
        infos[0]["selected"] = False
        self.app.update_selection_display()
        self.assertEqual(str(self.app.device_tree.heading("#0", "image")),
                         str(self.app.check_images["mixed"]))
        self.app.toggle_all_selection()
        self.app.toggle_all_selection()
        self.assertFalse(any(info["selected"] for info in infos))

    def test_start_snapshot_matches_descending_display_and_freezes_sort(self):
        self.app.sort_devices("ip")
        self.app.sort_devices("ip")
        selected = self.prepare_batch()
        self.assertEqual([self.app.clients[c]["peer"][0] for c in selected], self.ips())
        before = self.ips()
        self.app.sort_devices("ip")
        self.app.toggle_all_selection()
        self.assertEqual(self.ips(), before)
        self.assertEqual(len(self.app.selected_clients_in_order()), 3)

    def test_worker_order_and_success_history(self):
        self.app.sort_devices("ip")
        selected = self.prepare_batch()
        visited = []
        with patch.object(gui, "send_ota", side_effect=lambda port, *args: visited.append(port.sock)):
            self.app.worker_send_ota(selected, self.bin_path)
        self.drain()
        self.assertEqual(visited, selected)
        self.assertIn("성공 3대", self.app.summary_var.get())
        self.assertEqual(set(self.app.load_update_history()), set(self.ips()))
        self.assertTrue(all(self.app.device_tree.item(info["item"], "tags") == ("success",)
                            for info in self.app.device_rows.values()))

    def test_failure_continues_after_existing_retry(self):
        selected = self.prepare_batch()
        with patch.object(gui, "send_ota", side_effect=[None, RuntimeError("offline"),
                                                      RuntimeError("offline"), None]) as sender, \
                patch.object(gui.time, "sleep") as sleep:
            self.app.worker_send_ota(selected, self.bin_path)
        self.drain()
        self.assertEqual(sender.call_count, 4)
        self.assertEqual([call.args[0].sock for call in sender.call_args_list],
                         [selected[0], selected[1], selected[1], selected[2]])
        sleep.assert_called_once_with(5)
        self.assertIn("성공 2대 · 실패 1대 · 진행 0대 · 대기 0대", self.app.summary_var.get())
        self.assertEqual(self.app.failed_var.get(), "실패 장비: 2번")
        failed = self.app.clients[selected[1]]
        waiting = self.app.clients[selected[2]]
        self.assertEqual(self.app.device_tree.set(failed["item"], "updated_at"), "-")
        self.assertEqual(self.app.device_tree.item(waiting["item"], "tags"), ("success",))
        self.assertEqual(self.app.status_var.get(), "Done with failures")
        self.app.select_incomplete_devices()
        self.assertEqual(self.app.selected_clients_in_order(), [selected[1]])

    def test_disconnect_does_not_drop_worker_result(self):
        selected = self.prepare_batch()
        client = selected[0]
        self.app.events.put(("connection_lost", client))
        self.app.events.put(("device_status", (client, "OTA Failed")))
        self.drain()
        self.assertNotIn(client, self.app.clients)
        self.assertEqual(self.app.failed_var.get(), "실패 장비: 10번")
        self.assertIn("실패 1대", self.app.summary_var.get())

    def test_incomplete_selection_excludes_success_unrelated_and_disconnected(self):
        selected = self.prepare_batch()
        self.app.batch[self.app.clients[selected[0]]["peer"][0]]["status"] = "Success"
        self.app.batch[self.app.clients[selected[1]]["peer"][0]]["status"] = "OTA Failed"
        self.app.clients[selected[2]]["ready"] = False
        unrelated = self.add_device(42, "718")
        self.app.clients[unrelated]["selected"] = True
        self.app.ota_worker = None
        self.app.select_incomplete_devices()
        self.assertEqual(self.app.selected_clients_in_order(), [selected[1]])
        self.assertFalse(self.app.clients[unrelated]["selected"])
        self.app.clients[selected[2]]["ready"] = True
        self.app.select_incomplete_devices()
        self.assertEqual(self.app.selected_clients_in_order(), selected[1:])

    def test_incomplete_selection_is_locked_during_update(self):
        selected = self.prepare_batch()
        self.app.batch[self.app.clients[selected[0]]["peer"][0]]["status"] = "Success"
        self.app.select_incomplete_devices()
        self.assertEqual(self.app.selected_clients_in_order(), selected)

    def test_multiple_failures_are_all_counted(self):
        selected = self.prepare_batch()
        with patch.object(gui, "send_ota", side_effect=RuntimeError("offline")) as sender, \
                patch.object(gui.time, "sleep"):
            self.app.worker_send_ota(selected, self.bin_path)
        self.drain()
        self.assertEqual(sender.call_count, 6)
        self.assertIn("실패 3대", self.app.summary_var.get())
        self.assertEqual(self.app.failed_var.get(), "실패 장비: 10번, 2번, 30번")

    def test_reconnect_preserves_batch_result_and_success_time(self):
        selected = self.prepare_batch()
        info = self.app.clients[selected[0]]
        self.app.set_device_status(info, "Success")
        stamp = self.app.device_tree.set(info["item"], "updated_at")
        new_client = Mock()
        self.app.events.put(("connected", (new_client, info["peer"])))
        self.app.events.put(("version_result", (new_client, "719", 1)))
        with patch.object(gui.threading, "Thread"):
            self.drain()
        self.assertEqual(self.app.device_tree.item(info["item"], "tags"), ("success",))
        self.assertEqual(self.app.device_tree.set(info["item"], "status"), "전송 성공 / 버전 조회 완료")
        self.assertEqual(self.app.device_tree.set(info["item"], "updated_at"), stamp)

    def test_failed_attempt_does_not_replace_last_success_time(self):
        selected = self.prepare_batch()
        info = self.app.clients[selected[0]]
        self.app.set_device_status(info, "Success")
        history = self.app.history_path.read_text(encoding="utf-8")
        self.app.set_device_status(info, "OTA Failed")
        self.assertEqual(self.app.history_path.read_text(encoding="utf-8"), history)

    def test_saved_history_restored_on_new_connection(self):
        ip = "192.168.1.42"
        self.app.save_update_history(ip, "2026-09-21 10:00:00")
        self.app.update_history = self.app.load_update_history()
        self.app.events.put(("connected", (Mock(), (ip, 9100))))
        with patch.object(gui.threading, "Thread"):
            self.drain()
        item = self.app.device_rows[ip]["item"]
        self.assertEqual(self.app.device_tree.set(item, "updated_at"), "2026-09-21 10:00:00")

    def test_log_tabs_expand_and_collapse(self):
        self.assertEqual(len(self.app.content_panes.panes()), 1)
        self.app.toggle_logs()
        self.assertEqual(len(self.app.content_panes.panes()), 2)
        self.assertEqual(len(self.app.logs.tabs()), 3)
        self.app.toggle_logs()
        self.assertEqual(len(self.app.content_panes.panes()), 1)

    def test_success_schedules_requery_and_clears_old_version(self):
        selected = self.prepare_batch()
        info = self.app.clients[selected[0]]
        self.app.events.put(("device_status", (selected[0], "Success")))
        with patch.object(self.root, "after") as after, patch.object(gui.threading, "Thread") as thread:
            self.drain()
            scheduled = [call for call in after.call_args_list if call.args[0] == 3000]
            self.assertEqual(len(scheduled), 1)
            self.assertEqual(self.app.device_tree.set(info["item"], "version"), "-")
            self.assertFalse(info["ready"])
            thread.assert_not_called()
            scheduled[0].args[1]()
            self.assertEqual(thread.call_args.kwargs["args"],
                             (selected[0], info["peer"], info["version_query_id"]))

    def test_stale_version_is_ignored_and_unavailable_is_not_ota_failure(self):
        selected = self.prepare_batch()
        client = selected[0]
        info = self.app.clients[client]
        self.app.events.put(("device_status", (client, "Success")))
        self.drain()
        query_id = info["version_query_id"]
        self.app.events.put(("version_result", (client, "718", query_id - 1)))
        self.drain()
        self.assertEqual(self.app.device_tree.set(info["item"], "version"), "-")
        self.assertTrue(info["querying"])
        self.app.events.put(("version_result", (client, "-", query_id)))
        with patch.object(gui.threading, "Thread"):
            self.drain()
        self.assertEqual(self.app.device_tree.set(info["item"], "status"),
                         "전송 성공 / 버전 확인 불가")
        self.assertEqual(self.app.batch[info["peer"][0]]["status"], "Success")
        self.assertFalse(info["querying"])

    def test_reconnect_invalidates_delayed_query_on_old_socket(self):
        selected = self.prepare_batch()
        client = selected[0]
        info = self.app.clients[client]
        self.app.events.put(("device_status", (client, "Success")))
        with patch.object(self.root, "after") as after, patch.object(gui.threading, "Thread") as thread:
            self.drain()
            old_begin = [c.args[1] for c in after.call_args_list if c.args[0] == 3000][0]
            replacement = Mock()
            self.app.events.put(("connected", (replacement, info["peer"])))
            self.drain()
            old_begin()
            thread.assert_not_called()
            callbacks = [c for c in after.call_args_list if c.args[0] > 100]
            self.assertEqual(len(callbacks), 2)
            callbacks[-1].args[1]()
            self.assertEqual(thread.call_args.kwargs["args"][0], replacement)

    def test_version_response_updates_actual_version_without_target_comparison(self):
        selected = self.prepare_batch()
        client = selected[0]
        info = self.app.clients[client]
        self.app.events.put(("device_status", (client, "Success")))
        self.drain()
        query_id = info["version_query_id"]
        with patch.object(gui.time, "sleep"), patch.object(gui, "read_frame",
                return_value=("VER=", (718).to_bytes(2, "little"), b"")):
            self.app.query_device_version(client, info["peer"], query_id)
        with patch.object(gui.threading, "Thread"):
            self.drain()
        self.assertEqual(self.app.device_tree.set(info["item"], "version"), "718")
        self.assertEqual(self.app.device_tree.set(info["item"], "status"),
                         "전송 성공 / 버전 조회 완료")


if __name__ == "__main__":
    unittest.main()
