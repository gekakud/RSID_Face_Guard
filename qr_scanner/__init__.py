__all__ = ["QRScanner"]


def __getattr__(name):
    if name == "QRScanner":
        from .qr_scanner import QRScanner
        return QRScanner
    raise AttributeError(name)
