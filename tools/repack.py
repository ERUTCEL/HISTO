"""Rebuild the PyInstaller onefile EXE with redesigned UI modules.

Run with Python 3.10 (bytecode must match the bundled python310.dll):
    python3.10 repack.py <original.exe> <src_dir> <out.exe>

Everything in the original archive is copied byte-for-byte except:
  - 'app' (entry script)                   -> src_dir/app.py
  - PYZ: ui_theme, result_viewer           -> src_dir/*.py
  - PYZ: + ranker (new), + tkinter.font (stdlib, was not bundled before)
  - + fonts/*.otf and fonts/*.txt as data files (extracted next to the modules)
"""
import marshal
from pathlib import Path
import struct
import sys
import tkinter
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carchive  # noqa: E402

assert sys.version_info[:2] == (3, 10), 'run with Python 3.10'
src_exe, src_dir, out_exe = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])


def code_of(path, filename):
    return compile(Path(path).read_text(encoding='utf-8'), filename, 'exec', dont_inherit=True, optimize=0)


data, start, end, cookie, toc = carchive.read(src_exe)
assert end == len(data), 'unexpected trailing data after archive'

# ── PYZ ──
z = next(e for e in toc if e['typ'] == 'z')
pyz = data[start + z['pos']:start + z['pos'] + z['clen']]
header = pyz[:17]
assert header[:4] == b'PYZ\0'
toc_off = struct.unpack('!i', pyz[8:12])[0]
items = marshal.loads(pyz[toc_off:])
names = [n for n, _ in items]
APP_MODULES = ('ui_theme', 'result_viewer', 'ranker', 'textlab', 'textlab_ui', 'mtl')  # 바꾸거나 새로 넣을 앱 모듈
replace, add = {}, {}
for mod in APP_MODULES:
    entry = (0, code_of(src_dir / f'{mod}.py', f'{mod}.py'))
    (replace if mod in names else add)[mod] = entry
add['tkinter.font'] = (0, code_of(Path(tkinter.__file__).with_name('font.py'), 'tkinter\\font.py'))
new_items, blobs, pos = [], [], len(header)
order = list(items)
for n in sorted(add):
    assert n not in names
    at = next((i for i, (m, _) in enumerate(order) if m > n), len(order))
    order.insert(at, (n, None))
for name, entry in order:
    if name in replace or name in add:
        typ, co = (replace.get(name) or add[name])
        blob = zlib.compress(marshal.dumps(co), 9)
    else:
        typ, o, l = entry
        blob = pyz[o:o + l]
    new_items.append((name, (typ, pos, len(blob))))
    blobs.append(blob)
    pos += len(blob)
new_pyz = bytearray(header) + b''.join(blobs)
struct.pack_into('!i', new_pyz, 8, len(new_pyz))
new_pyz += marshal.dumps(new_items)
new_pyz = bytes(new_pyz)

# ── CArchive ──
app_code = code_of(src_dir / 'app.py', 'app.py')
extra = sorted((src_dir / 'fonts').glob('*'))
entries = []  # (name, flag, typ, raw_bytes, ulen)
for e in toc:
    raw = data[start + e['pos']:start + e['pos'] + e['clen']]
    if e['name'] == 'app':
        u = marshal.dumps(app_code)
        entries.append(('app', 1, 's', zlib.compress(u, 9), len(u)))
    elif e['typ'] == 'z':
        entries.append((e['name'], 0, 'z', new_pyz, len(new_pyz)))
    else:
        entries.append((e['name'], e['flag'], e['typ'], raw, e['ulen']))
insert_at = next(i for i, e in enumerate(entries) if e[0] == 'queries.json')
for f in extra:
    u = f.read_bytes()
    entries.insert(insert_at, ('fonts\\' + f.name, 1, 'b', zlib.compress(u, 9), len(u)))
    insert_at += 1

body, toc_bytes = bytearray(), bytearray()
for name, flag, typ, raw, ulen in entries:
    nm = name.encode('utf-8') + b'\0'
    size = 18 + len(nm)
    size += (-size) % 16  # PyInstaller pads TOC entries to 16 bytes
    nm = nm.ljust(size - 18, b'\0')
    toc_bytes += struct.pack('!IIIIBc', size, len(body), len(raw), ulen, flag, typ.encode()) + nm
    body += raw
cookie_len = struct.calcsize(carchive.COOKIE)
pkg_len = len(body) + len(toc_bytes) + cookie_len
pkg = bytes(body) + bytes(toc_bytes) + struct.pack(carchive.COOKIE, carchive.MAGIC, pkg_len, len(body),
                                                   len(toc_bytes), cookie['pyver'], cookie['pylib'])
out_exe.write_bytes(data[:start] + pkg)
print(f'wrote {out_exe} ({out_exe.stat().st_size:,} bytes); pyz {len(pyz):,} -> {len(new_pyz):,}; +{len(extra)} files')
