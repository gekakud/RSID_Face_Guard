#!/usr/bin/env python3

"""
PyQt5 GUI for RealSense ID Host Mode with camera preview and authentication
Based on host_mode_cli.py (business logic) and viewer_host_mode_with_db.py (camera preview)
"""

import argparse
import copy
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
RUN_ON_REAL_DEVICE = False

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

# Import rsid_py BEFORE PyQt5 to avoid native library conflicts
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
    from PIL import Image, ImageDraw
except ImportError:
    print('Failed importing PIL. Please install Pillow (pip install Pillow).')
    sys.exit(1)

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QFrame, QInputDialog, QMessageBox
    )
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
    from PyQt5.QtGui import QImage, QPixmap, QFont
except ImportError:
    print('Failed importing PyQt5. Please install it (pip install PyQt5).')
    sys.exit(1)

from user_db import UserDatabase


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


class AuthSignals(QObject):
    """Signals for thread-safe GUI updates from authentication"""
    status_update = pyqtSignal(str)
    auth_complete = pyqtSignal(bool, str)


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
        self.detected_faces = []
        
    def on_image(self, image):
        """Callback for preview frames"""
        if not self.running:
            return
        try:
            buffer = memoryview(image.get_buffer())
            arr = np.asarray(buffer, dtype=np.uint8)
            array2d = arr.reshape((image.height, image.width, -1))
            # Drop old frames if queue is full
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
    """Business logic for host mode authentication (from host_mode_cli.py)"""
    
    def __init__(self, port: str, signals: AuthSignals):
        self.port = port
        self.signals = signals
        self.user_db = UserDatabase()
        
        # Initialize Wiegand transmitter (card reader is initialized in main())
        try:
            initialize_wiegand_tx()
            print("Wiegand transmitter initialized")
        except Exception as e:
            print(f"Wiegand initialization failed: {e}")
    
    def authenticate_with_card(self, card_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate user with card ID and face matching"""
        self.signals.status_update.emit(f"Authenticating card ID: {card_id}...")
        
        # Check if card ID exists in database
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            self.signals.status_update.emit(f"Card ID {card_id} not found in database")
            return False, None, "Card not registered"
        
        # Extract faceprints from camera
        auth_status = None
        extracted_prints = None
        
        def on_fp_auth_result(status, new_prints):
            nonlocal auth_status, extracted_prints
            auth_status = status
            extracted_prints = new_prints
        
        try:
            with rsid_py.FaceAuthenticator(self.port) as authenticator:
                self.signals.status_update.emit("Extracting faceprints...")
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    msg = f"Face extraction failed: {auth_status}"
                    self.signals.status_update.emit(msg)
                    return False, None, msg
                
                # Perform host-side matching
                fp = user_info.get('faceprints')
                if not fp:
                    msg = f"No faceprints stored for user {user_info['name']}"
                    self.signals.status_update.emit(msg)
                    return False, None, "No faceprints on file"
                
                # Reconstruct faceprints object
                db_faceprints = rsid_py.Faceprints()
                db_faceprints.version = fp['version']
                db_faceprints.features_type = fp['features_type']
                db_faceprints.flags = fp['flags']
                db_faceprints.adaptive_descriptor_nomask = fp['adaptive_descriptor_nomask']
                db_faceprints.adaptive_descriptor_withmask = fp['adaptive_descriptor_withmask']
                db_faceprints.enroll_descriptor = fp['enroll_descriptor']
                
                # Match faceprints
                updated_faceprints = rsid_py.Faceprints()
                match_result = authenticator.match_faceprints(
                    extracted_prints, db_faceprints, updated_faceprints
                )
                
                if match_result.success or (match_result.score is not None and match_result.score >= CUSTOM_THRESHOLD):
                    send_w32(card_id)
                    return True, user_info['name'], user_info['permission_level']
                else:
                    return False, None, f"Face match failed (score: {match_result.score})"
                    
        except Exception as e:
            msg = f"Authentication error: {e}"
            self.signals.status_update.emit(msg)
            return False, None, str(e)
    
    def authenticate_all_users(self) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate by matching against all users in database"""
        self.signals.status_update.emit("Authenticating...")
        
        all_users = self.user_db.get_all_users()
        if not all_users:
            self.signals.status_update.emit("No users in database")
            return False, None, "No users in database"
        
        # Extract faceprints from camera
        auth_status = None
        extracted_prints = None
        
        def on_fp_auth_result(status, new_prints):
            nonlocal auth_status, extracted_prints
            auth_status = status
            extracted_prints = new_prints
        
        try:
            with rsid_py.FaceAuthenticator(self.port) as authenticator:
                self.signals.status_update.emit("Extracting faceprints...")
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    msg = f"Face extraction failed: {auth_status}"
                    self.signals.status_update.emit(msg)
                    return False, None, msg
                
                # Match against all users
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
            msg = f"Authentication error: {e}"
            self.signals.status_update.emit(msg)
            return False, None, str(e)
    
    def cleanup(self):
        """Cleanup resources"""
        try:
            disconnect_card_reader()
            close_wiegand_tx()
        except:
            pass


class MainWindow(QMainWindow):
    """Main PyQt5 GUI window"""
    
    def __init__(self, port: str, camera_index: int, device_type: rsid_py.DeviceType):
        super().__init__()
        self.port = port
        self.camera_index = camera_index
        self.device_type = device_type
        
        # Auth signals for thread-safe updates
        self.auth_signals = AuthSignals()
        self.auth_signals.status_update.connect(self.update_status)
        self.auth_signals.auth_complete.connect(self.on_auth_complete)
        
        # Initialize services
        self.preview_controller = PreviewController(port, camera_index, device_type)
        self.host_service = HostModeService(port, self.auth_signals)
        
        # Authentication state
        self.auth_in_progress = False
        
        self.init_ui()
        self.setup_video_timer()
        
        # Start preview thread
        self.preview_controller.start()
    
    def _place_on_correct_display(self):
        """Place window on the small display if connected (Linux only via xrandr)"""
        pos = _find_display_xy_by_resolution(SMALL_W, SMALL_H)
        if pos is not None:
            x, y = pos
            try:
                # Move to the small display
                self.move(x, y)
                print(f"GUI moved to small display at {x},{y}")
                # Go fullscreen
                self.showFullScreen()
            except Exception as e:
                print("Failed placing window:", e)
        else:
            # No small display found -> stay on primary (0,0)
            try:
                self.move(0, 0)
                print("Small display not found -> using primary screen")
            except Exception:
                pass
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("RealSense ID - Host Mode")
        
        # Small screen mode for RPi5 with 800x480 display
        if RUN_ON_REAL_DEVICE:
            self.resize(SMALL_W, SMALL_H)
            self.setCursor(Qt.BlankCursor)  # Hide cursor on small screen
            self._place_on_correct_display()
        else:
            self.setMinimumSize(600, 700)
            self.resize(720, 900)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        layout = QVBoxLayout(central_widget)
        if RUN_ON_REAL_DEVICE:
            layout.setSpacing(5)
            layout.setContentsMargins(5, 5, 5, 5)
        else:
            layout.setSpacing(10)
            layout.setContentsMargins(10, 10, 10, 10)
        
        # Video preview frame (with overlay for result indicator)
        video_frame = QFrame()
        video_frame.setFrameStyle(QFrame.Box | QFrame.Sunken)
        video_frame.setStyleSheet("background-color: black;")
        video_layout = QVBoxLayout(video_frame)
        video_layout.setContentsMargins(0, 0, 0, 0)
        
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        if RUN_ON_REAL_DEVICE:
            self.video_label.setMinimumSize(SMALL_W - 20, SMALL_H - 80)
        else:
            self.video_label.setMinimumSize(640, 480)
        self.video_label.setStyleSheet("background-color: black;")
        video_layout.addWidget(self.video_label)
        
        layout.addWidget(video_frame, stretch=1)
        
        # Result overlay label (big ✓ or ✗)
        self.result_label = QLabel(self)
        self.result_label.setAlignment(Qt.AlignCenter)
        if RUN_ON_REAL_DEVICE:
            self.result_label.setFont(QFont("Arial", 150, QFont.Bold))
        else:
            self.result_label.setFont(QFont("Arial", 200, QFont.Bold))
        self.result_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 180);
                border-radius: 20px;
            }
        """)
        self.result_label.hide()
        
        # Timer to hide result label
        self.result_timer = QTimer()
        self.result_timer.setSingleShot(True)
        self.result_timer.timeout.connect(self.hide_result)
        
        # Button layout
        button_layout = QHBoxLayout()
        
        self.auth_button = QPushButton("Authenticate")
        if RUN_ON_REAL_DEVICE:
            self.auth_button.setFont(QFont("Arial", 24, QFont.Bold))
            self.auth_button.setMinimumHeight(100)
        else:
            self.auth_button.setFont(QFont("Arial", 32, QFont.Bold))
            self.auth_button.setMinimumHeight(120)
        self.auth_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 15px;
                padding: 30px 60px;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
            QPushButton:disabled {
                background-color: #666;
            }
        """)
        self.auth_button.clicked.connect(self.on_authenticate_clicked)
        button_layout.addWidget(self.auth_button)
        
        layout.addLayout(button_layout)
    
    def setup_video_timer(self):
        """Setup timer for video frame updates"""
        self.video_timer = QTimer()
        self.video_timer.timeout.connect(self.update_video)
        self.video_timer.start(30)  # ~33 FPS
    
    def update_video(self):
        """Update video display from preview queue"""
        if self.preview_controller.image_queue.empty():
            return
        
        # Get latest frame
        frame = None
        while not self.preview_controller.image_queue.empty():
            try:
                frame = self.preview_controller.image_queue.get_nowait()
            except queue.Empty:
                break
        
        if frame is None:
            return
        
        try:
            # Convert numpy array to QImage
            height, width, channels = frame.shape
            bytes_per_line = channels * width
            
            # Create PIL Image for flipping
            pil_image = Image.fromarray(frame, mode="RGB")
            pil_image = pil_image.transpose(Image.FLIP_LEFT_RIGHT)
            
            # Convert back to numpy
            frame = np.array(pil_image)
            
            # Create QImage
            q_image = QImage(frame.data, width, height, bytes_per_line, QImage.Format_RGB888)
            
            # Scale to fit label while maintaining aspect ratio
            pixmap = QPixmap.fromImage(q_image)
            scaled_pixmap = pixmap.scaled(
                self.video_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            
            self.video_label.setPixmap(scaled_pixmap)
            
        except Exception as e:
            print(f"Video update error: {e}")
    
    def update_status(self, message: str):
        """Update status - no-op, kept for compatibility"""
        pass
    
    def show_result(self, success: bool):
        """Show big ✓ or ✗ overlay for 3 seconds"""
        if success:
            self.result_label.setText("✓")
            self.result_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(0, 0, 0, 180);
                    color: #4CAF50;
                    border-radius: 20px;
                }
            """)
        else:
            self.result_label.setText("✗")
            self.result_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(0, 0, 0, 180);
                    color: #F44336;
                    border-radius: 20px;
                }
            """)
        
        # Center the result label on the window
        label_size = 300 if RUN_ON_REAL_DEVICE else 400
        self.result_label.setFixedSize(label_size, label_size)
        x = (self.width() - label_size) // 2
        y = (self.height() - label_size) // 2
        self.result_label.move(x, y)
        self.result_label.show()
        self.result_label.raise_()
        
        # Start timer to hide after 3 seconds
        self.result_timer.start(3000)
    
    def hide_result(self):
        """Hide the result overlay"""
        self.result_label.hide()
    
    def on_auth_complete(self, success: bool, message: str):
        """Handle authentication completion"""
        self.auth_in_progress = False
        self.auth_button.setEnabled(True)
        self.show_result(success)
        print(message)  # Log to console
    
    def on_authenticate_clicked(self):
        """Handle authenticate button click"""
        if self.auth_in_progress:
            return
        
        self.auth_in_progress = True
        self.auth_button.setEnabled(False)
        
        # Run authentication in separate thread
        auth_thread = threading.Thread(target=self._run_authentication, daemon=True)
        auth_thread.start()
    
    def _run_authentication(self):
        """Run authentication (called in separate thread)"""
        try:
            success, name, permission = self.host_service.authenticate_all_users()
            
            if success:
                message = f"✅ Access granted: {name} ({permission})"
            else:
                message = f"❌ Access denied: {permission}"
            
            self.auth_signals.auth_complete.emit(success, message)
            
        except Exception as e:
            self.auth_signals.auth_complete.emit(False, f"Error: {e}")
    
    def closeEvent(self, event):
        """Handle window close"""
        self.video_timer.stop()
        self.preview_controller.stop()
        self.host_service.cleanup()
        event.accept()


def main():
    parser = argparse.ArgumentParser(prog='host_mode_gui', description='RealSense ID Host Mode GUI')
    parser.add_argument('-p', '--port', help='Device port', type=str, default=None)
    parser.add_argument('-c', '--camera', help='Camera number (-1 for autodetect)', type=int, default=-1)
    args = parser.parse_args()
    
    # Determine port (same logic as viewer_host_mode_with_db.py)
    if args.port is None:
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            # Try default port based on OS
            if platform.system() == "Windows":
                port = "COM9"
            else:
                port = "/dev/ttyACM0"
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
        print("\nTroubleshooting tips:")
        print("  - Make sure the RealSense ID device is connected")
        print("  - Check that no other application is using the port")
        print("  - Try unplugging and replugging the device")
        print("  - Run without debugger breakpoints (native library timing issue)")
        traceback.print_exc()
        sys.exit(1)
    
    # Configure device (same pattern as viewer_host_mode_with_db.py)
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
    
    # Initialize card reader before starting controller (same as viewer)
    initialize_card_reader()
    print("Card reader initialized")
    
    # Create Qt application
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # Create and show main window
    window = MainWindow(port, camera_index, device_type)
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
