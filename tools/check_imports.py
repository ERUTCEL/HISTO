"""EXE 안에 없는 모듈을 import하지 않는지 검사합니다.
사용: python3.10 check_imports.py <PYZ.pyz_extracted 폴더> <base_library.zip> <src 폴더>
(새 모듈을 쓰면 repack.py가 직접 넣어 주는 것: 앱 모듈, tkinter.font)"""
import ast, sys, zipfile
from pathlib import Path
pyz, base, src = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
have = {p.relative_to(pyz).with_suffix('').as_posix().replace('/', '.').removesuffix('.__init__') for p in pyz.rglob('*.pyc')}
have |= {n[:-4].replace('/', '.').removesuffix('.__init__') for n in zipfile.ZipFile(base).namelist() if n.endswith('.pyc')}
have |= set(sys.builtin_module_names) | {p.stem for p in src.glob('*.py')} | {'tkinter.font'}
bad = []
for f in sorted(src.glob('*.py')):
    for node in ast.walk(ast.parse(f.read_text(encoding='utf-8'))):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
        for n in names:
            if n and n not in have and n.split('.')[0] not in ('tkinter',) and not any(n == h or h.startswith(n + '.') for h in have):
                bad.append(f'{f.name}: {n}')
            elif n and n.startswith('tkinter.') and n not in have:
                bad.append(f'{f.name}: {n}')
print('\n'.join(bad) or 'OK: 모든 import가 EXE 안에 있습니다')
sys.exit(1 if bad else 0)
