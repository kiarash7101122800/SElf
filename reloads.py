"""
SElf - reloads.py (Optimized & Safe)
Handles Enemy and Mute lists with safe file handling for Railway deployment.
"""
import os

DATA_DIR = "data"
ENEMY_FILE = os.path.join(DATA_DIR, "Enemy.txt")
MUTE_FILE = os.path.join(DATA_DIR, "Mute.txt")

def _ensure_file(path):
    """Ensure file exists"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
    except Exception as e:
        print(f"[reloads] ensure file error {path}: {e}")

def Enm():
    """Return list of enemy user IDs - safe version"""
    _ensure_file(ENEMY_FILE)
    Enemys = []
    try:
        with open(ENEMY_FILE, "r", encoding="utf-8") as file:
            lines = file.read().split("\n")
            for i in lines:
                i = i.strip()
                if i:
                    try:
                        Enemys.append(int(i))
                    except ValueError:
                        continue
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[reloads] Enm error: {e}")
        return []
    return Enemys

def Mute():
    """Return list of muted user IDs - safe version"""
    _ensure_file(MUTE_FILE)
    Mutes = []
    try:
        with open(MUTE_FILE, "r", encoding="utf-8") as file:
            lines = file.read().split("\n")
            for i in lines:
                i = i.strip()
                if i:
                    try:
                        Mutes.append(int(i))
                    except ValueError:
                        continue
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[reloads] Mute error: {e}")
        return []
    return Mutes

def add_enemy(user_id):
    _ensure_file(ENEMY_FILE)
    try:
        with open(ENEMY_FILE, "a", encoding="utf-8") as f:
            f.write(f"{user_id}\n")
        return True
    except Exception as e:
        print(f"[reloads] add_enemy error: {e}")
        return False

def remove_enemy(user_id):
    _ensure_file(ENEMY_FILE)
    try:
        target = f"{user_id}\n"
        with open(ENEMY_FILE, "r", encoding="utf-8") as file:
            lines = file.readlines()
        new_lines = [l for l in lines if l.strip() != str(user_id) and l != target]
        with open(ENEMY_FILE, "w", encoding="utf-8") as file:
            file.writelines(new_lines)
        return True
    except Exception as e:
        print(f"[reloads] remove_enemy error: {e}")
        return False

def add_mute(user_id):
    _ensure_file(MUTE_FILE)
    try:
        with open(MUTE_FILE, "a", encoding="utf-8") as f:
            f.write(f"{user_id}\n")
        return True
    except Exception as e:
        print(f"[reloads] add_mute error: {e}")
        return False

def remove_mute(user_id):
    _ensure_file(MUTE_FILE)
    try:
        with open(MUTE_FILE, "r", encoding="utf-8") as file:
            lines = file.readlines()
        new_lines = [l for l in lines if l.strip() != str(user_id)]
        with open(MUTE_FILE, "w", encoding="utf-8") as file:
            file.writelines(new_lines)
        return True
    except Exception as e:
        print(f"[reloads] remove_mute error: {e}")
        return False
