
import os
import shutil
import sys

print(f"PATH: {os.environ.get('PATH')}")
print(f"shutil.which('npm'): {shutil.which('npm')}")
print(f"shutil.which('npm.cmd'): {shutil.which('npm.cmd')}")
print(f"shutil.which('npm.ps1'): {shutil.which('npm.ps1')}")

npm_path = shutil.which("npm.cmd")
if not npm_path:
    # Try looking in typical Trae locations if not in PATH
    trae_npm = os.path.expanduser(r"~/.trae/sdks/versions/node/current/npm.cmd")
    if os.path.exists(trae_npm):
        print(f"Found npm at Trae location: {trae_npm}")
    else:
        print("npm.cmd not found in Trae location")
