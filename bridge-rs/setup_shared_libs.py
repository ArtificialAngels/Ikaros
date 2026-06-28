"""Download shared sherpa-onnx libs and create MinGW import libraries."""
import urllib.request, tarfile, os, ssl, subprocess, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.3/sherpa-onnx-v1.13.3-win-x64-shared-MT-Release-lib.tar.bz2"
BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(BASE, "sherpa-onnx-shared.tar.bz2")
EXTRACT_DIR = os.path.join(BASE, "sherpa-onnx-shared-extracted")
MINGW_LIB_DIR = os.path.join(EXTRACT_DIR, "mingw-lib")

def download():
    if os.path.exists(ARCHIVE):
        print(f"Archive exists: {os.path.getsize(ARCHIVE)} bytes")
        return
    print(f"Downloading {URL}")
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
    total = int(resp.headers.get('Content-Length', 0))
    print(f"Total: {total:,} bytes")
    with open(ARCHIVE, 'wb') as f:
        while True:
            chunk = resp.read(256*1024)
            if not chunk: break
            f.write(chunk)
    print(f"Downloaded: {os.path.getsize(ARCHIVE):,} bytes")

def extract():
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    print(f"Extracting {ARCHIVE}")
    with tarfile.open(ARCHIVE, 'r:bz2') as t:
        for m in t.getmembers():
            print(f"  {m.name}")
        t.extractall(EXTRACT_DIR)
    print("Extracted OK")

def find_lib_dir():
    """Find the lib/ directory in extracted archive."""
    for root, dirs, files in os.walk(EXTRACT_DIR):
        if 'lib' in dirs:
            lib_dir = os.path.join(root, 'lib')
            dll_files = [f for f in os.listdir(lib_dir) if f.endswith('.dll')]
            lib_files = [f for f in os.listdir(lib_dir) if f.endswith('.lib')]
            print(f"Found lib dir: {lib_dir}")
            print(f"  DLLs: {dll_files}")
            print(f"  LIBs: {lib_files}")
            return lib_dir, dll_files
    return None, []

def create_mingw_import_libs(lib_dir, dll_files):
    """Use gendef + dlltool to create MinGW .a import libs from DLLs."""
    os.makedirs(MINGW_LIB_DIR, exist_ok=True)
    
    for dll in dll_files:
        dll_path = os.path.join(lib_dir, dll)
        def_file = os.path.join(MINGW_LIB_DIR, dll.replace('.dll', '.def'))
        a_file = os.path.join(MINGW_LIB_DIR, 'lib' + dll.replace('.dll', '.a'))
        
        # Step 1: gendef to create .def from DLL
        print(f"\ngendef {dll} -> {os.path.basename(def_file)}")
        result = subprocess.run(
            ['gendef', '-', dll_path],
            capture_output=True, text=True, cwd=MINGW_LIB_DIR
        )
        if result.stdout:
            with open(def_file, 'w') as f:
                f.write(result.stdout)
        elif result.returncode != 0:
            print(f"  gendef failed: {result.stderr}")
            continue
        
        if not os.path.exists(def_file) or os.path.getsize(def_file) < 10:
            # Try alternate gendef syntax
            result2 = subprocess.run(
                ['gendef', dll_path],
                capture_output=True, text=True, cwd=MINGW_LIB_DIR
            )
            if result2.returncode != 0:
                print(f"  gendef alt failed: {result2.stderr}")
                continue
        
        # Step 2: dlltool to create .a from .def
        print(f"dlltool {os.path.basename(def_file)} -> {os.path.basename(a_file)}")
        result = subprocess.run(
            ['dlltool', '-d', def_file, '-l', a_file, '-D', dll],
            capture_output=True, text=True, cwd=MINGW_LIB_DIR
        )
        if result.returncode != 0:
            print(f"  dlltool failed: {result.stderr}")
        else:
            print(f"  Created: {os.path.basename(a_file)} ({os.path.getsize(a_file):,} bytes)")

    # Also copy DLLs to MINGW_LIB_DIR (needed at runtime)
    for dll in dll_files:
        src = os.path.join(lib_dir, dll)
        dst = os.path.join(MINGW_LIB_DIR, dll)
        import shutil
        shutil.copy2(src, dst)
    
    print(f"\n*** MINGW_LIB_DIR = {MINGW_LIB_DIR} ***")
    for f in sorted(os.listdir(MINGW_LIB_DIR)):
        fp = os.path.join(MINGW_LIB_DIR, f)
        print(f"  {f}: {os.path.getsize(fp):,} bytes")

if __name__ == "__main__":
    download()
    extract()
    lib_dir, dlls = find_lib_dir()
    if lib_dir and dlls:
        create_mingw_import_libs(lib_dir, dlls)
    else:
        print("No DLLs found!")
