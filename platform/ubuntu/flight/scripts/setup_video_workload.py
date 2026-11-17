"""Download pinned official assets, verifying complete SHA256 before installation."""
import hashlib
import os
from pathlib import Path
import urllib.request

root = Path(os.environ.get('FLIGHT_VISION_ASSETS', '/tmp/lowair-vision-assets'))
root.mkdir(parents=True, exist_ok=True)
assets = [
    ('vtest.avi', 'https://raw.githubusercontent.com/opencv/opencv/4.11.0/samples/data/vtest.avi',
     '45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf'),
    ('ssdlite320_mobilenet_v3_large_coco-a79551df.pth',
     'https://download.pytorch.org/models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth',
     'a79551df90c79834bcd3bb3845ef9d966b5449a3a9b2833ae8404778ca5d65d2'),
]
for name, url, checksum in assets:
    target = root / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == checksum:
        print('Verified', target)
        continue
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(40 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError('Asset SHA256 mismatch: ' + name)
    temporary = target.with_suffix('.download')
    temporary.write_bytes(data)
    temporary.replace(target)
    print('Installed and verified', target)
