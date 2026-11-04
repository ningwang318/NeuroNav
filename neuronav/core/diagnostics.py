"""Tracking diagnostics snapshot, written as JSON for offline inspection."""
import json
from pathlib import Path
import time


def diagnostics_data(snapshot, reference=None):
    frame = snapshot['frame']
    data = dict(server=snapshot['server'], version=snapshot['version'], frames_received=snapshot['frames'],
                decode_errors=snapshot['parse_errors'], error=snapshot['error'],
                body_names=snapshot['models'].names, cameras=snapshot['models'].cameras,
                frame_number=frame.number if frame else None,
                frame_age_ms=(time.monotonic()-frame.received)*1000 if frame else None,
                observed_markers=len(frame.markers) if frame else 0,
                units='metres; quaternion xyzw; Motive Y-up')
    data['display_reference'] = ('three-marker plane' if reference else 'tracker')
    if reference:
        data['tracker_to_reference'] = dict(origin_m=reference.origin.tolist(),
                                            basis=reference.basis.tolist())
    return data


def save_diagnostics(path, snapshot, reference=None):
    Path(path).write_text(json.dumps(diagnostics_data(snapshot, reference), indent=2), encoding='utf-8')
