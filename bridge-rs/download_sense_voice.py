"""Download sense-voice model with resume support and long timeouts."""
import urllib.request, tarfile, os, sys, time, ssl, http.client

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SENSE_VOICE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"

def get_remote_size(url):
    try:
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        return int(resp.headers.get('Content-Length', 0))
    except:
        return 0

def download_with_resume(url, dest):
    """Download with HTTP Range resume, long timeout, retry."""
    total = get_remote_size(url)
    print(f"Remote size: {total:,} bytes")
    
    while True:
        existing = os.path.getsize(dest) if os.path.exists(dest) else 0
        if total > 0 and existing >= total:
            print(f"\n[OK] Already complete: {existing:,} bytes")
            return True
        
        print(f"\nResuming from {existing:,} / {total:,} ({existing*100/total:.1f}%)")
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        if existing > 0:
            headers['Range'] = f'bytes={existing}-'
        
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=120, context=ctx)
            
            mode = 'ab' if existing > 0 and resp.status == 206 else 'wb'
            if mode == 'wb':
                existing = 0
                print("Starting fresh download")
            
            downloaded = existing
            with open(dest, mode) as f:
                while True:
                    chunk = resp.read(512*1024)  # 512KB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 / total
                        print(f"  {downloaded:,}/{total:,} ({pct:.1f}%)", end='\r', flush=True)
            
            print(f"\n[OK] Downloaded {downloaded:,} bytes")
            if total > 0 and downloaded < total:
                print(f"Incomplete, retrying...")
                time.sleep(1)
                continue
            return True
            
        except Exception as e:
            print(f"\n[FAIL] {e}")
            time.sleep(3)

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    sv_dir = os.path.join(os.path.dirname(base), "data", "models", "sense-voice")
    os.makedirs(sv_dir, exist_ok=True)
    sv_archive = os.path.join(sv_dir, "sense-voice.tar.bz2")
    
    if download_with_resume(SENSE_VOICE_URL, sv_archive):
        print("\n=== Extracting sense-voice ===")
        try:
            with tarfile.open(sv_archive, 'r:bz2') as t:
                members = t.getmembers()
                print(f"Archive has {len(members)} entries")
                for m in members[:15]:
                    print(f"  {m.name} ({m.size:,} bytes)")
                t.extractall(sv_dir)
            
            # Move model files to sense-voice/
            import shutil
            for root, dirs, files in os.walk(sv_dir):
                for f in files:
                    if f in ('model.int8.onnx', 'tokens.txt'):
                        src = os.path.join(root, f)
                        dst = os.path.join(sv_dir, f)
                        if src != dst:
                            shutil.move(src, dst)
                            print(f"Moved {f} -> {sv_dir}")
            
            print("[OK] Extraction complete")
            # List final files
            for f in os.listdir(sv_dir):
                fp = os.path.join(sv_dir, f)
                if os.path.isfile(fp):
                    print(f"  {f}: {os.path.getsize(fp):,} bytes")
        except Exception as e:
            print(f"[FAIL] Extract: {e}")

if __name__ == "__main__":
    main()
