import os
import shutil
import zipfile
import glob

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BUILD_DIR)
BUNDLE_DIR = os.path.join(BUILD_DIR, 'bundle')
PYTHON_EMBED_ZIP = os.path.join(BUILD_DIR, 'python-3.12.8-embed-amd64.zip')
WHEELS_DIR = os.path.join(BUILD_DIR, 'wheels')

print("[1/6] Cleaning and preparing bundle directory...")
if os.path.exists(BUNDLE_DIR):
    shutil.rmtree(BUNDLE_DIR, ignore_errors=True)
os.makedirs(BUNDLE_DIR, exist_ok=True)

print("[2/6] Extracting Python 3.12 embeddable zip...")
with zipfile.ZipFile(PYTHON_EMBED_ZIP, 'r') as z:
    z.extractall(BUNDLE_DIR)

print("[3/6] Configuring python312._pth...")
pth_file = os.path.join(BUNDLE_DIR, 'python312._pth')
with open(pth_file, 'r', encoding='utf-8') as f:
    content = f.read()

lines = [l.strip() for l in content.splitlines() if l.strip()]
new_lines = []
for l in lines:
    if l == '#import site':
        new_lines.append('import site')
    elif l != 'import site':
        new_lines.append(l)

if 'Lib\\site-packages' not in new_lines:
    new_lines.append('Lib\\site-packages')
if 'app' not in new_lines:
    new_lines.append('app')
if 'import site' not in new_lines:
    new_lines.append('import site')

with open(pth_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines) + '\n')

print("[4/6] Unpacking wheels into Lib\\site-packages...")
site_packages = os.path.join(BUNDLE_DIR, 'Lib', 'site-packages')
os.makedirs(site_packages, exist_ok=True)

wheel_files = glob.glob(os.path.join(WHEELS_DIR, '*.whl'))
for whl in wheel_files:
    print(f"  Unpacking {os.path.basename(whl)}...")
    with zipfile.ZipFile(whl, 'r') as z:
        z.extractall(site_packages)

print("[5/6] Copying app files (app.py, gui_main.py, templates, cookies)...")
app_dest = os.path.join(BUNDLE_DIR, 'app')
os.makedirs(app_dest, exist_ok=True)

shutil.copy2(os.path.join(PROJECT_DIR, 'app.py'), os.path.join(app_dest, 'app.py'))
shutil.copy2(os.path.join(PROJECT_DIR, 'gui_main.py'), os.path.join(app_dest, 'gui_main.py'))

templates_dest = os.path.join(app_dest, 'templates')
os.makedirs(templates_dest, exist_ok=True)
shutil.copy2(os.path.join(PROJECT_DIR, 'templates', 'index.html'), os.path.join(templates_dest, 'index.html'))

cookies_src = os.path.join(PROJECT_DIR, 'cookies.txt')
if os.path.exists(cookies_src):
    shutil.copy2(cookies_src, os.path.join(app_dest, 'cookies.txt'))

print("[6/6] Writing VERSION stamp (3.0.0)...")
with open(os.path.join(BUNDLE_DIR, 'VERSION'), 'w', encoding='utf-8') as f:
    f.write('3.0.0\n')

print("[7/7] Packaging bundle.zip...")
bundle_zip = os.path.join(BUILD_DIR, 'bundle.zip')
if os.path.exists(bundle_zip):
    os.remove(bundle_zip)
with zipfile.ZipFile(bundle_zip, 'w', compression=zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(BUNDLE_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, BUNDLE_DIR)
            z.write(full_path, rel_path)

print("Bundle preparation complete! Size:", os.path.getsize(bundle_zip), "bytes")
