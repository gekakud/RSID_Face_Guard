#!/usr/bin/env python3
"""
License: Apache 2.0. See LICENSE file in root directory.
Copyright(c) 2020-2024 Intel Corporation. All Rights Reserved.

Modified by: (your changes)
Key changes:
- Works with 2 displays or only the small 3.5" display:
  * Detects 800x480 display via xrandr (X11/XWayland)
  * Moves the MAIN Tk window to the small display AFTER it is mapped (Wayland-safe)
  * Optional fullscreen on small display (kiosk mode)
- USE_CARD_READER flag:
  * If False: Authenticate button matches against ALL users in DB (no card_id filtering)
  * If True: Keeps background reading and auto-auth on card present
- Video scaling:
  * Uses ImageOps.fit to fill the screen (wide view) instead of contain
"""

import argparse
import copy
import ctypes
import json
import os
import pathlib
import queue
import signal
import sys
import threading
import time
import traceback
import platform
import subprocess
import re

# -------------------- Flags --------------------
USE_CARD_READER = False          # True if you want background card reading
KIOSK_ON_SMALL_DISPLAY = True    # Fullscreen on small display (800x480)
SMALL_W = 800
SMALL_H = 480

# If card reader is disabled, don't require the module at runtime
if USE_CARD_READER:
    from card_reader_api import initialize_card_reader, get_card_id, disconnect_card_reader

import PIL

try:
    import numpy as np
except ImportError:
    print('Failed importing numpy. Please install it (pip install numpy).')
    print('  On Ubuntu, you may install the system wide package instead: sudo apt install python3-numpy')
    raise

try:
    import tkinter as tk
    import tkinter.ttk as ttk
    from tkinter import messagebox, simpledialog
except ImportError as ex:
    print(f'Failed importing tkinter ({ex}).')
    print('  On Ubuntu, you also need to: sudo apt install python3-tk')
    print('  On Fedora, you also need to: sudo dnf install python3-tkinter')
    raise

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError as ex:
    print(f'Failed importing PIL ({ex}). Please install Pillow *version 9.1.0 or newer* (pip install Pillow).')
    print('  On Ubuntu, you may install the system wide package instead: sudo apt install python3-pil python3-pil.imagetk')
    raise

import rsid_py

print('Version: ' + rsid_py.__version__)

# -------------------- Globals --------------------
WINDOW_NAME = 'RealSenseID'
USER_DB_FILE = 'user_database.json'


# ============================================================
# Display helpers (XWayland/Wayland-safe placement)
# ============================================================
def _find_display_xy_by_resolution(prefer_w=800, prefer_h=480):
    """
    Return (x, y) offset of the first connected display with resolution prefer_w x prefer_h.
    Uses xrandr output (works under X11/XWayland). Returns None if not found / not available.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        out = subprocess.check_output(["xrandr"], text=True)
        # Example:
        # XWAYLAND0 connected 800x480+1920+0 inverted ...
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


def _count_connected_displays():
    if not sys.platform.startswith("linux"):
        return 1
    try:
        out = subprocess.check_output(["xrandr"], text=True)
        return sum(1 for l in out.splitlines() if " connected " in l)
    except Exception:
        return 1


# ============================================================
# User database
# ============================================================
class UserDatabase:
    """Manage user database in JSON file"""

    def __init__(self, filename=USER_DB_FILE):
        self.filename = filename
        self.users = self.load_users()

    def load_users(self):
        """Load users from JSON file"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading user database: {e}")
                return {}
        return {}

    def save_users(self):
        """Save users to JSON file"""
        try:
            with open(self.filename, 'w') as f:
                json.dump(self.users, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving user database: {e}")
            return False

    def add_user(self, user_id, name, permission_level, faceprints=None):
        """Add a new user to the database, including faceprints if provided"""
        self.users[user_id] = {
            'name': name,
            'id': user_id,
            'permission_level': permission_level,
            'faceprints': faceprints
        }
        return self.save_users()

    def get_user(self, user_id):
        """Get user details by ID"""
        return self.users.get(user_id, None)

    def delete_user(self, user_id):
        """Delete a user from the database"""
        if user_id in self.users:
            del self.users[user_id]
            return self.save_users()
        return False

    def clear_all(self):
        """Clear all users from the database"""
        self.users = {}
        return self.save_users()

    def get_all_users(self):
        """Get all users"""
        return self.users


# ============================================================
# Enroll dialog
# ============================================================
class EnrollDialog(tk.Toplevel):
    """Custom dialog for enrolling users with additional fields"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.result = None

        self.title("Enroll New User")
        self.geometry("400x250")
        self.resizable(False, False)

        # Center the dialog
        self.transient(parent)
        self.grab_set()

        # Move dialog to same screen as parent (good UX)
        try:
            self.update_idletasks()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            self.geometry(f"+{px+40}+{py+40}")
        except Exception:
            pass

        # Create form fields
        tk.Label(self, text="User ID:").grid(row=0, column=0, padx=10, pady=10, sticky='e')
        self.id_entry = tk.Entry(self, width=30)
        self.id_entry.grid(row=0, column=1, padx=10, pady=10)

        tk.Label(self, text="Name:").grid(row=1, column=0, padx=10, pady=10, sticky='e')
        self.name_entry = tk.Entry(self, width=30)
        self.name_entry.grid(row=1, column=1, padx=10, pady=10)

        tk.Label(self, text="Permission Level:").grid(row=2, column=0, padx=10, pady=10, sticky='e')
        self.permission_var = tk.StringVar(value="Limited access")
        self.permission_combo = ttk.Combobox(self, textvariable=self.permission_var, width=27, state='readonly')
        self.permission_combo['values'] = ('Extended access', 'Limited access')
        self.permission_combo.grid(row=2, column=1, padx=10, pady=10)

        # Buttons
        button_frame = tk.Frame(self)
        button_frame.grid(row=3, column=0, columnspan=2, pady=20)

        tk.Button(button_frame, text="OK", command=self.ok_pressed, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(button_frame, text="Cancel", command=self.cancel_pressed, width=10).pack(side=tk.LEFT, padx=5)

        # Focus on first field
        self.id_entry.focus_set()

        # Bind Enter key
        self.bind('<Return>', lambda e: self.ok_pressed())
        self.bind('<Escape>', lambda e: self.cancel_pressed())

    def ok_pressed(self):
        user_id = self.id_entry.get().strip()
        name = self.name_entry.get().strip()
        permission = self.permission_var.get()

        if not user_id:
            messagebox.showerror("Error", "User ID is required!", parent=self)
            return

        if not name:
            messagebox.showerror("Error", "Name is required!", parent=self)
            return

        self.result = {
            'id': user_id,
            'name': name,
            'permission_level': permission
        }
        self.destroy()

    def cancel_pressed(self):
        self.result = None
        self.destroy()


# ============================================================
# Controller thread
# ============================================================
class Controller(threading.Thread):
    status_msg: str
    detected_faces: list[dict]  # array of (faces, success, user_name)
    running: bool = True
    image_q: queue.Queue = queue.Queue()
    snapshot_q: queue.Queue = queue.Queue()

    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType, dump_mode: rsid_py.DumpMode):
        super().__init__()
        self.preview = None
        self.status_msg = ''
        self.detected_faces = []
        self.port = port
        self.camera_index = camera_index
        self.device_type = device_type
        self.dump_mode = dump_mode
        self.user_db = UserDatabase()

        if self.dump_mode in [rsid_py.DumpMode.CroppedFace, rsid_py.DumpMode.FullFrame]:
            self.status_msg = '-- Dump Mode --' if rsid_py.DumpMode.FullFrame == self.dump_mode else '-- Cropped Face --'
            (pathlib.Path('.') / 'dumps').mkdir(parents=True, exist_ok=True)
            if not (pathlib.Path('.') / 'dumps').exists():
                raise RuntimeError('Unable to create dumps directory.')

    def reset(self):
        self.status_msg = ''
        self.detected_faces = []

    def on_result(self, result, user_id=None):
        success = result == rsid_py.AuthenticateStatus.Success
        if success and user_id:
            user_info = self.user_db.get_user(user_id)
            if user_info:
                self.status_msg = f'Success: {user_info["name"]} (ID: {user_id}, Access: {user_info["permission_level"]})'
            else:
                self.status_msg = f'Success "{user_id}" (Not in database)'
        else:
            self.status_msg = str(result)

        for f in self.detected_faces:
            if 'success' not in f:
                f['success'] = success
                f['user_id'] = user_id
                break

    def on_progress(self, p: rsid_py.FacePose):
        self.status_msg = f'on_progress {p}'

    def on_hint(self, hint: rsid_py.AuthenticateStatus | rsid_py.EnrollStatus | None):
        self.status_msg = f'{hint}'

    def on_faces(self, faces: list[rsid_py.FaceRect], timestamp: int):
        self.status_msg = f'detected {len(faces)} face(s)'
        self.detected_faces = [{'face': f} for f in faces]

    # -------------------- AUTH --------------------
    def authenticate_user(self, card_id: int | None = None):
        """
        If card_id is provided: only compare against that user_id (legacy behavior)
        If card_id is None: compare against ALL users in DB (no card required)
        """
        def on_fp_auth_result(status, new_prints, authenticator):
            if status != rsid_py.AuthenticateStatus.Success:
                self.status_msg = f"Authentication failed: {status}"
                return

            max_score = -1e9
            selected_user_id = None

            print(f"Card ID read: {card_id}")

            for user_id, user_info in self.user_db.get_all_users().items():
                if card_id is not None:
                    try:
                        if int(user_id) != int(card_id):
                            continue
                    except ValueError:
                        continue

                fp = user_info.get('faceprints')
                if not fp:
                    continue

                db_item = rsid_py.Faceprints()
                db_item.version = fp['version']
                db_item.features_type = fp['features_type']
                db_item.flags = fp['flags']
                db_item.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
                db_item.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
                db_item.enroll_descriptor = fp['enroll_descriptor']

                updated_faceprints = rsid_py.Faceprints()
                match_result = authenticator.match_faceprints(new_prints, db_item, updated_faceprints)

                if match_result.success and match_result.score > max_score:
                    max_score = match_result.score
                    selected_user_id = user_id

            if selected_user_id:
                user_info = self.user_db.get_user(selected_user_id)
                self.status_msg = f"Authenticated: {user_info['name']} (ID: {selected_user_id})  score={max_score:.2f}"
            else:
                self.status_msg = "No match found"

        with rsid_py.FaceAuthenticator(self.port) as authenticator:
            self.status_msg = "Authenticating.."
            authenticator.extract_faceprints_for_auth(
                on_result=lambda status, new_prints: on_fp_auth_result(status, new_prints, authenticator)
            )

    # -------------------- ENROLL --------------------
    def enroll_user(self, user_id, name, permission_level):
        def on_fp_enroll_result(status, extracted_prints):
            if status == rsid_py.EnrollStatus.Success:
                faceprints_data = {
                    'version': extracted_prints.version,
                    'features_type': extracted_prints.features_type,
                    'flags': extracted_prints.flags,
                    'adaptive_descriptor_nomask': list(extracted_prints.features),
                    'adaptive_descriptor_withmask': [0] * 515,
                    'enroll_descriptor': list(extracted_prints.features)
                }
                self.user_db.add_user(user_id, name, permission_level, faceprints=faceprints_data)
                self.status_msg = f"Enroll Success: {name} (ID: {user_id})"
            else:
                self.status_msg = f"Enroll Failed: {status}"

        with rsid_py.FaceAuthenticator(self.port) as authenticator:
            self.status_msg = "Enroll.."
            authenticator.extract_faceprints_for_enroll(on_progress=self.on_progress, on_result=on_fp_enroll_result)

    # -------------------- REMOVE --------------------
    def remove_all_users(self):
        with rsid_py.FaceAuthenticator(self.port) as f:
            self.status_msg = "Remove.."
            f.remove_all_users()
            self.user_db.clear_all()
            self.status_msg = 'Remove All Success'

    def remove_user(self, user_id):
        with rsid_py.FaceAuthenticator(self.port) as f:
            self.status_msg = f"Removing user {user_id}.."
            users = f.query_user_ids()
            if user_id in users:
                f.remove_user(user_id)
                self.user_db.delete_user(user_id)
                self.status_msg = f'User {user_id} removed successfully'
                return True
            else:
                self.status_msg = f'User {user_id} not found'
                return False

    def query_users(self):
        with rsid_py.FaceAuthenticator(self.port) as f:
            return f.query_user_ids()

    # -------------------- PREVIEW --------------------
    def on_image(self, image):
        if not self.running:
            return
        try:
            buffer = memoryview(image.get_buffer())
            arr = np.asarray(buffer, dtype=np.uint8)
            array2d = arr.reshape((image.height, image.width, -1))
            self.image_q.put(array2d.copy())
        except Exception:
            print("Exception in on_image")
            print("-" * 60)
            traceback.print_exc(file=sys.stdout)
            print("-" * 60)

    def on_snapshot(self, image):
        try:
            if self.dump_mode == rsid_py.DumpMode.FullFrame:
                buffer = copy.copy(bytearray(image.get_buffer()))
                dump_path = (pathlib.Path('.') / 'dumps' / f'timestamp-{image.metadata.timestamp}')
                dump_path.mkdir(parents=True, exist_ok=True)
                file_name = (f'{image.metadata.timestamp}-{image.metadata.status}-{image.metadata.sensor_id}-'
                             f'{image.metadata.exposure}-{image.metadata.gain}.w10')
                file_path = dump_path / file_name
                with open(file_path, 'wb') as fd:
                    fd.write(buffer)
                print(f'RAW File saved to: {file_path.absolute()}')
            elif self.dump_mode == rsid_py.DumpMode.CroppedFace:
                buffer = image.get_buffer()
                image = Image.frombytes('RGB', (image.width, image.height), buffer, 'raw', 'RGB', 0, 1)
                self.snapshot_q.put(image)
        except Exception:
            print("Exception in on_snapshot")
            print("-" * 60)
            traceback.print_exc(file=sys.stdout)
            print("-" * 60)

    def start_preview(self):
        preview_cfg = rsid_py.PreviewConfig()
        preview_cfg.device_type = self.device_type
        preview_cfg.camera_number = self.camera_index

        if self.dump_mode == rsid_py.DumpMode.FullFrame:
            preview_cfg.preview_mode = rsid_py.PreviewMode.RAW10_1080P
        else:
            preview_cfg.preview_mode = rsid_py.PreviewMode.MJPEG_1080P

        self.preview = rsid_py.Preview(preview_cfg)
        self.preview.start(preview_callback=self.on_image, snapshot_callback=self.on_snapshot)

    def run(self):
        self.start_preview()
        while self.running:
            if USE_CARD_READER:
                try:
                    cid = get_card_id(timeout=0.1)
                except Exception:
                    cid = None
                if cid is not None:
                    print(f"Card ID read in preview thread: {cid}")
                    self.authenticate_user(card_id=cid)
            else:
                time.sleep(0.05)

        if self.preview is not None:
            self.preview.stop()
            self.preview = None
        print("Controller thread exited")

    def exit_thread(self):
        self.status_msg = 'Bye.. :)'
        self.running = False
        time.sleep(0.2)


# ============================================================
# GUI
# ============================================================
class GUI(tk.Tk):
    def __init__(self, controller: Controller):
        super().__init__(className=WINDOW_NAME)

        self.controller = controller
        self.scaled_image = None
        self.image = None
        self.snapshot_image = None

        self.reset_handle = None
        self.video_update_handle = None
        self.resize_handle = None
        self.snapshot_handle = None

        # Start window size (will go fullscreen on small display if enabled)
        self.win_w = int(720 / 1.5)
        self.win_h = int(1280 / 1.5) + 80
        self.geometry(f"{self.win_w}x{self.win_h}")
        self.minsize(int(self.win_w / 1.5), int(self.win_h / 2.5))
        self.maxsize(self.win_w, self.win_h)

        # Window bindings
        self.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.bind('<Escape>', lambda e: self.exit_app())
        self.bind("<Configure>", self.resize)
        self.bind("<Key>", self.key_event)

        self.grid_columnconfigure((1, 0), weight=1)
        self.grid_rowconfigure((1, 0), weight=1)

        # Video frame
        self.video_frame = ttk.Frame(self)
        self.video_frame.grid(row=0, column=0, padx=(0, 0), pady=(0, 20), sticky="nsew", columnspan=2)
        self.video_frame.grid_rowconfigure((0, 1), weight=1)
        self.video_frame.grid_columnconfigure((0, 1), weight=1)

        self.canvas = tk.Canvas(self.video_frame, bg='black')
        self.canvas.grid(row=0, column=0, padx=0, pady=0, sticky="nsew", columnspan=1)
        self.canvas.configure(width=self.win_w, height=self.win_h)

        # Canvas IDs
        self.reset_canvas = True
        self.canvas_image_id = None
        self.canvas_text_id = None
        self.canvas_text_bg_id = None
        self.canvas_snapshot_image_id = None

        # Button frame
        self.button_frame = ttk.Frame(self)
        self.button_frame.grid(row=1, column=0, padx=(5, 5), pady=(0, 5), sticky="nsew", columnspan=2)

        self.auth_button = ttk.Button(self.button_frame, text="Authenticate", command=self.authenticate)
        self.auth_button.grid(row=0, column=0, padx=(5, 5), pady=(5, 5), ipady=5, sticky="nsew")

        self.enroll_button = ttk.Button(self.button_frame, text="Enroll", command=self.enroll)
        self.enroll_button.grid(row=0, column=1, padx=(5, 5), pady=(5, 5), ipady=5, sticky="nsew")

        self.delete_button = ttk.Button(self.button_frame, text="Delete All", command=self.remove_all_users)
        self.delete_button.grid(row=0, column=2, padx=(5, 5), pady=(5, 5), ipady=5, sticky="nsew")

        self.delete_user_button = ttk.Button(self.button_frame, text="Delete User", command=self.delete_user)
        self.delete_user_button.grid(row=0, column=3, padx=(5, 5), pady=(5, 5), ipady=5, sticky="nsew")

        self.show_users_button = ttk.Button(self.button_frame, text="Show Users", command=self.show_all_users)
        self.show_users_button.grid(row=0, column=4, padx=(5, 5), pady=(5, 5), ipady=5, sticky="nsew")

        for i in range(5):
            self.button_frame.grid_columnconfigure(i, weight=1)

        style = ttk.Style(self)
        style.theme_use('clam' if not sys.platform.startswith('win') else 'vista')

        # Start loops
        self.after(50, self.update_video)
        self.after(200, self.update_app_icon)

        # CRITICAL: place window AFTER mapping (Wayland/XWayland)
        self.after(400, self._place_on_correct_display)

    # -------------------- Placement --------------------
    def _place_on_correct_display(self):
        pos = _find_display_xy_by_resolution(SMALL_W, SMALL_H)
        if pos is not None:
            x, y = pos
            try:
                # Move to the small display
                self.geometry(f"+{x}+{y}")
                self.update_idletasks()
                print(f"GUI moved to small display at {x},{y}")

                if KIOSK_ON_SMALL_DISPLAY:
                    # Fullscreen on small display
                    self.attributes("-fullscreen", True)
            except Exception as e:
                print("Failed placing window:", e)
        else:
            # No small display found -> stay on primary (0,0)
            try:
                self.geometry("+0+0")
                self.update_idletasks()
                print("Small display not found -> using primary screen")
            except Exception:
                pass

    # -------------------- Icon --------------------
    def update_app_icon(self):
        icon = Image.new("RGB", (50, 50))
        op = ImageDraw.Draw(icon)
        # NOTE: PIL ImageDraw doesn't support font_size kwarg reliably; keep simple
        op.text((18, 10), "R", fill="white")
        self.icon = ImageTk.PhotoImage(icon)
        self.wm_iconphoto(False, self.icon)

    # -------------------- Keys --------------------
    def key_event(self, event):
        cmd_exec = {'a': self.authenticate,
                    'e': self.enroll,
                    'd': self.remove_all_users,
                    'q': self.exit_app}
        cmd_exec.get(event.char, lambda: None)()

    # -------------------- Resize --------------------
    def resize(self, event):
        if event.widget == self.canvas:
            self.canvas.configure(width=event.width, height=event.height)
            self.reset_canvas = True
            if self.resize_handle is not None:
                self.after_cancel(self.resize_handle)
            self.resize_handle = self.after(100, self.canvas.update_idletasks)

    # -------------------- Status reset --------------------
    def reset_later(self):
        if self.reset_handle is not None:
            self.after_cancel(self.reset_handle)
        self.reset_handle = self.after(3 * 1000, self.controller_reset)

    def controller_reset(self):
        self.controller.reset()
        self.reset_canvas = True

    # -------------------- Buttons --------------------
    def authenticate(self):
        self.controller.reset()
        # IMPORTANT: no card_id here -> match against ALL users
        self.controller.authenticate_user(card_id=None)
        self.reset_later()

    def remove_all_users(self):
        self.controller.reset()
        result = messagebox.askyesno("Remove all users",
                                     "Are you sure you want to remove all users?",
                                     parent=self)
        if result:
            self.controller.remove_all_users()
            self.reset_later()

    def enroll(self):
        self.controller.reset()
        dialog = EnrollDialog(self)
        self.wait_window(dialog)

        if dialog.result:
            user_data = dialog.result
            self.controller.enroll_user(user_data['id'], user_data['name'], user_data['permission_level'])
            self.reset_later()

    def delete_user(self):
        self.controller.reset()
        user_id = simpledialog.askstring("Delete User", "Enter user ID to delete:", parent=self)

        if user_id:
            user_info = self.controller.user_db.get_user(user_id)
            if user_info:
                result = messagebox.askyesno("Delete User",
                                             f"Are you sure you want to delete user:\n\n"
                                             f"Name: {user_info['name']}\n"
                                             f"ID: {user_id}\n"
                                             f"Permission: {user_info['permission_level']}?",
                                             parent=self)
                if result:
                    if self.controller.remove_user(user_id):
                        messagebox.showinfo("Success", f"User {user_id} deleted successfully.", parent=self)
                    else:
                        messagebox.showerror("Error", f"User {user_id} not found on device.", parent=self)
            else:
                messagebox.showerror("Error", f"User {user_id} not found in database.", parent=self)
            self.reset_later()

    def show_all_users(self):
        users_window = tk.Toplevel(self)
        users_window.title("All Users")
        users_window.geometry("600x400")
        users_window.resizable(True, True)
        users_window.transient(self)

        frame = ttk.Frame(users_window, padding="10")
        frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        users_window.columnconfigure(0, weight=1)
        users_window.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        title_label = ttk.Label(frame, text="Registered Users", font=('Helvetica', 14, 'bold'))
        title_label.grid(row=0, column=0, pady=(0, 10))

        columns = ('ID', 'Name', 'Permission Level', 'Status')
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=15)

        tree.heading('ID', text='User ID')
        tree.heading('Name', text='Name')
        tree.heading('Permission Level', text='Permission Level')
        tree.heading('Status', text='Status')

        tree.column('ID', width=120, minwidth=100)
        tree.column('Name', width=180, minwidth=150)
        tree.column('Permission Level', width=150, minwidth=120)
        tree.column('Status', width=100, minwidth=80)

        db_users = self.controller.user_db.get_all_users()

        try:
            device_users = self.controller.query_users()
        except Exception:
            device_users = []

        for user_id, user_info in db_users.items():
            status = "Active" if user_id in device_users else "DB Only"
            tree.insert('', 'end', values=(user_id, user_info['name'], user_info['permission_level'], status))

        for user_id in device_users:
            if user_id not in db_users:
                tree.insert('', 'end', values=(user_id, "Unknown", "Unknown", "Device Only"))

        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        vsb.grid(row=1, column=1, sticky=(tk.N, tk.S))
        hsb.grid(row=2, column=0, sticky=(tk.W, tk.E))

        total_users = len(db_users) + len([u for u in device_users if u not in db_users])
        summary_label = ttk.Label(frame, text=f"Total users: {total_users}")
        summary_label.grid(row=3, column=0, pady=(10, 0))

        close_button = ttk.Button(frame, text="Close", command=users_window.destroy)
        close_button.grid(row=4, column=0, pady=(10, 0))

        users_window.focus_set()

    # -------------------- Snapshot clear --------------------
    def clear_snapshot(self):
        if self.canvas_snapshot_image_id is not None:
            self.canvas.itemconfig(self.canvas_snapshot_image_id, image=None)
            self.canvas.itemconfig(self.canvas_snapshot_image_id, state='hidden')
        self.snapshot_handle = None

    # -------------------- Video update --------------------
    def update_video(self):
        self.update_idletasks()

        if not self.controller.image_q.empty() and self.controller.running:
            array2d = None
            while not self.controller.image_q.empty():
                array2d = self.controller.image_q.get()
            try:
                self.image = Image.fromarray(array2d, mode="RGB")
            except PIL.UnidentifiedImageError:
                print("Preview Error: UnidentifiedImageError")

        self.canvas.update_idletasks()
        canvas_h = self.canvas.winfo_reqheight()
        canvas_w = self.canvas.winfo_reqwidth()

        if self.image is not None:
            image = self.image.copy()

            # Render faces
            for f in self.controller.detected_faces:
                self.render_face_rect(f, image)

            # Fill the canvas (wide look) instead of keeping full frame with borders
            scaled_image = ImageOps.fit(
                image,
                size=(canvas_w, canvas_h),
                method=Image.BICUBIC,
                centering=(0.5, 0.5)
            ).transpose(Image.Transpose.FLIP_LEFT_RIGHT)

            self.scaled_image = ImageTk.PhotoImage(image=scaled_image)

            if self.reset_canvas:
                self.canvas.delete("all")
                self.canvas_image_id = self.canvas.create_image(int(self.scaled_image.width() / 2),
                                                                int(self.scaled_image.height() / 2),
                                                                anchor=tk.CENTER, image=None)
                self.canvas_text_bg_id = self.canvas.create_rectangle(0, canvas_h - 50, canvas_w, canvas_h,
                                                                      fill='black', stipple='gray50')
                self.canvas_text_id = self.canvas.create_text(canvas_w / 2, canvas_h - 30, text='',
                                                              font='Helvetica 18 bold')
                self.canvas_snapshot_image_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=None)
                self.canvas.itemconfig(self.canvas_snapshot_image_id, state='hidden')
                self.reset_canvas = False

            self.canvas.itemconfig(self.canvas_image_id, image=self.scaled_image)
            self.canvas.moveto(self.canvas_image_id, int((canvas_w - self.scaled_image.width()) / 2),
                               int((canvas_h - self.scaled_image.height()) / 2))

            # Render message
            msg = self.controller.status_msg.replace('Status.', ' ')
            if msg != '':
                color = self.color_from_msg(self.controller.status_msg)
                self.canvas.itemconfig(self.canvas_text_bg_id, state='normal')
                self.canvas.itemconfig(self.canvas_text_id, state='normal', text=msg, fill=color)
            else:
                self.canvas.itemconfig(self.canvas_text_bg_id, state='hidden')
                self.canvas.itemconfig(self.canvas_text_id, state='hidden')

            # Render snapshot
            new_snapshot = None
            while not self.controller.snapshot_q.empty():
                new_snapshot = self.controller.snapshot_q.get()

            if new_snapshot is not None and self.canvas_snapshot_image_id is not None:
                snap = ImageOps.contain(new_snapshot, size=(int(canvas_w / 4), int(canvas_h / 4))).transpose(
                    Image.Transpose.FLIP_LEFT_RIGHT)

                self.snapshot_image = ImageTk.PhotoImage(image=snap)

                if self.snapshot_handle is not None:
                    self.after_cancel(self.snapshot_handle)
                self.snapshot_handle = self.after(5000, self.clear_snapshot)
                self.canvas.itemconfig(self.canvas_snapshot_image_id, image=self.snapshot_image)
                self.canvas.itemconfig(self.canvas_snapshot_image_id, state='normal')

        self.update_idletasks()

        if self.video_update_handle is not None:
            self.after_cancel(self.video_update_handle)
        if self.controller.running:
            self.video_update_handle = self.after(15, self.update_video)

    # -------------------- Helpers --------------------
    @staticmethod
    def render_face_rect(face, image):
        img1 = ImageDraw.Draw(image)
        f = face['face']

        success = face.get('success')
        if success is None:
            color = 'yellow'
        else:
            color = 'green' if success else 'blue'

        shape = [(f.x, f.y), (f.x + f.w, f.y + f.h)]
        img1.rectangle(shape, width=8, outline=color)

    @staticmethod
    def color_from_msg(msg):
        if 'Success' in msg:
            return 'lime green'
        if 'Forbidden' in msg or 'Fail' in msg or 'NoFace' in msg:
            return 'RoyalBlue1'
        return 'gray80'

    def exit_app(self):
        self.controller.exit_thread()
        self.quit()


# ============================================================
# Main
# ============================================================
def main():
    arg_parser = argparse.ArgumentParser(prog='viewer', add_help=False)
    options = arg_parser.add_argument_group('Options')
    options.add_argument('-h', '--help', action='help', default=argparse.SUPPRESS,
                         help='Show this help message and exit.')
    options.add_argument('-p', '--port', help='Device port. Will detect first device port if not specified.', type=str)
    options.add_argument('-c', '--camera', help='Camera number. -1 for autodetect.', type=int, default=-1)

    group = arg_parser.add_mutually_exclusive_group(required=False)
    group.add_argument('-d', '--dump', help='Dump mode.', action='store_true')
    group.add_argument('-r', '--crop', help='Cropped Face mode.', action='store_true')

    args = arg_parser.parse_args()

    # Port defaults
    if platform.system() == "Windows":
        port = "COM14"
    else:
        port = "/dev/ttyACM0"

    camera_index = args.camera

    # Discover device/port
    if args.port is None:
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            print('Error: No rsid devices were found and no port was specified.')
            sys.exit(1)
        port = devices[0]
    else:
        port = args.port

    device_type = rsid_py.discover_device_type(port)

    print(f'Using self.port: {port} ({device_type})')
    print(f'Using CAMERA_INDEX: {camera_index}')

    if args.dump:
        print("-" * 60)
        print('NOTE: Running in DUMP mode.')
        print('      While in dump mode, you need to use a separate rsid-client to initiate authentication for the')
        print('      RAW image to appear on this viewer.')
        print("-" * 60)

    # Configure dump/crop mode in device
    config = None
    with rsid_py.FaceAuthenticator(device_type, str(port)) as f:
        try:
            config = copy.copy(f.query_device_config())
            if args.dump:
                config.dump_mode = rsid_py.DumpMode.FullFrame
                f.set_device_config(config)
            elif args.crop:
                config.dump_mode = rsid_py.DumpMode.CroppedFace
                f.set_device_config(config)
            else:
                config.dump_mode = rsid_py.DumpMode.Disable
                f.set_device_config(config)
        except Exception:
            print("Exception while configuring device")
            print("-" * 60)
            traceback.print_exc(file=sys.stdout)
            print("-" * 60)
            os._exit(1)
        finally:
            try:
                f.disconnect()
            except Exception:
                pass

    gui = None

    def signal_handler(sig, frame):
        if gui is not None:
            gui.exit_app()

    signal.signal(signal.SIGINT, signal_handler)

    controller = Controller(port=port, camera_index=camera_index, device_type=device_type, dump_mode=config.dump_mode)
    controller.daemon = True

    if USE_CARD_READER:
        initialize_card_reader()

    controller.start()
    gui = GUI(controller)
    gui.mainloop()

    if USE_CARD_READER:
        try:
            disconnect_card_reader()
        except Exception:
            pass


if __name__ == '__main__':
    if sys.platform.startswith('win'):
        app_id = 'intel.realsenseid.viewer.1.0'
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    main()
