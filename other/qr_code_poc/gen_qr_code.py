import json

import qrcode

from qr_common import APP_NAME, create_signature


def generate_device_qr(
    device_id: str,
    url: str,
    output_path: str,
) -> None:
    payload = {
        "app": APP_NAME,
        "version": 1,
        "id": device_id,
        "url": url,
    }

    payload["signature"] = create_signature(payload)

    qr_content = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(qr_content)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white",
    )
    image.save(output_path)


if __name__ == "__main__":
    generate_device_qr(
        device_id="DEVICE-000123",
        url="https://www.google.com",
        output_path="device_000123.png",
    )