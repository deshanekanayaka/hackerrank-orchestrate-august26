"""Loads every dataset/*.csv into pandas DataFrames, all columns as strings.

Keeping everything as strings avoids pandas guessing types on ID-like columns
(e.g. treating a message_id as an int, or dropping leading zeros) and keeps
join keys byte-identical across files.
"""

import os
import pandas as pd

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "dataset"))

_FILES = {
    "messages": "messages.csv",
    "users": "users.csv",
    "groups": "groups.csv",
    "group_members": "group_members.csv",
    "business_accounts": "business_accounts.csv",
    "user_business_history": "user_business_history.csv",
    "message_history": "message_history.csv",
    "message_events": "message_events.csv",
    "images": "images.csv",
    "voice_notes": "voice_notes.csv",
    "daily_notification_summary": "daily_notification_summary.csv",
    "sample_messages": "sample_messages.csv",
}


def load_all(dataset_dir: str = DATASET_DIR) -> dict[str, pd.DataFrame]:
    data = {}
    for key, filename in _FILES.items():
        path = os.path.join(dataset_dir, filename)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        data[key] = df
    return data
