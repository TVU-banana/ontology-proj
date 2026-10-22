"""Explicit setup command; direct domestic mirror, verified wheel and pinned ONNX."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
from ontology_proj.adapters.ocr_routing import OCR_MODELS

URL='https://mirrors.aliyun.com/pypi/packages/59/07/203065283bba0c5e9c10af3602dc25ab3e59ee54dc2d1d22c8faeabf5ed8/rapidocr-3.10.0-py3-none-any.whl'
SHA='2fc34e26cd0f48514804a94f8832283039c7334076bff60768b1fed9ad068050'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--wheel',type=Path,help='Use a previously downloaded wheel without networking')
    args=ap.parse_args()
    root=args.output.expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    wheel=args.wheel.expanduser().resolve() if args.wheel else root/'rapidocr-3.10.0-py3-none-any.whl'
    if args.wheel and not wheel.is_file():raise ValueError('Explicit wheel does not exist')
    if not wheel.exists():
        temp=wheel.with_suffix('.partial')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL,timeout=60) as response,temp.open('wb') as stream:
            while chunk:=response.read(1024*1024):stream.write(chunk)
        if hashlib.sha256(temp.read_bytes()).hexdigest()!=SHA:raise ValueError('Mirror wheel hash mismatch')
        temp.replace(wheel)
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=SHA:raise ValueError('Cached wheel hash mismatch')
    rows=[]
    with zipfile.ZipFile(wheel) as archive:
        for task,(name,sha) in OCR_MODELS.items():
            content=archive.read('rapidocr/models/'+name)
            if hashlib.sha256(content).hexdigest()!=sha:raise ValueError('Packaged model hash mismatch')
            path=root/name
            if path.exists() and path.read_bytes()!=content:raise ValueError('Existing model differs; refuse overwrite')
            if not path.exists():path.write_bytes(content)
            rows.append({'task':task,'file':name,'bytes':len(content),'sha256':sha})
    manifest=root/'assets-manifest.json';payload=json.dumps({'source':URL,'wheel_sha256':SHA,'models':rows},indent=2)+'\n'
    if manifest.exists() and manifest.read_text()!=payload:raise ValueError('Existing manifest differs; refuse overwrite')
    manifest.write_text(payload);print(f'Verified {len(rows)} models in {root}')

if __name__=='__main__':main()
