"""Publish signed UI artifacts only. Private keys never enter the repository/bundle."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['init', 'build'])
parser.add_argument('--key', required=True, type=Path)
parser.add_argument('--revision', type=int)
parser.add_argument('--tag')
parser.add_argument('--dist', type=Path, help='Optional isolated frontend asset directory')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
if args.action == 'init':
    args.key.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with os.fdopen(os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
        output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    print(base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode())
else:
    if not args.revision or args.revision < 1 or not args.tag:
        raise SystemExit('--revision (positive integer) and --tag are required')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', args.tag): raise SystemExit('Invalid release tag')
    key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey): raise SystemExit('Expected Ed25519 key')
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    if public != (root / 'frontend/src-tauri/ui-update-public-key.txt').read_text().strip():
        raise SystemExit('Signing key does not match the application public key')
    version = json.loads((root / 'frontend/src-tauri/tauri.conf.json').read_text())['version']
    dist = args.dist.resolve() if args.dist else root / 'frontend/dist'
    if not dist.is_dir(): raise SystemExit('Frontend asset directory missing')
    allowed = {'.html', '.js', '.css', '.json', '.md', '.png', '.webp', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf'}
    paths = [path for path in dist.rglob('*') if path.is_file()]
    if not all(path.suffix.lower() in allowed and not path.is_symlink() and path.resolve().is_relative_to(dist.resolve()) for path in paths): raise SystemExit('Unexpected asset type or path')
    if not all((dist / name).is_file() for name in ('index.html','pet.html','pet-chat.html')): raise SystemExit('Run frontend build first')
    if sum(path.stat().st_size for path in paths) > 40_000_000: raise SystemExit('UI bundle too large')
    output = root / 'build' / f'ui-update-{version}-{args.revision}'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'ui-update.zip'
    hashes = {}
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(paths):
            name = path.relative_to(dist).as_posix()
            if name == 'ui-update.json': raise SystemExit('Reserved asset name')
            content = path.read_bytes()
            if path.suffix in {'.html','.js','.json','.md'} and re.search(rb'(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)',content):
                raise SystemExit('Possible private credential in frontend assets')
            # Development API ports must never be shipped to users.
            if path.suffix in {'.html','.js'} and any(marker in content for marker in (b'127.0.0.1:8011', b'127.0.0.1:8010')):
                raise SystemExit('Development backend URL found; rebuild without VITE_API_URL')
            hashes[name] = hashlib.sha256(content).hexdigest()
            bundle.writestr(name, content)
    payload = json.dumps({'schema':1, 'host_version':version, 'revision':args.revision, 'files':hashes,
        'archive_size':archive.stat().st_size, 'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'url':f'https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/download/{args.tag}/ui-update.zip'},
        ensure_ascii=True, separators=(',',':'), sort_keys=True).encode()
    envelope = {'payload':base64.b64encode(payload).decode(), 'signature':base64.b64encode(key.sign(payload)).decode()}
    (output / 'ui-update.json').write_text(json.dumps(envelope), encoding='utf-8')
    print(f'Signed UI revision {args.revision}, host {version}: {output}')
