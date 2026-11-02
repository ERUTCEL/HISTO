"""EXE 파일 아이콘 바꾸기: 원래 아이콘 자리(크기·색 깊이 그대로)에 새 그림을 덮어씁니다.

python tools/set_icon.py HISTRO.exe assets/histro-icon.png
- 256px 칸은 PNG, 48/32/16px 칸은 원래 형식(8비트 팔레트 또는 32비트 투명도)으로 만들어 넣습니다.
- 새 그림이 원래 칸보다 크면 멈춥니다(파일 배치를 바꾸지 않기 위해).
"""
import struct
import sys

import pefile
from PIL import Image


def _mask(img, w, h):
    row = ((w + 31) // 32) * 4
    out = bytearray()
    for y in range(h - 1, -1, -1):
        bits = bytearray(row)
        for x in range(w):
            if img.getpixel((x, y))[3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)
        out += bits
    return bytes(out)


def dib32(img):
    w, h = img.size
    px = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = img.getpixel((x, y))
            px += bytes((b, g, r, a))
    return struct.pack('<IiiHHIIiiII', 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0) + bytes(px) + _mask(img, w, h)


def dib8(img):
    w, h = img.size
    flat = Image.new('RGB', img.size, (255, 255, 255))
    flat.paste(img, (0, 0), img)
    q = flat.quantize(256)
    pal = q.getpalette()[:768] + [0] * (768 - len(q.getpalette()[:768]))
    palette = b''.join(bytes((pal[i + 2], pal[i + 1], pal[i], 0)) for i in range(0, 768, 3))
    row = (w + 3) // 4 * 4
    px = bytearray()
    for y in range(h - 1, -1, -1):
        line = bytes(q.getpixel((x, y)) for x in range(w))
        px += line + b'\0' * (row - w)
    return struct.pack('<IiiHHIIiiII', 40, w, h * 2, 1, 8, 0, 0, 0, 0, 256, 0) + palette + bytes(px) + _mask(img, w, h)


def main(exe, png):
    src = Image.open(png).convert('RGBA')
    pe = pefile.PE(exe)
    icons, group = {}, None
    for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        for n in t.directory.entries:
            leaf = n.directory.entries[0].data.struct
            if t.id == 3:
                icons[n.id] = leaf
            elif t.id == 14 and group is None:
                group = leaf
    gdata = bytearray(pe.get_data(group.OffsetToData, group.Size))
    count = struct.unpack('<H', gdata[4:6])[0]
    for i in range(count):
        at = 6 + 14 * i
        w, h, _, _, _, bpp, size, ident = struct.unpack('<BBBBHHIH', gdata[at:at + 14])
        n = w or 256
        img = src.resize((n, n), Image.LANCZOS)
        if n == 256:
            import io
            buf = io.BytesIO()
            img.save(buf, 'PNG', optimize=True)
            blob = buf.getvalue()
        else:
            blob = dib32(img) if bpp == 32 else dib8(img)
        leaf = icons[ident]
        if len(blob) > leaf.Size:
            sys.exit(f'{n}px {bpp}bit 아이콘이 원래 칸보다 큽니다({len(blob)} > {leaf.Size})')
        pe.set_bytes_at_rva(leaf.OffsetToData, blob + b'\0' * (leaf.Size - len(blob)))
        leaf.Size = len(blob)
        struct.pack_into('<I', gdata, at + 8, len(blob))
    pe.set_bytes_at_rva(group.OffsetToData, bytes(gdata))
    data = pe.write()
    open(exe, 'wb').write(data)
    print(f'icon set: {count} sizes')


if __name__ == '__main__':
    main(*sys.argv[1:3])
