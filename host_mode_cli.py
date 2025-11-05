#!/usr/bin/env python3

"""
Command-line host mode service for RealSense ID with database support
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
from datetime import datetime

# Card reader support
try:
    from card_reader_api import initialize_card_reader, get_card_id, disconnect_card_reader
    CARD_READER_SUPPORT = True
except ImportError:
    print('Card reader module not available. Card authentication disabled.')
    CARD_READER_SUPPORT = False

# Button listener support
try:
    from button_listener import ButtonListener
    BUTTON_SUPPORT = True
except ImportError:
    print('Button listener module not available. Physical button disabled.')
    BUTTON_SUPPORT = False

# LED control support
try:
    from led_control import LEDController
    LED_SUPPORT = True
except ImportError:
    print('LED control module not available. LED feedback disabled.')
    LED_SUPPORT = False

try:
    import rsid_py
except ImportError:
    print('Failed importing rsid_py. Please ensure rsid_py module is available.')
    exit(1)


# Configure logging
def setup_logging(log_file: str = 'host_mode_service.log', debug: bool = False):
    """Setup logging configuration"""
    log_level = logging.DEBUG if debug else logging.INFO
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


class UserDatabase:
    """Manage user database in JSON file"""
    
    def __init__(self, filename: str = 'user_database.json', logger: logging.Logger = None):
        self.filename = filename
        self.logger = logger or logging.getLogger(__name__)
        self.users = self.load_users()
        self.lock = threading.Lock()
    
    def load_users(self) -> Dict[str, Dict[str, Any]]:
        """Load users from JSON file"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    users = json.load(f)
                    self.logger.info(f"Loaded {len(users)} users from database")
                    return users
            except Exception as e:
                self.logger.error(f"Error loading user database: {e}")
                return {}
        self.logger.info("No existing user database found, starting fresh")
        return {}
    
    def save_users(self) -> bool:
        """Save users to JSON file"""
        try:
            with self.lock:
                with open(self.filename, 'w') as f:
                    json.dump(self.users, f, indent=2)
                self.logger.debug("User database saved successfully")
                return True
        except Exception as e:
            self.logger.error(f"Error saving user database: {e}")
            return False
    
    def add_user(self, user_id: str, name: str, permission_level: str, faceprints: Optional[Dict] = None) -> bool:
        """Add a new user to the database"""
        with self.lock:
            self.users[user_id] = {
                'name': name,
                'id': user_id,
                'permission_level': permission_level,
                'faceprints': faceprints,
                'created_at': datetime.now().isoformat(),
                'last_seen': None
            }
            success = self.save_users()
            if success:
                self.logger.info(f"Added user: {name} (ID: {user_id}, Permission: {permission_level})")
            return success
    
    def update_last_seen(self, user_id: str):
        """Update last seen timestamp for a user"""
        with self.lock:
            if user_id in self.users:
                self.users[user_id]['last_seen'] = datetime.now().isoformat()
                self.save_users()
    
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user details by ID"""
        with self.lock:
            return self.users.get(user_id, None)
    
    def delete_user(self, user_id: str) -> bool:
        """Delete a user from the database"""
        with self.lock:
            if user_id in self.users:
                user_name = self.users[user_id]['name']
                del self.users[user_id]
                success = self.save_users()
                if success:
                    self.logger.info(f"Deleted user: {user_name} (ID: {user_id})")
                return success
            return False
    
    def clear_all(self) -> bool:
        """Clear all users from the database"""
        with self.lock:
            user_count = len(self.users)
            self.users = {}
            success = self.save_users()
            if success:
                self.logger.info(f"Cleared all {user_count} users from database")
            return success
    
    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """Get all users"""
        with self.lock:
            return self.users.copy()


class HostModeService:
    """Host mode service for RealSense ID"""
    
    def __init__(self, port: str, device_type: rsid_py.DeviceType, logger: logging.Logger = None):
        self.port = port
        self.device_type = device_type
        self.logger = logger or logging.getLogger(__name__)
        self.running = True
        self.user_db = UserDatabase(logger=self.logger)
        
        # Initialize LED controller if available
        self.led_controller = None
        if LED_SUPPORT:
            try:
                self.led_controller = LEDController()
                self.logger.info("LED feedback initialized")
            except Exception as e:
                self.logger.warning(f"LED controller init failed: {e}")
        
        # Initialize card reader if available
        self.card_reader_initialized = False
        if CARD_READER_SUPPORT:
            try:
                initialize_card_reader()
                self.card_reader_initialized = True
                self.logger.info("Card reader initialized")
            except Exception as e:
                self.logger.warning(f"Card reader init failed: {e}")
        
        # Statistics
        self.stats = {
            'auth_attempts': 0,
            'auth_success': 0,
            'auth_failed': 0,
            'enrollments': 0,
            'last_auth_time': None,
            'last_auth_user': None
        }
    
    def authenticate_with_card(self, card_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate user with card ID and face matching"""
        self.stats['auth_attempts'] += 1
        self.logger.info(f"Authentication attempt with card ID: {card_id}")
        
        # Check if card ID exists in database
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            self.logger.warning(f"Card ID {card_id} not found in database")
            self.stats['auth_failed'] += 1
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
                self.logger.debug("Extracting faceprints for authentication...")
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    self.logger.warning(f"Face extraction failed: {auth_status}")
                    self.stats['auth_failed'] += 1
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    return False, None, f"Face extraction failed: {auth_status}"
                
                # Perform host-side matching
                fp = user_info.get('faceprints')
                if not fp:
                    self.logger.error(f"No faceprints stored for user {user_info['name']}")
                    self.stats['auth_failed'] += 1
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
                    self.logger.info(f"Authentication successful for {user_info['name']} (score: {match_result.score})")
                    self.stats['auth_success'] += 1
                    self.stats['last_auth_time'] = datetime.now().isoformat()
                    self.stats['last_auth_user'] = user_info['name']
                    self.user_db.update_last_seen(str(card_id))
                    
                    if self.led_controller:
                        self.led_controller.flash_green(3)
                    
                    return True, user_info['name'], user_info['permission_level']
                else:
                    self.logger.warning(f"Face match failed for card ID {card_id}")
                    self.stats['auth_failed'] += 1
                    
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    
                    return False, None, "Face match failed"
                    
        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            self.stats['auth_failed'] += 1
            return False, None, str(e)
    
    def authenticate_without_card(self) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate by matching face against all users in database"""
        self.stats['auth_attempts'] += 1
        self.logger.info("Authentication attempt without card")
        
        auth_status = None
        extracted_prints = None
        
        def on_fp_auth_result(status, new_prints):
            nonlocal auth_status, extracted_prints
            auth_status = status
            extracted_prints = new_prints
        
        try:
            with rsid_py.FaceAuthenticator(self.port) as authenticator:
                self.logger.debug("Extracting faceprints for authentication...")
                authenticator.extract_faceprints_for_auth(on_result=on_fp_auth_result)
                
                if auth_status != rsid_py.AuthenticateStatus.Success or not extracted_prints:
                    self.logger.warning(f"Face extraction failed: {auth_status}")
                    self.stats['auth_failed'] += 1
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    return False, None, f"Face extraction failed: {auth_status}"
                
                # Match against all users
                max_score = -100
                best_match_user = None
                
                for user_id, user_info in self.user_db.get_all_users().items():
                    fp = user_info.get('faceprints')
                    if not fp:
                        continue
                    
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
                    
                    if match_result.success and match_result.score > max_score:
                        max_score = match_result.score
                        best_match_user = user_info
                
                if best_match_user:
                    self.logger.info(f"Authentication successful for {best_match_user['name']} (score: {max_score})")
                    self.stats['auth_success'] += 1
                    self.stats['last_auth_time'] = datetime.now().isoformat()
                    self.stats['last_auth_user'] = best_match_user['name']
                    self.user_db.update_last_seen(best_match_user['id'])
                    
                    if self.led_controller:
                        self.led_controller.flash_green(3)
                    
                    return True, best_match_user['name'], best_match_user['permission_level']
                else:
                    self.logger.warning("No matching user found")
                    self.stats['auth_failed'] += 1
                    
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    
                    return False, None, "No match found"
                    
        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            self.stats['auth_failed'] += 1
            return False, None, str(e)
    
    def enroll_user(self, user_id: str, name: str, permission_level: str = "Limited access") -> bool:
        """Enroll a new user with faceprints"""
        self.logger.info(f"Starting enrollment for {name} (ID: {user_id})")
        
        enroll_status = None
        extracted_prints = None
        
        def on_fp_enroll_result(status, prints):
            nonlocal enroll_status, extracted_prints
            enroll_status = status
            extracted_prints = prints
        
        def on_progress(face_pose):
            self.logger.debug(f"Enrollment progress: {face_pose}")
        
        try:
            with rsid_py.FaceAuthenticator(self.port) as authenticator:
                self.logger.info("Starting face enrollment process...")
                authenticator.extract_faceprints_for_enroll(
                    on_progress=on_progress,
                    on_result=on_fp_enroll_result
                )
                
                if enroll_status == rsid_py.EnrollStatus.Success and extracted_prints:
                    # Convert faceprints to serializable format
                    faceprints_data = {
                        'version': extracted_prints.version,
                        'features_type': extracted_prints.features_type,
                        'flags': extracted_prints.flags,
                        'adaptive_descriptor_nomask': list(extracted_prints.features),
                        'adaptive_descriptor_withmask': [0] * 515,
                        'enroll_descriptor': list(extracted_prints.features)
                    }
                    
                    # Add user to database
                    success = self.user_db.add_user(user_id, name, permission_level, faceprints_data)
                    
                    if success:
                        self.logger.info(f"Enrollment successful for {name}")
                        self.stats['enrollments'] += 1
                        
                        if self.led_controller:
                            self.led_controller.flash_green(5)
                    
                    return success
                else:
                    self.logger.error(f"Enrollment failed: {enroll_status}")
                    
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    
                    return False
                    
        except Exception as e:
            self.logger.error(f"Enrollment error: {e}")
            return False
    
    def delete_user(self, user_id: str) -> bool:
        """Delete a user from the database"""
        return self.user_db.delete_user(user_id)
    
    def list_users(self) -> list:
        """List all enrolled users"""
        users = self.user_db.get_all_users()
        user_list = []
        
        for user_id, user_info in users.items():
            user_list.append({
                'id': user_id,
                'name': user_info['name'],
                'permission': user_info['permission_level'],
                'created': user_info.get('created_at', 'Unknown'),
                'last_seen': user_info.get('last_seen', 'Never')
            })
        
        return user_list
    
    def get_stats(self) -> dict:
        """Get service statistics"""
        return self.stats.copy()
    
    def run_service(self):
        """Main service loop"""
        self.logger.info("Host Mode Service started")
        self.logger.info(f"Port: {self.port}, Device Type: {self.device_type}")
        
        # Start card reader monitoring thread if available
        if self.card_reader_initialized:
            card_thread = threading.Thread(target=self._card_reader_loop, daemon=True)
            card_thread.start()
            self.logger.info("Card reader monitoring started")
        
        # Start button listener if available
        button_listener = None
        if BUTTON_SUPPORT:
            try:
                button_listener = ButtonListener(
                    pin=16,
                    callback=lambda: self.authenticate_without_card()
                )
                button_thread = threading.Thread(target=button_listener.start, daemon=True)
                button_thread.start()
                self.logger.info("Button listener started on GPIO 16")
            except Exception as e:
                self.logger.warning(f"Failed to start button listener: {e}")
        
        # Main service loop
        try:
            while self.running:
                time.sleep(1)
                # Could add periodic tasks here (cleanup, stats reporting, etc.)
        
        except KeyboardInterrupt:
            self.logger.info("Service interrupted by user")
        
        finally:
            self.cleanup()
    
    def _card_reader_loop(self):
        """Monitor card reader for authentication requests"""
        self.logger.info("Card reader monitoring active")
        last_card_id = None
        card_cooldown = 3.0  # seconds before same card can be read again
        last_read_time = 0
        
        while self.running:
            try:
                card_id = get_card_id(timeout=0.5)
                
                if card_id is not None:
                    current_time = time.time()
                    
                    # Check if it's the same card within cooldown period
                    if card_id == last_card_id and (current_time - last_read_time) < card_cooldown:
                        continue
                    
                    self.logger.info(f"Card detected: {card_id}")
                    success, user_name, permission = self.authenticate_with_card(card_id)
                    
                    if success:
                        self.logger.info(f"Access granted to {user_name} ({permission})")
                    else:
                        self.logger.warning(f"Access denied for card {card_id}")
                    
                    last_card_id = card_id
                    last_read_time = current_time
                    
            except Exception as e:
                self.logger.error(f"Card reader error: {e}")
                time.sleep(1)
    
    def cleanup(self):
        """Cleanup resources"""
        self.running = False
        self.logger.info("Cleaning up resources...")
        
        if self.led_controller:
            self.led_controller.cleanup()
            self.logger.info("LED controller cleaned up")
        
        if self.card_reader_initialized and CARD_READER_SUPPORT:
            try:
                disconnect_card_reader()
                self.logger.info("Card reader disconnected")
            except:
                pass
        
        self.logger.info("Service stopped")
    
    def stop(self):
        """Stop the service"""
        self.logger.info("Stopping service...")
        self.running = False


def interactive_mode(service: HostModeService):
    """Run in interactive mode with menu"""
    
    def print_menu():
        print("\n" + "="*60)
        print("  RealSense ID Host Mode Service - Interactive Mode")
        print("="*60)
        print("  1. Authenticate (no card)")
        print("  2. Enroll new user")
        print("  3. List all users")
        print("  4. Delete user")
        print("  5. Show statistics")
        print("  6. Clear all users")
        print("  0. Exit")
        print("-"*60)
    
    while True:
        print_menu()
        choice = input("Enter choice: ").strip()
        
        if choice == '1':
            print("\nStarting authentication...")
            success, name, permission = service.authenticate_without_card()
            if success:
                print(f"✅ Authentication successful: {name} ({permission})")
            else:
                print(f"❌ Authentication failed: {permission}")
        
        elif choice == '2':
            user_id = input("Enter user ID (e.g., card number): ").strip()
            name = input("Enter user name: ").strip()
            permission = input("Enter permission level (Extended/Limited) [Limited]: ").strip() or "Limited access"
            
            if user_id and name:
                print(f"\nEnrolling {name}...")
                print("Please look at the camera and follow the instructions...")
                if service.enroll_user(user_id, name, permission):
                    print(f"✅ Enrollment successful for {name}")
                else:
                    print(f"❌ Enrollment failed")
        
        elif choice == '3':
            users = service.list_users()
            if users:
                print(f"\nTotal users: {len(users)}")
                print("-"*60)
                for user in users:
                    print(f"ID: {user['id']}")
                    print(f"  Name: {user['name']}")
                    print(f"  Permission: {user['permission']}")
                    print(f"  Created: {user['created']}")
                    print(f"  Last seen: {user['last_seen']}")
                    print()
            else:
                print("\nNo users enrolled")
        
        elif choice == '4':
            user_id = input("Enter user ID to delete: ").strip()
            if user_id:
                if service.delete_user(user_id):
                    print(f"✅ User {user_id} deleted")
                else:
                    print(f"❌ User {user_id} not found")
        
        elif choice == '5':
            stats = service.get_stats()
            print("\nService Statistics:")
            print("-"*60)
            print(f"Authentication attempts: {stats['auth_attempts']}")
            print(f"Successful: {stats['auth_success']}")
            print(f"Failed: {stats['auth_failed']}")
            print(f"Enrollments: {stats['enrollments']}")
            print(f"Last auth time: {stats['last_auth_time'] or 'Never'}")
            print(f"Last auth user: {stats['last_auth_user'] or 'None'}")
        
        elif choice == '6':
            confirm = input("Are you sure you want to delete ALL users? (yes/no): ").strip().lower()
            if confirm == 'yes':
                if service.user_db.clear_all():
                    print("✅ All users deleted")
                else:
                    print("❌ Failed to clear users")
        
        elif choice == '0':
            print("\nExiting...")
            break
        
        else:
            print("Invalid choice")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='host_mode_cli',
        description='RealSense ID Host Mode Service'
    )
    
    parser.add_argument(
        '-p', '--port',
        help='Device port. Will auto-detect if not specified.',
        type=str,
        default=None
    )
    
    parser.add_argument(
        '-d', '--daemon',
        help='Run as daemon/service (non-interactive)',
        action='store_true'
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
    
    # Setup logging
    logger = setup_logging(args.log_file, args.debug)
    
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
                logger.warning(f"No devices auto-detected. Trying default port: {port}")
            else:
                port = "/dev/ttyACM0"
                logger.warning(f"No devices auto-detected. Trying default port: {port}")
        else:
            port = devices[0]
            logger.info(f"Auto-detected device on port: {port}")
    
    # Discover device type
    try:
        device_type = rsid_py.discover_device_type(port)
        logger.info(f"Device type: {device_type}")
    except Exception as e:
        logger.error(f"Could not connect to device on port {port}: {e}")
        print("\nPlease check:")
        print("  - Device is connected")
        print("  - Port is correct (use -p to specify)")
        print("  - You have necessary permissions")
        exit(1)
    
    # Create service
    service = HostModeService(port, device_type, logger)
    
    # Signal handler for clean exit
    def signal_handler(sig, frame):
        logger.info("Signal received, shutting down...")
        service.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run in appropriate mode
    if args.daemon:
        logger.info("Starting in daemon mode...")
        service.run_service()
    else:
        logger.info("Starting in interactive mode...")
        try:
            interactive_mode(service)
        finally:
            service.cleanup()


if __name__ == '__main__':
    main()
