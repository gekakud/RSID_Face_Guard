# Access Control Edge Device

**Production Software Requirements – Updated Specification**

Revision 2.0 | Raspberry Pi Compute Module 5 | QR Provisioning, Technician Mode, API, Data and Cybersecurity

| Item | Detail |
|---|---|
| Document status | Updated working specification |
| Primary platform | Raspberry Pi Compute Module 5 |
| Camera | Intel RealSense color/depth camera |
| Primary provisioning method | Installer mobile application + signed QR code |
| Security posture | Offline-capable, least privilege, signed commands and encrypted data |
| Revision date | July 2026 |

## Revision Summary

- The initial server URL entry method is now defined as QR-code provisioning from an authorized installer application.
- A one-second pre-application technician-entry screen and signed technician QR command flow are defined.
- The server-to-device JSON dataset is defined, including users, door-specific permissions, credentials, synchronization metadata and 512-element faceprint vectors.
- The Raspberry Pi-to-server REST API, authentication, idempotency, synchronization and error behavior are defined.
- Cybersecurity requirements are added for secure boot posture, identity, TLS, secrets, local encryption, Wi-Fi/Bluetooth control, updates, audit and recovery.
- Factory reset and re-provisioning now require an authorized, short-lived signed command and physical presence.

## Document Conventions

- “Shall” indicates a mandatory requirement. “Should” indicates a recommended requirement. “May” indicates an optional capability.
- All timestamps exchanged with the server shall use UTC in ISO 8601 format.
- All identifiers shall be opaque strings or UUIDs unless explicitly specified otherwise.
- Examples are informative; the normative field definitions and validation rules take precedence.

## 1. Purpose

This document defines the initial production software requirements for an access control edge device based on the Raspberry Pi 5 Compute Module.

The device is responsible for:

- Local user identification.
- Card and/or facial recognition.
- User authorization.
- Activating a local relay or sending an authorization message to a local access controller.
- Operating during temporary loss of server connectivity.
- Synchronizing device configuration, users, credentials, facial data, and authorization information with a central server.
- Reporting access attempts, device status, faults, and operational events.
- Maintaining local event and system logs.
- Supporting future use as an attendance management terminal.

This document focuses primarily on the operational flow and production software behavior.

Detailed API structures, database schemas, hardware communication interfaces, and attendance functionality shall be defined separately.

## 2. High-Level Operational Flow

### 2.1 First Installation Flow

```text
POWER ON
|
v
SYSTEM INITIALIZATION
|
v
VALID LOCAL CONFIGURATION AVAILABLE?
|                         |
| NO                      | YES
v                         v
ENTER PROVISIONING MODE   LOAD LOCAL CONFIGURATION
|                         |
v                         v
RECEIVE SERVER URL       INITIALIZE HARDWARE
|                         |
v                         v
CONNECT TO SERVER        START BACKGROUND SERVICES
|                         |
v                         v
SEND INITIAL DEVICE      ENTER NORMAL OPERATION
REGISTRATION REQUEST
|
v
RECEIVE:
- DEVICE CONFIGURATION
- USER DATABASE
- ACCESS PERMISSIONS
- SYNCHRONIZATION SETTINGS
- REPORTING SETTINGS
|
v
VALIDATE RECEIVED DATA
|
+------ INVALID ------> DISPLAY SETUP ERROR
|                       LOG FAILURE
|                       RETRY ACCORDING TO POLICY
|
v
STORE CONFIGURATION AND DATABASE
|
v
INITIALIZE HARDWARE AND SERVICES
|
v
ENTER NORMAL OPERATION
```

### 2.2 Normal Access Flow

```text
IDLE SCREEN
|
+-----------------------------------+
|                                   |
| DEVICE WITH CARD READER           | DEVICE WITHOUT CARD READER
v                                   v
WAIT FOR CARD                     WAIT FOR USER BUTTON
|                                   |
v                                   v
READ CARD IDENTIFIER             ACTIVATE CAMERA
|                                   |
v                                   v
FIND ASSOCIATED USER             ACQUIRE FACE
|                                   |
v                                   v
ACTIVATE CAMERA                  SEARCH LOCAL USER DATABASE
|                                   |
v                                   v
ACQUIRE FACE                     IDENTIFY USER
|
v
VERIFY FACE AGAINST
ASSOCIATED USER
|
+-------------------+
|
v
USER IDENTIFIED?
|          |
| YES      | NO
v          v
CHECK AUTHORIZATION  ACCESS DENIED
|
v
ACCESS APPROVED?
|          |
| YES      | NO
v          v
DETERMINE OUTPUT   ACCESS DENIED
|
+------+------+
|             |
v             v
ACTIVATE RELAY   SEND AUTHORIZATION
MESSAGE TO LOCAL
ACCESS CONTROLLER
|             |
+------v------+
|
v
DISPLAY WELCOME MESSAGE
|
v
CREATE LOCAL EVENT
|
v
STORE IMAGE ACCORDING TO POLICY
|
v
SEND OR QUEUE EVENT FOR SERVER
|
v
RETURN TO IDLE
```

### 2.3 Background Services

The following services shall run independently of the interactive access flow:

- Device configuration synchronization
- User database synchronization + Access permission synchronization
- Event upload
- Device status reporting
- Fault reporting
- Time synchronization
- Server connectivity monitoring
- Log management
- Storage monitoring
- Software update checks
- Process monitoring and recovery

An access attempt shall not be interrupted by a normal background synchronization operation.

## 3. System Overview

The access control system consists of the following components.

### 3.1 Edge Device

The edge device is based on a Raspberry Pi 5 Compute Module and may include:

- Display.
- Camera.
- Optional card reader.
- Optional relay output.
- Optional output interface to a local access controller.
- Network interface.
- Local persistent storage.
- Other hardware interfaces to be defined.

### 3.2 Central Server

The server may be:

- A local server installed at the customer site.
- A cloud-based server.

The server provides:

- Device configuration.
- User information.
- Card and credential information.
- Facial recognition data.
- User access permissions.
- Access schedules.
- Synchronization instructions.
- Device monitoring.
- Fault and log collection.
- Software update instructions.

### 3.3 Local Access Controller

In some installations, the edge device shall not directly control the door.

After successful identification and authorization, the device shall send an authorization message to a local access controller.

The local controller shall be responsible for operating the physical door.

The interface may include:

- Wiegand output.
- Serial communication.
- Ethernet communication.
- Digital output.
- Other protocol to be defined.

## 4. Device Operating Modes

The production software should use a single configurable software version for all supported device configurations.

Possible operating modes include:

- access_control
- attendance
- access_control_and_attendance

Possible access output modes include:

- local_relay
- external_access_controller
- no_access_output

The active functionality shall be determined by the configuration received from the server.

Possible device configurations include:

- Card reader and facial verification.
- Facial identification without a card reader.
- Direct door control using a relay.
- Authorization through a local access controller.
- Wiegand output to a local access controller.

Attendance management operation.

Combined access control and attendance operation.

Attendance functionality shall be supported by the architecture but is not defined in the current specification.

### 4.1 Security and Lifecycle Operating States

- factory_unprovisioned: no valid device identity or production dataset exists. QR provisioning is enabled; production access operation is disabled.
- provisioning: the device is processing an installer QR, establishing network connectivity, registering with the server and downloading its first valid dataset.
- production: normal access-control operation. Provisioning QR codes are ignored. Wi-Fi and Bluetooth behavior follows the approved customer security profile.
- technician_entry_window: a one-second startup window displayed before the main application. Only a local touch activates technician QR scanning.
- technician_mode: a time-limited maintenance state entered only after local physical interaction and validation of a signed technician QR command.
- degraded_offline: server connectivity is unavailable but the last valid local dataset can safely authorize users.
- safe_service: repeated critical failures prevent normal operation; access output is disabled unless a separately approved fail-safe policy applies.
- factory_reset_pending: an authorized reset command has been validated and the device is waiting for final local confirmation or controlled restart.

## 5. Initial Device Provisioning by Installer QR Code

### 5.1 Entry Conditions

- On first boot, after an authorized factory reset, or when no valid local identity exists, the device shall enter factory_unprovisioned state.
- The device shall not enter normal access operation until registration, initial synchronization, validation and atomic activation have completed.
- Provisioning shall be implemented as a separate service from the main access-control application.

### 5.2 Provisioning Screen

The display shall show a clear installation instruction, for example:

```text
Device setup required
1. Open the authorized Installer application on your phone.
2. Select the customer, site and door.
3. Create a setup QR code.
4. Hold the QR code in front of this device camera.
```

- The screen shall indicate camera readiness, network state, QR validity, server connection progress and setup result.
- The screen shall not display credentials, secret tokens, internal URLs containing secrets, detailed server errors or biometric data.
- A provisioning timeout shall not grant access or silently change the device state.

### 5.3 Installer Application and QR Creation

- The installer shall authenticate to the mobile application using an account authorized for the selected organization and site.
- The mobile application shall request a short-lived, one-time provisioning authorization from the server.
- The QR shall contain a signed provisioning envelope. The server shall bind the authorization to the organization, site, door, allowed device class and expiration time.
- The QR shall not contain permanent device credentials or reusable customer secrets.

#### 5.3.1 Provisioning QR Envelope

```json
{
  "schema": "acme.provisioning-qr.v1",
  "command": "provision_device",
  "server_url": "https://access.example.com",
  "tenant_id": "tenant_123",
  "site_id": "site_456",
  "door_id": "door_789",
  "provisioning_token": "opaque-one-time-token",
  "issued_at": "2026-07-27T15:00:00Z",
  "expires_at": "2026-07-27T15:10:00Z",
  "nonce": "b188...uuid",
  "network_profile": {
    "mode": "ethernet_or_preconfigured_wifi",
    "wifi_profile_ref": null
  },
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "installer-signing-key-2026-01",
    "value": "base64url-signature"
  }
}
```

### 5.4 Device-Side QR Processing

1. Start the RealSense color stream and QR detector.
2. Decode the QR payload and enforce maximum payload size.
3. Validate JSON syntax and schema version.
4. Validate server URL against HTTPS and allowed URL rules.
5. Validate issued_at, expires_at and nonce.
6. Validate the digital signature using a pinned trusted public key or certificate chain.
7. Verify that the command is permitted in the current device state.
8. Establish server communication and exchange the one-time token for a permanent device identity.
9. Download the initial signed dataset.
10. Validate and atomically activate the configuration and local database.
11. Store only the permanent device identity and approved configuration; erase the provisioning token.
12. Transition to production state and restart controlled services.

### 5.5 Initial Device Registration Request

```json
{
  "hardware_identity": {
    "device_serial": "ACD-CM5-00001234",
    "cm_serial": "10000000abcd1234",
    "mac_addresses": [
      "..."
    ],
    "hardware_model": "AccessTerminal-CM5",
    "hardware_revision": "A1"
  },
  "software": {
    "os_version": "...",
    "application_version": "2.0.0",
    "boot_slot": "A"
  },
  "capabilities": {
    "realsense": true,
    "card_reader": true,
    "relay": true,
    "wiegand_output": false,
    "wifi": true,
    "bluetooth": true,
    "secure_element": false
  },
  "provisioning_token": "opaque-one-time-token",
  "request_id": "uuid",
  "timestamp_utc": "2026-07-27T15:02:00Z"
}
```

### 5.6 Provisioning Failure Behavior

- Invalid or expired QR: reject locally, record a security event and continue scanning.
- Server unreachable: remain unprovisioned and retry according to a bounded backoff policy.
- Registration rejected: show a generic setup error and require a newly generated QR when the authorization is no longer valid.
- Dataset validation failure: do not activate the dataset; retain the unprovisioned state and upload diagnostics when possible.
- Power interruption: provisioning shall resume safely without a partially activated identity or database.

## 6. Application Startup and Technician Entry Window

### 6.1 Startup Sequence

1. Operating system and mandatory security services start.
2. Hardware identity, storage integrity and system time are checked.
3. The display service shows a one-second technician entry window before the main application starts.
4. If the technician button is not pressed, the launcher selects the normal state: provisioning, production, degraded_offline or safe_service.
5. If pressed, the device enters technician QR scan mode.
6. The selected application state is started under operating-system supervision.

### 6.2 One-Second Technician Entry Screen

```text
For technician interface, press here
```

- The touch target shall be visible for approximately one second. The exact duration shall be configurable within an approved range.
- The screen shall appear before initialization of the main access workflow, but after the display and trusted launcher are operational.
- No remote network request shall be sufficient to enter technician mode. A local screen press or approved physical service input is mandatory.
- Failure to press within the window shall not delay normal startup.

### 6.3 Technician QR Command Flow

- After local entry, the device shall activate the RealSense QR scanner for a limited time.
- The technician shall authenticate in the authorized mobile application and select an action allowed for that device and customer.
- The mobile application shall obtain a signed, short-lived command from the server and display it as a QR code.
- The device shall validate signature, audience, device ID or hardware serial, command type, expiry, nonce, technician authorization and current state.
- High-impact operations shall require a second local confirmation screen.

#### 6.3.1 Technician Command Example – Factory Reset

```json
{
  "schema": "acme.technician-command.v1",
  "command_id": "uuid",
  "command": "factory_reset",
  "target": {
    "device_id": "dev_00291",
    "hardware_serial": "ACD-CM5-00001234"
  },
  "parameters": {
    "preserve_audit_anchor": true,
    "erase_pending_events": false
  },
  "requested_by": {
    "technician_id": "tech_77",
    "organization_id": "service_org_1"
  },
  "issued_at": "2026-07-27T15:20:00Z",
  "expires_at": "2026-07-27T15:22:00Z",
  "nonce": "uuid",
  "requires_local_confirmation": true,
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "technician-command-key-2026-01",
    "value": "base64url-signature"
  }
}
```

#### 6.3.2 Allowed Technician Commands

- view_diagnostics
- export_sanitized_diagnostics
- test_camera
- test_card_reader
- test_relay_with_warning
- temporary_network_enable
- network_configuration
- restart_application
- reboot_device
- request_support_session
- reprovision
- factory_reset
- install_approved_update

#### 6.3.3 Command Safety Rules

- Commands shall be allow-listed, not arbitrary shell commands.
- A QR command shall execute at most once. Used nonces shall be stored for replay protection.
- Factory reset, re-provisioning, relay test and network enable shall create SECURITY audit records.
- A factory reset shall not occur merely because a QR was visible to the camera; local technician entry and final confirmation are required.
- Technician mode shall automatically exit after inactivity or command completion.

## 7. User Interface

### 7.1 Idle Screen

The idle screen shall include:

- Customer or product logo.
- A clear user instruction.
- Optional current time and date.
- Optional device status indication.

For a device with a card reader, the message shall be similar to:

- Present your card for access

For a device without a card reader, the message shall be similar to:

- Press to enter

The exact text and visual design shall be provided separately.

### 7.2 Required Screen States

The software shall support at least:

- Starting
- Provisioning required
- Connecting to server
- Synchronizing
- Idle
- Waiting for card / Card detected
- Camera active
- Face acquisition
- Identification / Verification in progress
- Access granted / Access denied
- Temporary error
- Offline / Out of service
- Maintenance

### 7.3 Access Granted Screen

When access is approved, the device shall display a message similar to:

- Welcome

The display may optionally include:

- User name.
- User image.
- Custom greeting.
- Access-related information.

The displayed information shall be controlled by configuration and privacy policy.

### 7.4 Access Denied Screen

When access is denied, the device shall display a message similar to:

- You are not authorized

The display duration shall be configurable.

Internal technical denial reasons should not normally be displayed to the user.

### 7.5 Additional Provisioning and Service Screens

- Technician entry window: “For technician interface, press here”.
- Technician QR scan: “Open the technician application and show the authorized QR command”.
- QR rejected: generic reason category such as expired, unauthorized or invalid; no cryptographic details.
- Factory reset confirmation: explicit warning describing which local data will be erased.
- Provisioning progress: scanning QR, validating authorization, connecting, registering, downloading, validating and completing.
- Security lockout: displayed when repeated invalid technician commands exceed policy.

## 8. Access Flow with Card Reader

### 8.1 Card Reading

While in the idle state, the device shall wait for card reader interrupt.

When a card is detected, the device shall:

- Read the card identifier or employee identifier.
- Search for the associated user in the local database.
- Confirm that the card and user are active.
- Load the associated facial reference data.
- Perform facial verification.

### 8.2 Invalid or Unknown Card

If the card:

- Cannot be read.
- Has an invalid format.
- Is inactive.
- Is not present in the local database.
- Is associated with an inactive user.

The device shall treat the attempt according to the configured policy.

Possible behavior includes:

- Immediate denial.
- Image capture followed by denial.
- User instruction to try again.
- Logging as an unknown credential event.

### 8.3 Face Acquisition and Verification

The software shall use RealSense api and camera for faceprint acquisition and verification

The captured face shall be compared only against the facial data associated with the cardholder.

The verification result should include:

- match result
- similarity score
- decision threshold

## 9. Access Flow without Card Reader

### 9.1 User Initiation

The user shall initiate the process by pressing a button on the device display.

The device shall then:

- Activate the camera.
- Acquire one or more face images.
- Perform one-to-many identification

### 9.2 One-to-Many Identification

The detected face shall be compared against the relevant local user database using RealSense API.

The identification result should include:

- identified user ID
- best-match score
- decision threshold
- algorithm version

The device shall handle multiple identfication scenarios according RealSense api, such as - No face detected, Multiple faces detected, No match above the threshold, Local recognition service failure, spoofing, liveliness etc.

## 10. Authorization Decision

Identification and authorization shall be separate software stages.

A successful card or face match does not automatically grant access.

The authorization process may consider:

- User active status.
- Card active status.
- Permission for the current device.
- Approved access schedule, including permission expiration.
- Device operating mode.
- Additional server-defined rules.

### 10.1 Approved Access

If identification and authorization are successful, the device shall:

- Determine the configured output mode.
- Activate the relay or send an authorization message to the local access controller.
- Display the welcome screen.
- Create a local access event.
- Store the relevant image according to policy.
- Upload the event immediately or queue it for later upload.
- Return to the idle screen.

### 10.2 Denied Access

If identification or authorization fails, the device shall:

- Not activate the relay.
- Not send an access-grant command to the local access controller.
- Display an access denied screen.
- Create a local denied-access event.
- Record the denial reason.
- Store an image according to policy.
- Upload or queue the event.
- Return to the idle screen.

### 10.3 Denial Reason Codes

Possible denial reasons include:

- unknown_card
- invalid_card
- inactive_card
- unknown_user
- inactive_user
- face_not_detected
- multiple_faces_detected
- face_mismatch
- face_not_identified
- outside_authorized_schedule
- door_not_authorized
- credential_expired
- camera_error
- card_reader_error
- database_error
- recognition_timeout
- system_error
- other

The final list shall be defined separately.

## 11. Access Grant Output

### 11.1 Local Relay Mode

When configured for direct door control, the software shall activate the local relay.

The configuration shall define:

- relay output
- active state
- activation duration
- activation delay
- retry behavior
- failure behavior

The device shall log:

- relay activation requested
- relay activation failed
- associated event ID

### 11.2 Local Access Controller Mode

When configured to work with a local access controller, the device shall send an authorization message to that controller, the message may include different parameters according controller’s api.

The interface may be Wiegand output or another supported communication method.

No Wiegand input is required.

Where supported, the software shall handle:

- Successful message transmission.
- Controller acknowledgment.
- Controller rejection.
- Communication timeout.
- Communication failure.
- Retry according to configuration.

### 11.3 Authorization and Physical Access States

The software shall distinguish between:

- identity verified
- authorization approved
- access command requested
- access command sent
- access command acknowledged
- relay activated
- door opened

Not all installations will provide physical door-status feedback.

An approved authorization event shall not automatically be interpreted as confirmation that the door physically opened.

## 12. Event Reporting

The device shall create a record for every relevant access attempt.

Events shall include:

- Approved access.
- Denied access.
- Unknown card.
- Unknown user.
- Authorization failure  and type of failure from RealSense.
- Output activation failure.
- Controller communication failure.
- Other access-related events.

### 12.1 Access Event Data Placeholder

Each access event may include:

- event_id
- event_category
- device_id
- site_id
- door_id
- event_timestamp_utc
- timezone
- event_type
- access_result
- denial_reason
- user_id
- employee_id / card_id
- recognition_score
- recognition_threshold
- authorization_result
- output_result
- database_version
- software_version
- image_reference

The final event structure shall be defined separately.

### 12.2 Image Policy

The configuration shall define whether images are stored and uploaded for:

All attempts.

Approved attempts.

Denied attempts.

Unknown users.

Recognition failures.

Low-confidence results.

Fault events.

No access events.

The policy may define:

- full image or cropped face
- resolution
- format
- compression quality
- maximum image size
- local retention period
- upload priority
- delete-after-upload behavior

### 12.3 Immediate and Batch Upload

Events may be:

- Sent immediately.
- Sent periodically.
- Sent immediately only for selected event types.
- Stored locally while offline.
- Uploaded when connectivity is restored.

Every event shall have a unique identifier to prevent duplicate records on the server.

## 13. Offline Operation

The device shall continue operating using the last valid local configuration and database when the server is temporarily unavailable.

During offline operation, the device shall:

- Continue card reading.
- Continue facial recognition.
- Continue local authorization.
- Activate the configured access output when approved.
- Store events locally.
- Store required images locally.
- Store device and fault logs locally.
- Periodically retry server communication.
- Upload queued data after connectivity is restored.

The device shall not lose queued events during:

- Application restart.
- Operating system restart.
- Temporary power interruption.

The display may indicate that the server is offline, provided this does not interfere with normal user interaction.

## 14. Database Synchronization

### 14.1 Synchronization Types

The software should support:

- Initial full synchronization.
- Periodic full synchronization.
- Incremental synchronization.
- User additions.
- User updates.
- User deletion or deactivation.
- Card and credential updates.
- Facial data updates.
- Access permission updates.
- Configuration updates.
- Full database replacement.

### 14.2 Synchronization Frequency

The server configuration shall define synchronization intervals for:

- device configuration
- user database
- credentials
- facial data
- access permissions
- server commands
- software update checks

### 14.3 Atomic Update

A partially received or invalid update shall not replace the currently active data.

The device shall:

- Download new data to temporary storage.
- Validate integrity.
- Validate the schema version.
- Validate required records.
- Validate associated facial data.
- Activate the new dataset atomically.
- Preserve the previous valid dataset for rollback where possible.

### 14.4 Synchronization State

The device shall locally maintain:

- current configuration version
- current database version
- last synchronization attempt
- last successful synchronization
- last failed synchronization
- failure reason
- pending update status

## 15. Database Structure Placeholder

The final local database schema shall be defined separately.

Expected entities may include:

- Device
- DeviceConfiguration
- Site
- Door
- User
- Employee
- Credential
- Card
- FacePrints
- AccessSchedule
- AccessEvent
- AttendanceEvent
- DeviceEvent
- FaultEvent
- SynchronizationState
- SoftwareVersion
- ConfigurationVersion

The following shall be defined later:

- Tables.
- Fields.
- Relationships.
- Indexes.
- Record versioning.
- Database migrations.
- Data retention.
- Encryption.
- Backup and recovery.

Access events and attendance events shall remain logically separate, even if they share common fields.

## 16. Device Status Reporting

The device shall periodically report its operational status to the server.

Possible status information includes:

- device_id
- timestamp
- online_status
- device_operating_mode
- access_output_mode
- software_version
- configuration_version
- database_version
- CPU_temperature
- CPU_usage
- memory_usage
- storage_usage
- camera_status
- card_reader_status
- relay_status
- local_access_controller_status
- local_database_status
- last_synchronization_time
- last_successful_server_connection
- pending_event_count
- pending_image_count
- system_uptime
- last_reboot_reason

Critical device faults may be reported immediately.

## 17. Local Logging

The device shall maintain local logs for:

- Access attempts.
- Approved access.
- Denied access.
- Card reader activity.
- Authorization decisions.
- Relay operations.
- Local controller communication.
- Database synchronization.
- Server communication.
- Device startup and shutdown.
- Software faults.
- Hardware faults.
- Configuration changes.
- Software updates.
- Maintenance actions.

### 17.1 Log Levels

The software should support:

```text
DEBUG
INFO
WARNING
ERROR
CRITICAL
SECURITY
```

### 17.2 Log Management

The logging mechanism shall support:

- Timestamped records.
- Maximum storage allocation.
- Automatic removal of old logs.
- Prevention of storage exhaustion.
- Optional upload to the server.
- Local retrieval by authorized service personnel.
- Separation between access records and technical logs.

Sensitive information shall not be written to ordinary log files.

This includes:

- Facial templates.
- Passwords.
- Authentication tokens.
- Encryption keys.
- Unprotected biometric data.

### 17.3 Cybersecurity and Protection Requirements

- Unique device identity: each device shall have a unique device ID and asymmetric device key pair. Shared fleet passwords are prohibited.
- Transport security: all server communication shall use TLS 1.2 or later, with TLS 1.3 preferred. Certificate validation shall never be disabled in production.
- Mutual authentication: production deployments should use mutual TLS or signed device requests with short-lived access tokens.
- Trust anchors: server and command-signing trust anchors shall be provisioned through a controlled manufacturing or signed update process.
- Least privilege: services shall run as separate non-root users with minimal Linux capabilities, file permissions and device access.
- Secrets: API tokens, private keys and encryption keys shall not be stored in logs, QR codes or ordinary configuration files. At minimum, files shall be owner-only and encrypted at rest using a device-bound key.
- Biometric protection: faceprints and related indexes shall be encrypted at rest, never written to general logs and deleted according to customer retention policy.
- Local database integrity: active datasets shall include schema version, sequence number, content hash and server signature. Rollback to an older unauthorized dataset shall be rejected.
- Wi-Fi and Bluetooth: radios shall be disabled by default in high-security production profiles and may be enabled only during provisioning or an authorized, time-limited technician command. The system shall block automatic pairing, discovery and unmanaged access points.
- Firewall: inbound connections shall be denied by default. Only explicitly approved local services and destinations shall be allowed.
- Remote access: no default SSH password, no unauthenticated remote shell and no permanent support tunnel. Support access shall be customer-approved, time-limited, audited and cryptographically authenticated.
- Updates: application and operating-system update packages shall be signed, version-controlled, rollback-capable and protected against downgrade below the approved security baseline.
- Boot integrity: the product should use verified boot or equivalent integrity checks where supported by the final CM5 carrier and boot architecture.
- Audit: provisioning, technician entry, invalid QR commands, configuration changes, credential rotation, reset, update and security-policy changes shall create tamper-evident audit events.
- Rate limiting: QR validation, registration, login and API requests shall be rate-limited and protected against replay and brute-force attempts.
- Data minimization: the server shall send only users and permissions relevant to the specific door or approved device scope.
- Fail-safe behavior: security component failure shall not silently grant access. Access output shall follow the configured fail-secure/fail-safe policy and legal safety requirements.
- Secure erasure: reset and decommissioning shall erase credentials, faceprints and local datasets using storage-appropriate secure deletion or cryptographic erasure.

## 18. Error Handling

The software shall detect and handle at least:

- camera unavailable
- card reader unavailable
- relay failure
- local access controller communication failure / Wiegand output failure
- display failure
- server unavailable
- invalid server response
- local database corruption
- insufficient storage
- incorrect system time
- network failure
- facial recognition service failure
- application process crash
- configuration incompatibility
- software update failure

For each fault, the system shall define:

Whether normal access operation may continue.

Whether degraded operation is permitted.

What is displayed to the user.

What is recorded locally.

What is reported to the server.

Whether a component or application restart is required.

Retry frequency.

Escalation behavior.

An authorization approval followed by output failure shall be recorded separately from an authorization denial.

## 19. Process Supervision and Recovery

The production application shall be supervised by the operating system.

The supervision mechanism shall:

- Start the application automatically after boot.
- Restart failed services.
- Detect repeated failures.
- Prevent uncontrolled restart loops.
- Record restart reasons.
- Enter a safe service state after repeated critical failures.
- Support remote diagnostics where allowed.

The software may be divided into separate services for:

- user interface
- access control logic
- facial recognition
- card reader
- local database
- server communication
- synchronization
- event upload
- image upload
- device monitoring
- software updates

A failure in one background service should not terminate the entire application unless normal operation is no longer safe or possible.

## 20. Time Management

Correct time is required for:

- Authorization schedules.
- Event timestamps.
- Synchronization.
- Log records.

Attendance functionality.

The device shall maintain:

- UTC time
- configured timezone
- local display time

The device may synchronize time using:

- Network time service.
- Central server.
- Local real-time clock.
- Other trusted source.

## 21. Software Updates

The production software should support remotely managed updates.

The update mechanism should support:

- Update availability check.
- Version compatibility validation.
- Package integrity verification.
- Interrupted download recovery.
- Scheduled installation.
- Reporting update status.
- Failed-update detection.
- Automatic rollback.
- Prevention of installation during an active access transaction.

Application updates and operating system updates may use separate policies.

## 22. Factory Reset and Re-Provisioning

- Factory reset shall be available only from technician mode entered through local physical interaction.
- The reset QR shall be generated by an authenticated technician application and signed by the server.
- The command shall target a specific device identity, expire within a short period and contain a one-time nonce.
- The device shall show a final confirmation screen before erasing data.
- Reset policy shall explicitly define treatment of pending access events, audit records, logs, images, server URL, device keys, local users, faceprints and update rollback data.
- After successful reset, the device shall restart in factory_unprovisioned state and show the installer QR instructions.
- Re-provisioning without full erasure may be supported as a separate command, but shall preserve required audit continuity and require server authorization.

## 23. Attendance Management Placeholder

The product may be used as an attendance management terminal.

The attendance workflow is not defined in the current specification.

The architecture shall allow future support for:

- Employee attendance identification.
- Check-in events.
- Check-out events.
- Entry and exit direction.
- Shift-related events.
- Offline attendance recording.

Attendance event synchronization.

Combined access and attendance operation.

Attendance-only operation.

Possible event categories include:

- access_event
- attendance_event
- device_event
- fault_event
- security_event

Reserved attendance fields may include:

- attendance_event_type
- entry_exit_direction
- shift_id
- workday_id
- attendance_rule_result

The final attendance behavior shall be defined in a later specification.

## 24. Server-to-Device Data Contract

### 24.1 Dataset Envelope

```json
{
  "schema": "access-device-dataset.v1",
  "dataset_id": "uuid",
  "device_id": "dev_00291",
  "tenant_id": "tenant_123",
  "site_id": "site_456",
  "door_id": "door_789",
  "dataset_version": 1842,
  "base_version": 1841,
  "sync_type": "full",
  "generated_at_utc": "2026-07-27T15:30:00Z",
  "valid_from_utc": "2026-07-27T15:30:00Z",
  "minimum_software_version": "2.0.0",
  "content_encoding": "json",
  "content_hash": "sha256:...",
  "signature": {
    "algorithm": "Ed25519",
    "key_id": "dataset-key-2026-01",
    "value": "..."
  },
  "device_configuration": {},
  "synchronization": {},
  "users": [],
  "deleted_entities": []
}
```

### 24.2 Device Configuration Object

```json
{
  "device_name": "Main Entrance",
  "device_operating_mode": "access_control",
  "access_output_mode": "local_relay",
  "language": "he-IL",
  "timezone": "Asia/Jerusalem",
  "ui": {
    "show_clock": true,
    "show_user_name_on_grant": true,
    "technician_entry_window_ms": 1000,
    "welcome_screen_ms": 1500,
    "denied_screen_ms": 2000
  },
  "recognition": {
    "enabled": true,
    "mode": "card_1_to_1_or_face_1_to_many",
    "algorithm": "realsense",
    "algorithm_version": "x.y",
    "verification_threshold": 0.82,
    "identification_threshold": 0.88,
    "liveness_required": true,
    "multiple_face_policy": "deny"
  },
  "card_reader": {
    "enabled": true,
    "type": "usb",
    "credential_format": "decimal"
  },
  "output": {
    "relay": {
      "enabled": true,
      "channel": 1,
      "active_state": "high",
      "activation_ms": 3000
    },
    "external_controller": {
      "enabled": false
    }
  },
  "image_policy": {
    "capture_approved": false,
    "capture_denied": true,
    "capture_unknown": true,
    "format": "jpeg",
    "max_width": 640,
    "quality": 75,
    "retention_hours": 24,
    "delete_after_upload": true
  },
  "network_security_profile": {
    "wifi_policy": "disabled_in_production",
    "bluetooth_policy": "disabled_in_production",
    "inbound_firewall": "deny",
    "remote_support": "disabled"
  },
  "offline_policy": {
    "enabled": true,
    "maximum_dataset_age_hours": 72,
    "deny_when_time_untrusted": true
  },
  "logging": {
    "level": "INFO",
    "security_log_enabled": true,
    "retention_days": 14,
    "max_storage_mb": 256
  }
}
```

### 24.3 Synchronization Object

```json
{
  "configuration_interval_seconds": 300,
  "user_delta_interval_seconds": 60,
  "full_dataset_interval_seconds": 86400,
  "server_command_interval_seconds": 30,
  "status_interval_seconds": 60,
  "event_upload_interval_seconds": 10,
  "image_upload_interval_seconds": 30,
  "update_check_interval_seconds": 3600,
  "retry": {
    "initial_seconds": 2,
    "maximum_seconds": 300,
    "multiplier": 2.0,
    "jitter_percent": 20
  },
  "limits": {
    "max_users_per_device": 50000,
    "max_faceprints_per_user": 5,
    "max_batch_events": 500,
    "max_payload_bytes": 10485760
  }
}
```

### 24.4 User Record

```json
{
  "user_id": "user_1001",
  "employee_id": "EMP-421",
  "record_version": 17,
  "status": "active",
  "display_name": "Example User",
  "valid_from_utc": "2026-01-01T00:00:00Z",
  "valid_until_utc": null,
  "credentials": [
    {
      "credential_id": "cred_1",
      "type": "card",
      "value_hash": "sha256:...",
      "status": "active",
      "valid_until_utc": null
    }
  ],
  "door_permissions": [
    {
      "door_id": "door_789",
      "permission": "allow",
      "schedule_id": "schedule_day_shift",
      "valid_from_utc": "2026-01-01T00:00:00Z",
      "valid_until_utc": null,
      "anti_passback_profile_id": null
    }
  ],
  "faceprints": [
    {
      "faceprint_id": "fp_1",
      "algorithm": "realsense",
      "algorithm_version": "x.y",
      "vector_type": "uint16",
      "dimension": 512,
      "encoding": "base64-le",
      "vector": "<base64 of 512 unsigned 16-bit values>",
      "quality_score": 0.94,
      "created_at_utc": "2026-06-01T10:00:00Z",
      "template_hash": "sha256:..."
    }
  ],
  "privacy": {
    "display_name_allowed": true,
    "image_display_allowed": false
  },
  "updated_at_utc": "2026-07-27T14:00:00Z"
}
```

Faceprint clarification: the requested “512 uint” vector shall be represented normatively as 512 unsigned integers with a defined width. This specification uses uint16 by default. If the recognition SDK requires uint8, uint32 or floating-point values, vector_type and encoding shall state that explicitly; implementations shall never rely on the ambiguous term “uint” alone.

### 24.5 Access Schedule

```json
{
  "schedule_id": "schedule_day_shift",
  "timezone": "Asia/Jerusalem",
  "weekly": [
    {
      "days": [
        "SUN",
        "MON",
        "TUE",
        "WED",
        "THU"
      ],
      "intervals": [
        {
          "start": "07:00:00",
          "end": "19:00:00"
        }
      ]
    }
  ],
  "exceptions": [
    {
      "date": "2026-09-22",
      "mode": "deny_all"
    }
  ],
  "record_version": 5
}
```

### 24.6 Incremental Synchronization

- A delta response shall state base_version and dataset_version. The device shall reject a delta if its active version does not equal base_version.
- Each record shall contain record_version and updated_at_utc.
- deleted_entities shall contain entity_type, entity_id, deletion_version and deleted_at_utc.
- The device shall apply deltas to temporary storage, validate referential integrity and activate atomically.
- The device shall acknowledge the activated dataset version.

### 24.7 Local Storage Model

- SQLite or another transactional embedded database may be used.
- Recommended logical tables: device_configuration, users, credentials, faceprints, door_permissions, schedules, sync_state, access_events, image_queue, device_events, security_events and command_nonce_history.
- Indexes shall support credential lookup, active-user filtering, door-permission lookup, faceprint retrieval and unsent-event queues.
- Biometric blobs and secret fields shall be encrypted at rest or stored in an encrypted database/container.

## 25. Raspberry Pi – Server API

### 25.1 Common API Rules

- Base path: /api/v1 unless negotiated otherwise.
- Content type: application/json; large binary images and update packages may use multipart or dedicated object upload URLs.
- Authentication after provisioning: mutual TLS and/or OAuth-style short-lived device access token bound to device identity.
- Every mutating request shall include request_id or Idempotency-Key.
- Every response shall include server_time_utc and correlation_id.
- The server shall reject unsupported schema versions explicitly.
- Timeouts and retry behavior shall distinguish connection failure, timeout, 4xx permanent failure and 5xx transient failure.

### 25.2 Endpoint Summary

| Method | Endpoint | Purpose | Auth | Idempotent |
|---|---|---|---|---|
| POST | /provisioning/v1/register | Exchange one-time QR token for device identity and initial dataset | Provisioning token + signed hardware request | Yes |
| POST | /devices/{device_id}/token/refresh | Refresh short-lived device token | mTLS/device token | Yes |
| GET | /devices/{device_id}/configuration | Get current configuration or delta | Device auth | Yes |
| GET | /devices/{device_id}/datasets | Get full or incremental user dataset | Device auth | Yes |
| POST | /devices/{device_id}/datasets/ack | Acknowledge activated version | Device auth | Yes |
| POST | /devices/{device_id}/events:batch | Upload access/device/security events | Device auth | Yes |
| POST | /devices/{device_id}/images:initiate | Create controlled image upload | Device auth | Yes |
| POST | /devices/{device_id}/status | Upload health/status | Device auth | Yes |
| POST | /devices/{device_id}/faults | Upload urgent fault | Device auth | Yes |
| GET | /devices/{device_id}/commands | Retrieve signed server commands | Device auth | Yes |
| POST | /devices/{device_id}/commands/{command_id}/ack | Report command result | Device auth | Yes |
| GET | /devices/{device_id}/updates | Query approved update | Device auth | Yes |
| POST | /devices/{device_id}/updates/{update_id}/status | Report update progress/result | Device auth | Yes |

### 25.3 Provisioning Registration

```text
POST /api/v1/provisioning/v1/register
{
  "request_id": "uuid",
  "provisioning_token": "opaque",
  "hardware_identity": {},
  "software": {},
  "capabilities": {},
  "device_public_key": "base64-spki",
  "timestamp_utc": "..."
}
{
  "device_id": "dev_00291",
  "device_certificate": "PEM or reference",
  "device_access_token": "short-lived-token",
  "token_expires_at": "...",
  "dataset": {
    "download_url": "...",
    "dataset_version": 1,
    "sha256": "...",
    "signature": "..."
  },
  "server_time_utc": "...",
  "correlation_id": "..."
}
```

### 25.4 Dataset Synchronization

```text
GET /api/v1/devices/{device_id}/datasets?current_version=1841&mode=delta
```

- HTTP 200 returns a full or delta dataset.
- HTTP 204 means no update.
- HTTP 409 version_conflict instructs the device to request a full dataset.
- Dataset download and activation shall be independent: the server considers the version active only after the device sends an acknowledgment.

### 25.5 Event Upload

```json
{
  "batch_id": "uuid",
  "device_id": "dev_00291",
  "events": [
    {
      "event_id": "uuid",
      "event_type": "access_attempt",
      "timestamp_utc": "...",
      "user_id": "user_1001",
      "access_result": "granted",
      "recognition": {
        "mode": "verification",
        "score": 0.91,
        "threshold": 0.82
      },
      "authorization": {
        "result": "approved"
      },
      "output": {
        "type": "relay",
        "result": "activated"
      },
      "dataset_version": 1842,
      "software_version": "2.0.0",
      "image_reference": null
    }
  ]
}
```

- The server shall deduplicate events by event_id.
- The response shall identify accepted, duplicate and rejected event IDs.
- Rejected events caused by schema errors shall be quarantined locally and not retried indefinitely.

### 25.6 Status and Fault Reporting

```json
{
  "timestamp_utc": "...",
  "state": "production",
  "software_version": "2.0.0",
  "dataset_version": 1842,
  "uptime_seconds": 12345,
  "resources": {
    "cpu_temperature_c": 58.1,
    "cpu_percent": 22,
    "memory_percent": 48,
    "storage_percent": 61
  },
  "components": {
    "camera": "ok",
    "card_reader": "ok",
    "relay": "ok",
    "database": "ok"
  },
  "queues": {
    "events": 12,
    "images": 2
  },
  "connectivity": {
    "server": "online",
    "wifi": "disabled",
    "bluetooth": "disabled"
  }
}
```

### 25.7 Standard Error Object

```json
{
  "error": {
    "code": "dataset_version_conflict",
    "message": "Generic machine-readable description",
    "retryable": true,
    "retry_after_seconds": 30,
    "details": {}
  },
  "server_time_utc": "...",
  "correlation_id": "..."
}
```

### 25.8 Recommended Status Codes

- 200/201: success.
- 204: no content or no synchronization update.
- 400: malformed request.
- 401: authentication failed.
- 403: authenticated but not authorized.
- 404: resource not found.
- 409: state/version conflict or nonce already used.
- 413: payload too large.
- 422: schema-valid but semantically invalid.
- 429: rate limited.
- 500/502/503/504: transient server or gateway failure.

## 26. Updated Initial Production Acceptance Flow

1. The unprovisioned device displays installer-application and QR instructions.
2. The installer application creates a server-authorized, signed and expiring provisioning QR.
3. The RealSense camera reads the QR and the device rejects invalid, expired or replayed payloads.
4. The device registers using the one-time token and a generated device public key.
5. The server returns the permanent device identity and a signed initial dataset.
6. The device validates schema, signatures, hashes, hardware compatibility, users, 512-element faceprints, schedules and door permissions.
7. The device atomically activates the dataset and enters production mode.
8. At every power-on, a one-second technician-entry screen appears before the main application.
9. Without a press, the correct state starts without additional delay.
10. With a press, the device scans a signed technician QR and executes only allow-listed, authorized commands.
11. Factory reset requires a targeted, expiring QR and final local confirmation.
12. The normal card and facial access flows operate from the local database.
13. Offline authorization and durable event queuing operate through restart and power interruption.
14. Full and incremental synchronization, version acknowledgments and rollback behavior are demonstrated.
15. API requests are authenticated, encrypted, idempotent where required and rate-limited.
16. Wi-Fi and Bluetooth follow the selected production security profile and can only be enabled through approved provisioning or technician flows.
17. Signed update installation and rollback are demonstrated.
18. Security events and technician operations are visible in audit logs without exposing secrets or faceprints.

## 27. Remaining Open Issues and Project Decisions

- Confirm the exact RealSense recognition SDK faceprint numeric type and binary layout; replace the default uint16 example if required.
- Select the final device key storage mechanism: filesystem encryption, TPM/secure element, or carrier-board security component.
- Define the customer-specific Wi-Fi and Bluetooth policies, including whether radios are physically absent, software-disabled or temporarily service-enabled.
- Define the manufacturing identity injection and trust-anchor process.
- Define relay electrical behavior, fail-secure/fail-safe requirements and emergency/lockdown integration.
- Define the final external controller and Wiegand formats.
- Define retention rules for events, images, logs, faceprints and reset audit continuity.
- Define legal/privacy requirements for biometric data per deployment jurisdiction.
- Define exact signed update and verified-boot implementation for the final CM5 hardware and OS image.
- Define technician roles, command permissions, approval workflows and support-session policy.
- Define production penetration testing, vulnerability disclosure, SBOM and security-update support period.
- Complete attendance-management behavior and event contracts.
