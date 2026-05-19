import json

PRINTS_FILE = '/tmp/school_prints.json'
PRINT_SESSION_FILE = '/tmp/print_sessions.json'


def load_prints():
    try:
        with open(PRINTS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_prints(data):
    try:
        with open(PRINTS_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"prints save error: {e}")


def load_print_sessions():
    try:
        with open(PRINT_SESSION_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def save_print_sessions(data):
    try:
        with open(PRINT_SESSION_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"print_sessions save error: {e}")
