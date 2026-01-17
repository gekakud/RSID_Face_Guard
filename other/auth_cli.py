#!/usr/bin/env python3

"""
Command-line authentication tool for RealSense ID
Press Space to authenticate, 'q' to quit
"""

import argparse
import platform
import signal
import sys
import threading
import time
from typing import Optional
from other.button_listener import ButtonListener


try:
    import rsid_py
except ImportError:
    print('Failed importing rsid_py. Please ensure rsid_py module is available.')
    exit(1)

# Import LED control
try:
    from other.led_control import LEDController
    LED_SUPPORT = True
except ImportError:
    print('LED control module not available. LED feedback disabled.')
    LED_SUPPORT = False

# Check if we're on Windows for keyboard input
if sys.platform.startswith('win'):
    import msvcrt
else:
    import termios
    import tty


class AuthenticatorCLI:
    """Command-line authenticator for RealSense ID"""
    
    def __init__(self, port: str, device_type: rsid_py.DeviceType):
        self.port = port
        self.device_type = device_type
        self.status_msg = ''
        self.running = True
        self.auth_in_progress = False
        
        # Initialize LED controller if available
        self.led_controller = None
        if LED_SUPPORT:
            try:
                self.led_controller = LEDController()
                print("💡 LED feedback enabled")
            except Exception as e:
                print(f"⚠ LED controller init failed: {e}")
                self.led_controller = None
        
    def on_result(self, result: rsid_py.AuthenticateStatus, user_id: Optional[str] = None):
        """Callback for authentication result"""
        success = result == rsid_py.AuthenticateStatus.Success
        
        print("\n" + "="*60)
        if success and user_id:
            print(f"✅ AUTHENTICATION SUCCESS")
            print(f"   User ID: {user_id}")
            # Flash green LED for success
            if self.led_controller:
                self.led_controller.flash_green(3)
        elif success:
            print(f"✅ AUTHENTICATION SUCCESS")
            # Flash green LED for success
            if self.led_controller:
                self.led_controller.flash_green(3)
        else:
            print(f"❌ AUTHENTICATION FAILED")
            print(f"   Status: {result}")
            # Flash red LED for failure
            if self.led_controller:
                self.led_controller.flash_red(3)
        print("="*60)
        self.auth_in_progress = False
        
    def on_hint(self, hint):
        """Callback for authentication hints"""
        # Convert hint to string and display user-friendly messages
        hint_str = str(hint)
        
        if "NoFaceDetected" in hint_str:
            print("   ⚠ No face detected - please position your face in front of the camera")
        elif "FaceDetected" in hint_str:
            print("   👤 Face detected - authenticating...")
        elif "MaskDetected" in hint_str:
            print("   ⚠ Mask detected - please remove mask")
        elif "LookingAway" in hint_str:
            print("   ⚠ Please look at the camera")
        elif "Spoof" in hint_str:
            print("   ⚠ Spoof attempt detected")
        else:
            print(f"   ℹ {hint_str}")
            
    def on_faces(self, faces, timestamp):
        """Callback for face detection"""
        if len(faces) > 0:
            print(f"   👥 Detected {len(faces)} face(s)")
        
    def authenticate(self):
        """Perform authentication"""
        if self.auth_in_progress:
            print("\n⚠ Authentication already in progress...")
            return
            
        self.auth_in_progress = True
        print("\n🔐 Starting authentication...")
        
        try:
            with rsid_py.FaceAuthenticator(self.device_type, self.port) as f:
                f.authenticate(
                    on_hint=self.on_hint,
                    on_result=self.on_result,
                    on_faces=self.on_faces
                )
        except Exception as e:
            print(f"\n❌ Authentication error: {e}")
            self.auth_in_progress = False
            
    def display_info(self):
        """Display device information"""
        print("\n" + "="*60)
        print("  RealSense ID Authentication Tool")
        print("="*60)
        print(f"📍 Port: {self.port}")
        print(f"🔧 Device Type: {self.device_type}")
        print(f"📦 Version: {rsid_py.__version__}")
        
        # Query device info
        try:
            with rsid_py.FaceAuthenticator(self.device_type, self.port) as f:
                users = f.query_user_ids()
                print(f"👥 Enrolled Users: {len(users)}")
                if len(users) > 0:
                    print(f"   User IDs: {', '.join(users[:5])}", end="")
                    if len(users) > 5:
                        print(f" ... and {len(users) - 5} more")
                    else:
                        print()
        except Exception as e:
            print(f"⚠ Could not query device info: {e}")
            
        print("\n" + "-"*60)
        print("  Controls:")
        print("-"*60)
        print("  [Space] - Authenticate")
        print("  [i]     - Show device info")
        print("  [q]     - Quit")
        print("-"*60)
        print("\nReady. Press Space to authenticate...\n")


def get_keypress():
    """Get a single keypress - cross platform"""
    if sys.platform.startswith('win'):
        # Windows
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b' ':
                return ' '
            elif key == b'q':
                return 'q'
            elif key == b'i':
                return 'i'
            # Handle special keys (arrows, etc.)
            elif key in [b'\x00', b'\xe0']:
                msvcrt.getch()  # consume the second byte
    else:
        # Unix/Linux/macOS
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)
            return key
    return None


def setup_terminal():
    if not sys.stdin.isatty():
        # running under systemd or non-interactive -> skip raw TTY tweaks
        return
    """Setup terminal for raw input - Unix/Linux/macOS only"""
    
    if not sys.platform.startswith('win'):
        import termios, tty
        global old_settings
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        

def restore_terminal():
    """Restore terminal settings - Unix/Linux/macOS only"""
    if not sys.platform.startswith('win'):
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        prog='auth_cli',
        description='RealSense ID Command-line Authentication Tool'
    )
    
    parser.add_argument(
        '-p', '--port',
        help='Device port. Will auto-detect if not specified.',
        type=str,
        default=None
    )
    
    args = parser.parse_args()
    
    # Determine port
    if args.port:
        port = args.port
    else:
        # Auto-detect device
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            # Try default ports based on OS
            if platform.system() == "Windows":
                port = "COM9"
                print(f"⚠ No devices auto-detected. Trying default port: {port}")
            else:
                port = "/dev/ttyACM0"
                print(f"⚠ No devices auto-detected. Trying default port: {port}")
        else:
            port = devices[0]
            print(f"✅ Auto-detected device on port: {port}")
    
    # Discover device type
    try:
        device_type = rsid_py.discover_device_type(port)
    except Exception as e:
        print(f"❌ Error: Could not connect to device on port {port}")
        print(f"   {e}")
        print("\nPlease check:")
        print("  - Device is connected")
        print("  - Port is correct (use -p to specify)")
        print("  - You have necessary permissions")
        exit(1)
    
    # Create authenticator
    auth = AuthenticatorCLI(port, device_type)
    
    button_listener = ButtonListener(pin=16, callback=auth.authenticate)  # GPIO pin 16
    button_thread = threading.Thread(target=button_listener.start, daemon=True)
    button_thread.start()
    # Display initial info
    auth.display_info()
    
    # Setup terminal for raw input (Unix/Linux/macOS)
    if not sys.platform.startswith('win'):
        setup_terminal()
    
    # Signal handler for clean exit
    def signal_handler(sig, frame):
        print("\n\n👋 Exiting...")
        auth.running = False
        # Clean up LEDs
        if auth.led_controller:
            auth.led_controller.cleanup()
        if not sys.platform.startswith('win'):
            restore_terminal()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Main loop
    try:
        while auth.running:
            key = get_keypress()
            
            if key == ' ':
                auth.authenticate()
            elif key == 'i':
                auth.display_info()
            elif key == 'q':
                print("\n👋 Exiting...")
                # Clean up LEDs before exit
                if auth.led_controller:
                    auth.led_controller.cleanup()
                break
                
            time.sleep(0.05)  # Small delay to prevent CPU spinning
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        # Clean up LEDs
        if auth.led_controller:
            auth.led_controller.cleanup()
        if not sys.platform.startswith('win'):
            restore_terminal()
        print("Goodbye!")


if __name__ == '__main__':
    main()
