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
    # Set directly: set_device_mode() still refuses this mode until T8.
    monkeypatch.setattr(config, "DEVICE_MODE", "time_registry")
    ui, view = _view()

    view.show_idle()

    assert ui.calls == ["screensaver"]
