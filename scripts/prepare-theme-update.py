"""Overlay theme CSS/assets on a verified existing UI bundle, without changing JS."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--baseline', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
baseline = args.baseline.resolve()
output = args.output.resolve()
if not output.is_relative_to(root / 'build') or output == root / 'build' or output.exists():
    raise SystemExit('Output must be a new directory inside project build/')
envelope = json.loads((baseline / 'ui-update.json').read_text())
payload = base64.b64decode(envelope['payload'], validate=True)
public = base64.b64decode((root / 'frontend/src-tauri/ui-update-public-key.txt').read_text().strip())
Ed25519PublicKey.from_public_bytes(public).verify(base64.b64decode(envelope['signature'], validate=True), payload)
manifest = json.loads(payload)
version = json.loads((root / 'frontend/src-tauri/tauri.conf.json').read_text())['version']
if manifest['host_version'] != version:
    raise SystemExit('Baseline host version mismatch')
archive = baseline / 'ui-update.zip'
if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
    raise SystemExit('Baseline archive hash mismatch')
with zipfile.ZipFile(archive) as bundle:
    if len(bundle.infolist()) != len(manifest['files']):
        raise SystemExit('Baseline file list mismatch')
    checked = {}
    for item in bundle.infolist():
        name = item.filename
        if name.startswith('/') or '\\' in name or ':' in name or any(part in ('', '.', '..') for part in name.split('/')) or name in checked:
            raise SystemExit('Unsafe baseline path')
        data = bundle.read(item)
        if len(data) > 40_000_000 or hashlib.sha256(data).hexdigest() != manifest['files'].get(name):
            raise SystemExit('Baseline file hash mismatch')
        checked[name] = data
if sum(map(len, checked.values())) > 40_000_000:
    raise SystemExit('Baseline too large')
output.mkdir(parents=True)
for name, data in checked.items():
    path = output / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
html = (output / 'index.html').read_text()
style = re.search(r'<link rel="stylesheet"[^>]*href="/?([^" ]+)"', html)
if not style:
    raise SystemExit('Baseline main stylesheet missing')
css_file = output / style[1]
theme = (root / 'frontend/src/WorkspaceSunlight.css').read_text()
theme = theme.replace("./assets/themes/sunlight-studio.png", "./sunlight-studio.png")
# The older UI's decorative markup is retained but hidden. Use noninteractive
# pseudo-layers for this theme only; no script, API or plugin changes.
compatibility = '''
.workspace-tide-scene .violet-horizon { background:transparent; }
.workspace-tide-scene .violet-aurora,.workspace-tide-scene .violet-stars,
.workspace-tide-scene .violet-water,.workspace-tide-scene .violet-sculpture { display:none; }
.workspace-tide-scene::before { content:""; position:absolute; inset:-12%; pointer-events:none; opacity:.3; background:repeating-linear-gradient(90deg,transparent 0 18%,#fffef952 18% 30%,transparent 30% 48%); }
.workspace-tide-scene[data-motion="full"]::before { animation:sunlight-drift 14s ease-in-out infinite; }
.workspace-tide-scene[data-motion="light"]::before { animation:sunlight-drift-light 20s ease-in-out infinite; }
.workspace-chat-canvas:has(textarea:focus) .workspace-tide-scene::before,
.workspace-tide-scene[data-visible="false"]::before { animation-play-state:paused !important; }
.shell.workspace:has(.workspace-tide-scene[data-motion="off"]) .form-page { animation:none; }
@media(prefers-reduced-motion:reduce) { .workspace-tide-scene::before { animation:none !important; } }
'''
with css_file.open('a', encoding='utf-8') as destination:
    destination.write('\n' + theme + '\n' + compatibility)
(css_file.parent / 'sunlight-studio.png').write_bytes((root / 'frontend/src/assets/themes/sunlight-studio.png').read_bytes())
for name, data in checked.items():
    if Path(name).suffix == '.js' and (output / name).read_bytes() != data:
        raise SystemExit('Unexpected JavaScript modification')
print(f'Theme-only assets prepared: {output}; baseline revision {manifest["revision"]}; all JavaScript unchanged')
