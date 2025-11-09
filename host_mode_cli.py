#!/usr/bin/env python3

"""
Command-line host mode service for RealSense ID with card authentication only
Suitable for running as a Linux service with card reader integration
"""

import argparse
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
from typing import Optional, Dict, Any

# Card reader support
try:
    from card_reader_api import initialize_card_reader, get_card_id, disconnect_card_reader
    CARD_READER_SUPPORT = True
except ImportError:
    print('Card reader module not available. Card authentication disabled.')
    CARD_READER_SUPPORT = False
    sys.exit(1)  # Exit if card reader is not available since it's required

# Card writer support
try:
    from card_writer_api import initialize_wiegand_tx, send_w32, close_wiegand_tx
    CARD_WRITER_SUPPORT = True
except ImportError:
    print('Card writer module not available. Card authentication disabled.')
    CARD_WRITER_SUPPORT = False
    sys.exit(1)  # Exit if card writer is not available since it's required

# LED control support
try:
    from led_control import LEDController
    LED_SUPPORT = True
except ImportError:
    print('LED control module not available. LED feedback disabled.')
    LED_SUPPORT = False

try:
    from card_reader_led_control_test import CardReaderLEDAPI
    CARD_LED_SUPPORT = True
except ImportError:
    print('Card reader LED control module not available. LED feedback disabled.')
    CARD_LED_SUPPORT = False

try:
    import rsid_py
except ImportError:
    print('Failed importing rsid_py. Please ensure rsid_py module is available.')
    exit(1)


class UserDatabase:
    """Manage user database in JSON file (read-only)"""
    
    def __init__(self, filename: str = 'user_database.json'):
        self.filename = filename
        self.users = self.load_users()
        self.lock = threading.Lock()
    
    def load_users(self) -> Dict[str, Dict[str, Any]]:
        """Load users from JSON file"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    users = json.load(f)
                    print(f"Loaded {len(users)} users from database")
                    return users
            except Exception as e:
                print(f"Error loading user database: {e}")
                return {}
        print("No user database found - cannot authenticate without users")
        return {}
    
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user details by ID"""
        with self.lock:
            return self.users.get(user_id, None)
    
    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """Get all users"""
        with self.lock:
            return self.users.copy()


class HostModeService:
    """Host mode service for RealSense ID with card authentication only"""
    
    def __init__(self, port: str, device_type: rsid_py.DeviceType):
        self.port = port
        self.device_type = device_type
        self.running = True
        self.user_db = UserDatabase()
        
        # Check if we have users in database
        if not self.user_db.get_all_users():
            print("No users in database. Please enroll users using a different tool.")
            sys.exit(1)
        
        # Initialize LED controller if available
        self.led_controller = None
        if LED_SUPPORT:
            try:
                self.led_controller = LEDController()
                print("LED feedback initialized")
            except Exception as e:
                print(f"LED controller init failed: {e}")
        self.card_led_controller = None

        if CARD_LED_SUPPORT:
            try:
                self.card_led_controller = CardReaderLEDAPI()
                print("Card reader LED feedback initialized")
            except Exception as e:
                print(f"Card reader LED controller init failed: {e}")

        # Initialize card reader (required)
        try:
            initialize_card_reader()
            print("Card reader initialized")
            initialize_wiegand_tx()
            print("Wiegand transmitter initialized")
        except Exception as e:
            print(f"Wiegand initialization failed: {e}")
            sys.exit(1)
    
    def authenticate_with_card(self, card_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate user with card ID and face matching"""
        print(f"Authentication attempt with card ID: {card_id}")
        
        # Check if card ID exists in database
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            print(f"Card ID {card_id} not found in database")
            if self.led_controller:
                self.led_controller.flash_red(3)

            if self.card_led_controller:
                self.card_led_controller.led_red_on(3)

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
                print("Extracting faceprints for authentication...")
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    print(f"Face extraction failed: {auth_status}")
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    if self.card_led_controller:
                        self.card_led_controller.led_red_on(3)

                    return False, None, f"Face extraction failed: {auth_status}"
                
                # Perform host-side matching
                fp = user_info.get('faceprints')
                if not fp:
                    print(f"No faceprints stored for user {user_info['name']}")
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    if self.card_led_controller:
                        self.card_led_controller.led_red_on(3)

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
                
                if match_result.success:
                    print(f"Authentication successful for {user_info['name']} (score: {match_result.score})")
                    
                    send_w32(card_id)  # Send card ID via Wiegand

                    if self.led_controller:
                        self.led_controller.flash_green(3)
                        
                    if self.card_led_controller:
                        self.card_led_controller.led_green_on(3)
                    
                    return True, user_info['name'], user_info['permission_level']
                else:
                    print(f"Face match failed for card ID {card_id}")
                    
                    if self.led_controller:
                        self.led_controller.flash_red(3)

                    if self.card_led_controller:
                        self.card_led_controller.led_red_on(3)
                    
                    return False, None, "Face match failed"
                    
        except Exception as e:
            print(f"Authentication error: {e}")
            if self.led_controller:
                self.led_controller.flash_red(3)
            if self.card_led_controller:
                self.card_led_controller.led_red_on(3)
            return False, None, str(e)
    
    def run_service(self):
        """Main service loop"""
        print("Host Mode Service started (Card Authentication Only)")
        print(f"Port: {self.port}, Device Type: {self.device_type}")
        print(f"Total users in database: {len(self.user_db.get_all_users())}")
        
        # Start card reader monitoring thread
        card_thread = threading.Thread(target=self._card_reader_loop, daemon=True)
        card_thread.start()
        print("Card reader monitoring started")
        
        # Main service loop
        try:
            while self.running:
                time.sleep(1)
                # Main loop just keeps the service alive
        
        except KeyboardInterrupt:
            print("Service interrupted by user")
        
        finally:
            self.cleanup()
    
    def _card_reader_loop(self):
        """Monitor card reader for authentication requests"""
        print("Card reader monitoring active")
        last_card_id = None
        card_cooldown = 2.0  # seconds before same card can be read again
        last_read_time = 0
        
        while self.running:
            try:
                card_id = get_card_id(timeout=0.5)
                
                if card_id is not None:
                    current_time = time.time()
                    
                    # Check if it's the same card within cooldown period
                    if card_id == last_card_id and (current_time - last_read_time) < card_cooldown:
                        continue
                    
                    print(f"Card detected: {card_id}")
                    success, user_name, permission = self.authenticate_with_card(card_id)
                    
                    if success:
                        print(f"✅ Access granted to {user_name} ({permission})")
                    else:
                        print(f"❌ Access denied for card {card_id}: {permission}")
                    
                    last_card_id = card_id
                    last_read_time = current_time
                    
            except Exception as e:
                print(f"Card reader error: {e}")
                time.sleep(1)
    
    def cleanup(self):
        """Cleanup resources"""
        self.running = False
        print("Cleaning up resources...")
        
        if self.led_controller:
            self.led_controller.cleanup()
            print("LED controller cleaned up")
        if self.card_led_controller:
            self.card_led_controller.close()
            print("Card reader LED controller cleaned up")
            
        try:
            disconnect_card_reader()
            print("Card reader disconnected")
            close_wiegand_tx()
            print("Wiegand transmitter closed")
        except:
            pass
        
        print("Service stopped")
    
    def stop(self):
        """Stop the service"""
        print("Stopping service...")
        self.running = False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='host_mode_cli',
        description='RealSense ID Host Mode Service - Card Authentication Only'
    )
    
    parser.add_argument(
        '-p', '--port',
        help='Device port. Will auto-detect if not specified.',
        type=str,
        default=None
    )
    
    parser.add_argument(
        '-l', '--log-file',
        help='Log file path',
        type=str,
        default='host_mode_service.log'
    )
    
    parser.add_argument(
        '--debug',
        help='Enable debug logging',
        action='store_true'
    )
    
    args = parser.parse_args()
    
    # Check if card reader is available
    if not CARD_READER_SUPPORT:
        print("Card reader module is required but not available. Exiting.")
        sys.exit(1)
    if not CARD_WRITER_SUPPORT:
        print("Card writer module is required but not available. Exiting.")
        sys.exit(1)

    # Determine port
    if args.port:
        port = args.port
    else:
        # Auto-detect device
        devices = rsid_py.discover_devices()
        if len(devices) == 0:
            # Try default ports based on OS
            if platform.system() == "Windows":
                port = "COM14"
                print(f"No devices auto-detected. Trying default port: {port}")
            else:
                port = "/dev/ttyACM0"
                print(f"No devices auto-detected. Trying default port: {port}")
        else:
            port = devices[0]
            print(f"Auto-detected device on port: {port}")
    
    # Discover device type
    try:
        device_type = rsid_py.discover_device_type(port)
        print(f"Device type: {device_type}")
    except Exception as e:
        print(f"Could not connect to device on port {port}: {e}")
        print("\nPlease check:")
        print("  - Device is connected")
        print("  - Port is correct (use -p to specify)")
        print("  - You have necessary permissions")
        exit(1)
    
    # Create service
    service = HostModeService(port, device_type)
    
    # Signal handler for clean exit
    def signal_handler(sig, frame):
        print("Signal received, shutting down...")
        service.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run service
    print("Starting service mode...")
    service.run_service()


if __name__ == '__main__':
    main()
