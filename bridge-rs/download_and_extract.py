"""Download sherpa-onnx prebuilt libs + sense-voice model with retry."""
import urllib.request, tarfile, os, sys, time, ssl

# Disable SSL verification for mirrors
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

PREBUILT_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.3/sherpa-onnx-v1.13.3-win-x64-static-MT-Release-lib.tar.bz2"
SENSE_VOICE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"

def download_file(url, dest):
    """Download file with retry."""
    print(f"\n=== Downloading {os.path.basename(dest)} ===")
    print(f"URL: {url}")
    
    if os.path.exists(dest):
        print(f"File exists: {os.path.getsize(dest)} bytes, deleting for fresh download")
        os.remove(dest)
    
    # Try direct download
    for attempt in range(3):
        try:
            print(f"Attempt {attempt+1}...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=30, context=ctx)
            total = int(resp.headers.get('Content-Length', 0))
            print(f"Total size: {total:,} bytes")
            
            downloaded = 0
            with open(dest, 'wb') as f:
                while True:
                    chunk = resp.read(256*1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 / total
                        print(f"  {downloaded:,}/{total:,} ({pct:.1f}%)", end='\r', flush=True)
            
            print(f"\n[OK] Downloaded {os.path.getsize(dest):,} bytes")
            return True
        except Exception as e:
            print(f"\n[FAIL] Attempt {attempt+1}: {e}")
            if os.path.exists(dest):
                os.remove(dest)
            time.sleep(2)
    
    return False

def extract_tarbz2(archive, dest_dir):
    """Extract tar.bz2."""
    print(f"\n=== Extracting {os.path.basename(archive)} ===")
    os.makedirs(dest_dir, exist_ok=True)
    with tarfile.open(archive, 'r:bz2') as t:
        members = t.getmembers()
        print(f"Archive has {len(members)} entries:")
        for m in members:
            print(f"  {m.name} ({m.size:,} bytes)")
        t.extractall(dest_dir)
    print(f"[OK] Extracted to {dest_dir}")
    return True

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base)
    
    # 1. Download & extract prebuilt lib (smaller, ~109MB)
    prebuilt_archive = os.path.join(base, "sherpa-onnx-prebuilt.tar.bz2")
    prebuilt_dir = os.path.join(base, "sherpa-onnx-prebuilt-extracted")
    
    if download_file(PREBUILT_URL, prebuilt_archive):
        try:
            extract_tarbz2(prebuilt_archive, prebuilt_dir)
            # Find the lib dir
            lib_dir = None
            for root, dirs, files in os.walk(prebuilt_dir):
                if 'lib' in dirs:
                    lib_dir = os.path.join(root, 'lib')
                    break
            if lib_dir:
                print(f"\n*** SHERPA_ONNX_LIB_DIR = {lib_dir} ***")
        except Exception as e:
            print(f"[FAIL] Extract prebuilt: {e}")
    else:
        print("[FAIL] Could not download prebuilt lib")
    
    # 2. Download & extract sense-voice model (~1GB)
    sv_dir = os.path.join(os.path.dirname(base), "data", "models", "sense-voice")
    sv_archive = os.path.join(sv_dir, "sense-voice.tar.bz2")
    
    if download_file(SENSE_VOICE_URL, sv_archive):
        try:
            extract_tarbz2(sv_archive, sv_dir)
            # Move files from nested dir to sense-voice/
            for root, dirs, files in os.walk(sv_dir):
                for f in files:
                    if f in ('model.int8.onnx', 'tokens.txt'):
                        src = os.path.join(root, f)
                        dst = os.path.join(sv_dir, f)
                        if src != dst:
                            import shutil
                            shutil.move(src, dst)
                            print(f"Moved {f} -> {sv_dir}")
        except Exception as e:
            print(f"[FAIL] Extract sense-voice: {e}")
    else:
        print("[FAIL] Could not download sense-voice model")

if __name__ == "__main__":
    main()
