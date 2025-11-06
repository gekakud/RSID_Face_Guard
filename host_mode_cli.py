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
    """Manage user database in JSON file (read-only)"""
    
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
        self.logger.error("No user database found - cannot authenticate without users")
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
    
    def __init__(self, port: str, device_type: rsid_py.DeviceType, logger: logging.Logger = None):
        self.port = port
        self.device_type = device_type
        self.logger = logger or logging.getLogger(__name__)
        self.running = True
        self.user_db = UserDatabase(logger=self.logger)
        
        # Check if we have users in database
        if not self.user_db.get_all_users():
            self.logger.error("No users in database. Please enroll users using a different tool.")
            sys.exit(1)
        
        # Initialize LED controller if available
        self.led_controller = None
        if LED_SUPPORT:
            try:
                self.led_controller = LEDController()
                self.logger.info("LED feedback initialized")
            except Exception as e:
                self.logger.warning(f"LED controller init failed: {e}")
        
        # Initialize card reader (required)
        try:
            initialize_card_reader()
            self.logger.info("Card reader initialized")
        except Exception as e:
            self.logger.error(f"Card reader initialization failed: {e}")
            sys.exit(1)
    
    def authenticate_with_card(self, card_id: int) -> tuple[bool, Optional[str], Optional[str]]:
        """Authenticate user with card ID and face matching"""
        self.logger.info(f"Authentication attempt with card ID: {card_id}")
        
        # Check if card ID exists in database
        user_info = self.user_db.get_user(str(card_id))
        if not user_info:
            self.logger.warning(f"Card ID {card_id} not found in database")
            if self.led_controller:
                self.led_controller.flash_red(3)
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
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    return False, None, f"Face extraction failed: {auth_status}"
                
                # Perform host-side matching
                fp = user_info.get('faceprints')
                if not fp:
                    self.logger.error(f"No faceprints stored for user {user_info['name']}")
                    if self.led_controller:
                        self.led_controller.flash_red(3)
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
                    
                    if self.led_controller:
                        self.led_controller.flash_green(3)
                    
                    return True, user_info['name'], user_info['permission_level']
                else:
                    self.logger.warning(f"Face match failed for card ID {card_id}")
                    
                    if self.led_controller:
                        self.led_controller.flash_red(3)
                    
                    return False, None, "Face match failed"
                    
        except Exception as e:
            self.logger.error(f"Authentication error: {e}")
            if self.led_controller:
                self.led_controller.flash_red(3)
            return False, None, str(e)
    
    def run_service(self):
        """Main service loop"""
        self.logger.info("Host Mode Service started (Card Authentication Only)")
        self.logger.info(f"Port: {self.port}, Device Type: {self.device_type}")
        self.logger.info(f"Total users in database: {len(self.user_db.get_all_users())}")
        
        # Start card reader monitoring thread
        card_thread = threading.Thread(target=self._card_reader_loop, daemon=True)
        card_thread.start()
        self.logger.info("Card reader monitoring started")
        
        # Main service loop
        try:
            while self.running:
                time.sleep(1)
                # Main loop just keeps the service alive
        
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
                        self.logger.info(f"✅ Access granted to {user_name} ({permission})")
                    else:
                        self.logger.warning(f"❌ Access denied for card {card_id}: {permission}")
                    
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
    
    # Setup logging
    logger = setup_logging(args.log_file, args.debug)
    
    # Check if card reader is available
    if not CARD_READER_SUPPORT:
        logger.error("Card reader module is required but not available. Exiting.")
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
    
    # Run service
    logger.info("Starting service mode...")
    service.run_service()


if __name__ == '__main__':
    main()
