import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from ota_w5500_gui import build_pages
from ota_send_gui import OtaSenderApp

class PageTests(unittest.TestCase):
    def test_navigation_scan_selection_and_logs(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(OtaSenderApp, 'load_update_history', return_value={}), patch('ota_send_gui.get_local_ipv4_addresses', return_value=['192.168.0.231']):
            camera, direct, thermal = build_pages(root)
        root.update_idletasks()
        self.assertEqual(len(direct.content_panes.panes()), 1)
        self.assertEqual(direct.server_button['text'], 'SCAN')
        self.assertEqual(camera.server_button['text'], 'Start Server')
        direct.apply_scan({'192.168.30.200': '321', '192.168.30.201': '322'})
        direct.update_action_buttons()
        direct.toggle_all_selection()
        self.assertEqual(len(direct.selected_clients_in_order()), 2)
        direct.apply_scan({'192.168.30.201': '323'})
        direct.update_action_buttons()
        self.assertEqual(direct.selected_clients_in_order(), ['192.168.30.201'])
        self.assertEqual(direct.device_tree.set(direct.clients['192.168.30.201']['item'], 'version'), '323')
        direct.log('RX token FAIL')
        direct.process_events()
        self.assertIn('RX token FAIL', direct.log_text.get('1.0', 'end'))
        # Top buttons switch the packed page without creating a second window.
        buttons = root.winfo_children()[0].winfo_children()
        buttons[1].invoke()
        root.update_idletasks()
        self.assertEqual(direct.ui_parent.winfo_manager(), 'pack')
        self.assertEqual(camera.ui_parent.winfo_manager(), '')
        self.assertEqual([button['text'] for button in buttons],
                         ['바이패스', 'W5500 구역제어기', 'W5500 열화상 카메라'])
        buttons[2].invoke()
        root.update_idletasks()
        self.assertEqual(thermal.ui_parent.winfo_manager(), 'pack')
        self.assertEqual(direct.ui_parent.winfo_manager(), '')
        thermal.apply_scan({'192.168.0.30': '721'})
        self.assertEqual(thermal.clients['192.168.0.30']['peer'], ('192.168.0.30', 9303))
        self.assertEqual(thermal.port_var.get(), '9303')

if __name__ == '__main__': unittest.main()
