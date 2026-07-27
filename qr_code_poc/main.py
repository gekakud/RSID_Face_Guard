import json
import sys
from typing import Optional

import cv2
from qr_common import verify_signature
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class QRCodeReader(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("QR Code Reader")
        self.resize(900, 700)

        self.camera: Optional[cv2.VideoCapture] = None
        self.qr_detector = cv2.QRCodeDetector()
        self.last_qr_data = ""

        self.video_label = QLabel("Camera is stopped")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(640, 480)
        self.video_label.setStyleSheet(
            """
            QLabel {
                background-color: #202020;
                color: white;
                border: 1px solid #505050;
            }
            """
        )

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("Decoded QR-code content will appear here.")

        self.start_button = QPushButton("Start Camera")
        self.stop_button = QPushButton("Stop Camera")
        self.clear_button = QPushButton("Clear Result")

        self.stop_button.setEnabled(False)

        self.start_button.clicked.connect(self.start_camera)
        self.stop_button.clicked.connect(self.stop_camera)
        self.clear_button.clicked.connect(self.clear_result)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.clear_button)

        layout = QVBoxLayout()
        layout.addWidget(self.video_label, stretch=1)
        layout.addLayout(button_layout)
        layout.addWidget(QLabel("QR-code content:"))
        layout.addWidget(self.result_text)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)

    def start_camera(self) -> None:
        if self.camera is not None and self.camera.isOpened():
            return

        # Try camera index 0. Change this to 1, 2, etc. for another camera.
        self.camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)

        if not self.camera.isOpened():
            self.camera.release()
            self.camera = None

            QMessageBox.critical(
                self,
                "Camera Error",
                "Could not open the camera.",
            )
            return

        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        self.timer.start(30)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_camera(self) -> None:
        self.timer.stop()

        if self.camera is not None:
            self.camera.release()
            self.camera = None

        self.video_label.clear()
        self.video_label.setText("Camera is stopped")

        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def update_frame(self) -> None:
        if self.camera is None or not self.camera.isOpened():
            return

        success, frame = self.camera.read()

        if not success:
            return

        frame = self.detect_qr_code(frame)
        self.display_frame(frame)

    def detect_qr_code(self, frame):
        qr_data, points, _ = self.qr_detector.detectAndDecode(frame)

        if points is not None:
            points = points.astype(int).reshape(-1, 2)

            for index in range(len(points)):
                start_point = tuple(points[index])
                end_point = tuple(points[(index + 1) % len(points)])

                cv2.line(
                    frame,
                    start_point,
                    end_point,
                    (0, 255, 0),
                    3,
                )

        if qr_data:
            if qr_data != self.last_qr_data:
                self.last_qr_data = qr_data
                self.result_text.setPlainText(self.format_qr_data(qr_data))

            if points is not None:
                text_position = tuple(points[0])

                cv2.putText(
                    frame,
                    "QR code detected",
                    (text_position[0], max(text_position[1] - 15, 30)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )

        return frame

    def format_qr_data(self, qr_data: str) -> str:
        try:
            payload = json.loads(qr_data)
        except (json.JSONDecodeError, TypeError):
            return qr_data

        if not isinstance(payload, dict):
            return qr_data

        is_valid = verify_signature(payload)
        status = "VALID signature" if is_valid else "INVALID signature"

        lines = [f"Status: {status}", "", "Decoded text:", qr_data, "", "Parsed fields:"]
        for key, value in payload.items():
            lines.append(f"{key}: {value}")

        return "\n".join(lines)

    def display_frame(self, frame) -> None:
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        height, width, channels = rgb_frame.shape
        bytes_per_line = channels * width

        image = QImage(
            rgb_frame.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        ).copy()

        pixmap = QPixmap.fromImage(image)
        pixmap = pixmap.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.video_label.setPixmap(pixmap)

    def clear_result(self) -> None:
        self.last_qr_data = ""
        self.result_text.clear()

    def closeEvent(self, event) -> None:
        self.stop_camera()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)

    window = QRCodeReader()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()