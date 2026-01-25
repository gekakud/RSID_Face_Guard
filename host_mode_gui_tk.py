#!/usr/bin/env python3

"""
Tkinter GUI for RealSense ID Host Mode with camera preview and authentication
Based on host_mode_gui.py (PyQt5 version) - converted to Tkinter
"""

import argparse
import copy
import ctypes
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import traceback
from typing import Optional

# Configuration
SIMULATE_HW = True
CUSTOM_THRESHOLD = 400

# Set to True for RPi5 with small 800x480 screen (fullscreen mode)
RUN_SMALL_SCREEN = True

# Small display resolution settings
SMALL_W = 800
SMALL_H = 480

# Card API support
if SIMULATE_HW:
    from card_api_sim import (
        initialize_card_reader, get_card_id, disconnect_card_reader,
        initialize_wiegand_tx, send_w32, close_wiegand_tx,
    )
else:
    try:
        from card_api import (
            initialize_card_reader, get_card_id, disconnect_card_reader,
            initialize_wiegand_tx, send_w32, close_wiegand_tx,
        )
    except ImportError:
        print('Card API module not available.')
        sys.exit(1)

# Import rsid_py BEFORE tkinter to avoid native library conflicts
try:
    import rsid_py
    print('rsid_py Version: ' + rsid_py.__version__)
except ImportError:
    print('Failed importing rsid_py. Please ensure rsid_py module is available.')
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print('Failed importing numpy. Please install it (pip install numpy).')
    sys.exit(1)

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk, ImageFont
except ImportError:
    print('Failed importing PIL. Please install Pillow (pip install Pillow).')
    sys.exit(1)

try:
    import tkinter as tk
    import tkinter.ttk as ttk
except ImportError:
    print('Failed importing tkinter.')
    sys.exit(1)

from user_db import UserDatabase

WINDOW_NAME = 'RealSenseID Host Mode'


def _find_display_xy_by_resolution(prefer_w=800, prefer_h=480):
    """
    Return (x, y) offset of the first connected display with resolution prefer_w x prefer_h.
    Uses xrandr output (works under X11/XWayland). Returns None if not found / not available.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        out = subprocess.check_output(["xrandr"], text=True)
        pat = re.compile(r"connected\s+(?P<w>\d+)x(?P<h>\d+)\+(?P<x>\d+)\+(?P<y>\d+)")
        for line in out.splitlines():
            if " connected " not in line:
                continue
            m = pat.search(line)
            if not m:
                continue
            w = int(m.group("w"))
            h = int(m.group("h"))
            x = int(m.group("x"))
            y = int(m.group("y"))
            if w == prefer_w and h == prefer_h:
                return (x, y)
    except Exception as e:
        print("xrandr detect failed:", e)
    return None


class PreviewController(threading.Thread):
    """Handles camera preview in a separate thread"""
    
    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        super().__init__(daemon=True)
        self.port = port
        self.camera_index = camera_index
        self.device_type = device_type
        self.running = True
        self.preview = None
        self.image_queue = queue.Queue(maxsize=2)
        
    def on_image(self, image):
        """Callback for preview frames"""
        if not self.running:
            return
        try:
            buffer = memoryview(image.get_buffer())
            arr = np.asarray(buffer, dtype=np.uint8)
            array2d = arr.reshape((image.height, image.width, -1))
            if self.image_queue.full():
                try:
                    self.image_queue.get_nowait()
                except queue.Empty:
                    pass
            self.image_queue.put(array2d.copy())
        except Exception:
            traceback.print_exc()
    
    def start_preview(self):
        """Initialize and start the camera preview"""
        preview_cfg = rsid_py.PreviewConfig()
        preview_cfg.device_type = self.device_type
        preview_cfg.camera_number = self.camera_index
        preview_cfg.preview_mode = rsid_py.PreviewMode.MJPEG_1080P
        
        self.preview = rsid_py.Preview(preview_cfg)
        self.preview.start(preview_callback=self.on_image, snapshot_callback=None)
    
    def run(self):
        """Main thread loop"""
        self.start_preview()
        while self.running:
            threading.Event().wait(0.1)
        
        if self.preview:
            self.preview.stop()
            self.preview = None
        print("Preview controller thread exited")
    
    def stop(self):
        """Stop the preview thread"""
        self.running = False


class HostModeService:
    """Business logic for host mode authentication"""
    
    def __init__(self, port: str):
        self.port = port
        self.user_db = UserDatabase()
        
        try:
            initialize_wiegand_tx()
            print("Wiegand transmitter initialized")
        except Exception as e:
            print(f"Wiegand initialization failed: {e}")
    
    def authenticate_all_users(self) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate by matching against all users in database"""
        all_users = self.user_db.get_all_users()
        if not all_users:
            return False, None, "No users in database"
        
        auth_status = None
        extracted_prints = None
        
        def on_fp_auth_result(status, new_prints):
            nonlocal auth_status, extracted_prints
            auth_status = status
            extracted_prints = new_prints
        
        try:
            with rsid_py.FaceAuthenticator(self.port) as authenticator:
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    return False, None, f"Face extraction failed: {auth_status}"
                
                max_score = -100
                selected_user_id = None
                selected_user_info = None
                
                for user_id, user_info in all_users.items():
                    fp = user_info.get('faceprints')
                    if not fp:
                        continue
                    
                    db_faceprints = rsid_py.Faceprints()
                    db_faceprints.version = fp['version']
                    db_faceprints.features_type = fp['features_type']
                    db_faceprints.flags = fp['flags']
                    db_faceprints.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
                    db_faceprints.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
                    db_faceprints.enroll_descriptor = fp['enroll_descriptor']
                    
                    updated_faceprints = rsid_py.Faceprints()
                    match_result = authenticator.match_faceprints(
                        extracted_prints, db_faceprints, updated_faceprints
                    )
                    
                    if match_result.success and match_result.score > max_score:
                        max_score = match_result.score
                        selected_user_id = user_id
                        selected_user_info = user_info
                
                if selected_user_id:
                    send_w32(int(selected_user_id))
                    return True, selected_user_info['name'], selected_user_info['permission_level']
                else:
                    return False, None, "No match found"
                    
        except Exception as e:
            return False, None, str(e)
    
    def cleanup(self):
        """Cleanup resources"""
        try:
            disconnect_card_reader()
            close_wiegand_tx()
        except:
            pass


class GUI(tk.Tk):
    """Main Tkinter GUI window"""
    
    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        super().__init__(className=WINDOW_NAME)
        
        self.port = port
        self.image = None
        self.scaled_image = None
        self.video_update_handle = None
        self.result_hide_handle = None
        
        # Initialize services
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port)
        
        # Authentication state
        self.auth_in_progress = False
        
        # Window setup
        if RUN_SMALL_SCREEN:
            self.geometry(f"{SMALL_W}x{SMALL_H}")
            self.config(cursor="none")
            self._place_on_correct_display()
        else:
            max_w = 720
            max_h = 900
            self.geometry(f"{max_w}x{max_h}")
            self.minsize(500, 600)
        
        self.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.bind('<Escape>', lambda e: self.exit_app())
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        
        # Video canvas
        self.canvas = tk.Canvas(self, bg='black', highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        
        self.canvas_image_id = None
        self.canvas_result_id = None
        self.canvas_result_bg_id = None
        
        # Button frame
        button_frame = ttk.Frame(self)
        button_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(5, 10))
        button_frame.grid_columnconfigure(0, weight=1)
        
        # Style for big button
        style = ttk.Style(self)
        if sys.platform.startswith('win'):
            style.theme_use('vista')
        else:
            style.theme_use('clam')
        
        # Configure big button style
        if RUN_SMALL_SCREEN:
            style.configure('Big.TButton', font=('Arial', 20, 'bold'), padding=(20, 30))
        else:
            style.configure('Big.TButton', font=('Arial', 28, 'bold'), padding=(30, 40))
        
        self.auth_button = ttk.Button(
            button_frame, 
            text="Authenticate", 
            command=self.authenticate,
            style='Big.TButton'
        )
        if RUN_SMALL_SCREEN:
            self.auth_button.grid(row=0, column=0, sticky="ew", ipady=20)
        else:
            self.auth_button.grid(row=0, column=0, sticky="ew", ipady=30)
        
        # Start preview
        self.preview_controller.start()
        
        # Start video update loop
        self.after(50, self.update_video)
        self.after(200, self.update_app_icon)
    
    def _place_on_correct_display(self):
        """Place window on the small display if connected (Linux only via xrandr)"""
        pos = _find_display_xy_by_resolution(SMALL_W, SMALL_H)
        if pos is not None:
            x, y = pos
            try:
                self.geometry(f"+{x}+{y}")
                self.update_idletasks()
                print(f"GUI moved to small display at {x},{y}")
                self.attributes("-fullscreen", True)
            except Exception as e:
                print("Failed placing window:", e)
        else:
            try:
                self.geometry("+0+0")
                self.update_idletasks()
                print("Small display not found -> using primary screen")
            except Exception:
                pass
    
    def update_app_icon(self):
        """Set window icon"""
        icon = Image.new("RGB", (50, 50))
        op = ImageDraw.Draw(icon)
        op.text((10, 0), "R", font_size=40, fill="white")
        self.icon = ImageTk.PhotoImage(icon)
        self.wm_iconphoto(False, self.icon)
    
    def update_video(self):
        """Update video display"""
        self.update_idletasks()
        
        if not self.preview_controller.image_queue.empty() and self.preview_controller.running:
            array2d = None
            while not self.preview_controller.image_queue.empty():
                array2d = self.preview_controller.image_queue.get()
            
            if array2d is not None:
                try:
                    self.image = Image.fromarray(array2d, mode="RGB")
                except Exception:
                    pass
        
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        if self.image is not None and canvas_w > 1 and canvas_h > 1:
            image = self.image.copy()
            scaled_image = ImageOps.contain(image, size=(canvas_w, canvas_h)).transpose(Image.FLIP_LEFT_RIGHT)
            self.scaled_image = ImageTk.PhotoImage(image=scaled_image)
            
            if self.canvas_image_id is None:
                self.canvas_image_id = self.canvas.create_image(
                    canvas_w // 2, canvas_h // 2, 
                    anchor=tk.CENTER, 
                    image=self.scaled_image
                )
            else:
                self.canvas.itemconfig(self.canvas_image_id, image=self.scaled_image)
                self.canvas.coords(self.canvas_image_id, canvas_w // 2, canvas_h // 2)
        
        if self.preview_controller.running:
            self.video_update_handle = self.after(30, self.update_video)
    
    def show_result(self, success: bool):
        """Show big ✓ or ✗ overlay for 3 seconds"""
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        # Remove previous result if any
        if self.canvas_result_bg_id:
            self.canvas.delete(self.canvas_result_bg_id)
        if self.canvas_result_id:
            self.canvas.delete(self.canvas_result_id)
        
        # Draw semi-transparent background
        box_size = 300 if RUN_SMALL_SCREEN else 400
        x1 = (canvas_w - box_size) // 2
        y1 = (canvas_h - box_size) // 2
        x2 = x1 + box_size
        y2 = y1 + box_size
        
        self.canvas_result_bg_id = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill='black',
            stipple='gray50',
            outline=''
        )
        
        # Draw checkmark or X
        symbol = "✓" if success else "✗"
        color = '#4CAF50' if success else '#F44336'  # Green or Red
        font_size = 150 if RUN_SMALL_SCREEN else 200
        
        self.canvas_result_id = self.canvas.create_text(
            canvas_w // 2, canvas_h // 2,
            text=symbol,
            font=('Arial', font_size, 'bold'),
            fill=color
        )
        
        # Schedule hiding after 3 seconds
        if self.result_hide_handle:
            self.after_cancel(self.result_hide_handle)
        self.result_hide_handle = self.after(3000, self.hide_result)
    
    def hide_result(self):
        """Hide the result overlay"""
        if self.canvas_result_bg_id:
            self.canvas.delete(self.canvas_result_bg_id)
            self.canvas_result_bg_id = None
        if self.canvas_result_id:
            self.canvas.delete(self.canvas_result_id)
            self.canvas_result_id = None
    
    def authenticate(self):
        """Start authentication"""
        if self.auth_in_progress:
            return
        
        self.auth_in_progress = True
        self.auth_button.state(['disabled'])
        
        # Run in thread
        auth_thread = threading.Thread(target=self._run_authentication, daemon=True)
        auth_thread.start()
    
    def _run_authentication(self):
        """Run authentication (in separate thread)"""
        try:
            success, name, permission = self.host_service.authenticate_all_users()
            
            if success:
                message = f"✅ Access granted: {name} ({permission})"
            else:
                message = f"❌ Access denied: {permission}"
            
            print(message)
            
            # Update GUI in main thread
            self.after(0, lambda: self._on_auth_complete(success))
            
        except Exception as e:
            print(f"Error: {e}")
            self.after(0, lambda: self._on_auth_complete(False))
    
    def _on_auth_complete(self, success: bool):
        """Handle authentication completion (called in main thread)"""
        self.auth_in_progress = False
        self.auth_button.state(['!disabled'])
        self.show_result(success)
    
    def exit_app(self):
        """Exit the application"""
        if self.video_update_handle:
            self.after_cancel(self.video_update_handle)
        self.preview_controller.stop()
        self.host_service.cleanup()
        self.quit()


def main():
    parser = argparse.ArgumentParser(prog='host_mode_gui_tk', description='RealSense ID Host Mode GUI (Tkinter)')
    parser.add_argument('-p', '--port', help='Device port', type=str, default=None)
    parser.add_argument('-c', '--camera', help='Camera number (-1 for autodetect)', type=int, default=-1)
    args = parser.parse_args()
    
    # Determine port
    if args.port is None:
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            port = "COM9" if platform.system() == "Windows" else "/dev/ttyACM0"
            print(f"No devices auto-detected. Trying default port: {port}")
        else:
            port = devices[0]
            print(f"Auto-detected device on port: {port}")
    else:
        port = args.port
        print(f"Using specified port: {port}")
    
    camera_index = args.camera
    
    # Discover device type
    print(f"Discovering device type on port: {port}...")
    try:
        device_type = rsid_py.discover_device_type(port)
        print(f"Device type: {device_type}")
    except Exception as e:
        print(f"Could not connect to device on port {port}: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    # Configure device
    config = None
    print("Configuring device...")
    with rsid_py.FaceAuthenticator(device_type, str(port)) as f:
        try:
            config = copy.copy(f.query_device_config())
            config.dump_mode = rsid_py.DumpMode.Disable
            f.set_device_config(config)
            print("Device configured successfully")
        except Exception as e:
            print(f"Device configuration error: {e}")
            traceback.print_exc(file=sys.stdout)
            os._exit(1)
        finally:
            f.disconnect()
    
    print(f"Using port: {port} ({device_type})")
    print(f"Using camera index: {camera_index}")
    
    # Initialize card reader
    initialize_card_reader()
    print("Card reader initialized")
    
    # Create and run GUI
    gui = GUI(port, camera_index, device_type)
    gui.mainloop()


if __name__ == '__main__':
    if sys.platform.startswith('win'):
        app_id = 'intel.realsenseid.hostmode.1.0'
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except:
            ctypes.windll.user32.SetProcessDPIAware()
    
    main()
