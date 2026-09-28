import pathlib
import shutil

for p in pathlib.Path('.').rglob('__pycache__'):
    shutil.rmtree(str(p), ignore_errors=True)
    print(f"Removed {p}")

print("Cache cleaned successfully")
