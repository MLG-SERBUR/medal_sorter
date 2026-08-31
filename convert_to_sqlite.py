import json
import sqlite3
import sys
from pathlib import Path

def convert():
    json_path = Path('clip_data.json')
    sqlite_path = Path('clip_data.sqlite')
    
    print(f"Reading {json_path}...")
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print("Removing bounding boxes...")
    records = data.get('records', {})
    for path, record in records.items():
        # Remove from observations
        try:
            observations = record['analysis']['usernames']['ocr']['observations']
            for obs in observations:
                obs.pop('bounding_box', None)
        except KeyError:
            pass
        
        # Remove from resolution evidence
        try:
            resolution = record['analysis']['usernames']['resolution']
            # resolution has {"usernames": [{"name":..., "evidence": [...]}]}
            usernames = resolution.get("usernames", []) if isinstance(resolution, dict) else []
            for user_data in usernames:
                if isinstance(user_data, dict) and 'evidence' in user_data:
                    for ev in user_data['evidence']:
                        if isinstance(ev, dict):
                            ev.pop('bounding_box', None)
        except (KeyError, AttributeError):
            pass

    print(f"Creating {sqlite_path}...")
    conn = sqlite3.connect(sqlite_path)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS records (clip_path TEXT PRIMARY KEY, data TEXT)')

    if 'schema_version' in data:
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('schema_version', str(data['schema_version'])))
    else:
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('schema_version', '1'))
    if 'last_discovery_at' in data:
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('last_discovery_at', data['last_discovery_at']))
    if 'medal_root' in data:
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('medal_root', data['medal_root']))

    print("Inserting records...")
    for path, record in records.items():
        c.execute('INSERT OR REPLACE INTO records VALUES (?, ?)', (path, json.dumps(record)))
        
    conn.commit()
    conn.close()
    print("Conversion complete.")

if __name__ == '__main__':
    convert()
