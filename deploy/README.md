# deploy/

System files that live OUTSIDE the repo on a running device, kept here so a
fresh `git clone` can reproduce them instead of rediscovering them the hard way.

| File | Installs to | Purpose |
|---|---|---|
| `99-realsense-id.rules` | `/etc/udev/rules.d/` | raw USB access for the RealSense ID camera (non-root preview) |

The systemd unit (`face-guard.service`) and its launcher (`run_service.sh`)
stay in the repo root. See `docs/SETUP_RPI5.md` steps 6 and 8.
