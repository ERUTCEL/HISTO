import struct, zlib, marshal, sys
MAGIC = b'MEI\014\013\012\013\016'
COOKIE = '!8sIIii64s'

def read(path):
    data = open(path, 'rb').read()
    pos = data.rfind(MAGIC)
    magic, pkglen, tocpos, toclen, pyver, pylib = struct.unpack(COOKIE, data[pos:pos + struct.calcsize(COOKIE)])
    end = pos + struct.calcsize(COOKIE)
    start = end - pkglen
    toc = []
    p = start + tocpos
    while p < start + tocpos + toclen:
        size, epos, clen, ulen, flag, typ = struct.unpack('!IIIIBc', data[p:p + 18])
        name = data[p + 18:p + size].rstrip(b'\0').decode('utf-8')
        toc.append(dict(name=name, pos=epos, clen=clen, ulen=ulen, flag=flag, typ=typ.decode()))
        p += size
    return data, start, end, dict(pkglen=pkglen, tocpos=tocpos, toclen=toclen, pyver=pyver, pylib=pylib), toc

if __name__ == '__main__':
    data, start, end, cookie, toc = read(sys.argv[1])
    print('file', len(data), 'pkg start', start, 'end', end, 'trailing', len(data) - end, cookie)
    from collections import Counter
    print(Counter(e['typ'] for e in toc))
    for e in toc:
        if e['typ'] not in ('b', 'x') or e['name'].endswith(('.json',)) : print(e)
