# ============================================
# FILE: nas2gp/events.py
# CHANGES: Add Pub/Sub mechanism + new GUI events
# ============================================

import traceback
from collections import defaultdict

APP_START = "APP_START"
APP_FINISHED = "APP_FINISHED"

SCAN_START = "SCAN_START"
SCAN_FINISHED = "SCAN_FINISHED"

UPLOAD_START = "UPLOAD_START"
UPLOAD_FINISHED = "UPLOAD_FINISHED"

GOOGLE_WAIT_START = "GOOGLE_WAIT_START"
GOOGLE_WAIT_FINISHED = "GOOGLE_WAIT_FINISHED"

DELETE_START = "DELETE_START"
DELETE_FINISHED = "DELETE_FINISHED"

ERROR = "ERROR"

BACKUP_RULES_TICK = "BACKUP_RULES_TICK"
UPLOAD_PROGRESS_TICK = "UPLOAD_PROGRESS_TICK"
SCAN_PROGRESS_TICK = "SCAN_PROGRESS_TICK"
EXEC_CONTROL_CHECK = "EXEC_CONTROL_CHECK"
SYNC_PHASE_CHANGED = "SYNC_PHASE_CHANGED"

ADB_DEVICE_OFFLINE_PERSISTENT = "ADB_DEVICE_OFFLINE_PERSISTENT"
ADB_DEVICE_UNAUTHORIZED = "ADB_DEVICE_UNAUTHORIZED"
ADB_DEVICE_RECOVERED = "ADB_DEVICE_RECOVERED"
ADB_DEVICE_UNRECOVERABLE = "ADB_DEVICE_UNRECOVERABLE"
DEVICE_STORAGE_INSUFFICIENT = "DEVICE_STORAGE_INSUFFICIENT"
DEVICE_STORAGE_RECOVERED = "DEVICE_STORAGE_RECOVERED"

_SUBSCRIBERS = defaultdict(list)


def subscribe(event_name, fn):
    _SUBSCRIBERS[event_name].append(fn)


def unsubscribe(event_name, fn):
    try:
        _SUBSCRIBERS[event_name].remove(fn)
    except (ValueError, KeyError):
        pass


def publish(event_name, data=None):
    for fn in list(_SUBSCRIBERS.get(event_name, [])):
        try:
            fn(event_name, data)
        except Exception:
            traceback.print_exc()
