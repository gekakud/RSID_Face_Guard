"""Idle screen picks the IN/OUT toggle by mode (FR-MODE-06, FR-UI-01/03)."""

import pytest

import config
from gui_web.web_window import WebSessionView


class FakeDeviceUI:
    """Records which idle variant the view drove."""

    def __init__(self):
        self.calls = []

    def screensaver(self):
        self.calls.append("screensaver")

    def screensaver_basic(self):
        self.calls.append("screensaver_basic")

    def set_attendance_enabled(self, on):
        self.calls.append(f"attendance_enabled={on}")


def _view():
    ui = FakeDeviceUI()
    return ui, WebSessionView(ui, page=None)


@pytest.mark.parametrize("mode", ["card_and_face", "card_only", "face_only"])
def test_non_time_registry_modes_hide_direction_control(mode, monkeypatch):
    monkeypatch.setattr(config, "DEVICE_MODE", mode)
    ui, view = _view()

    view.show_idle()

    assert ui.calls == ["screensaver_basic"]


def test_time_registry_shows_direction_control(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_MODE", "time_registry")
    ui, view = _view()

    view.show_idle()

    # The control ships hidden in index.html, so it must be switched on too.
    assert ui.calls == ["attendance_enabled=True", "screensaver"]
