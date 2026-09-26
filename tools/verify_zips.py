import zipfile, hashlib, os, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")

for name in ("X32Remote-v1.1.0-full.zip", "X32Remote-v1.1.0-assets.zip"):
    p = os.path.join(OUT, name)
    z = zipfile.ZipFile(p)
    bad = z.testzip()
    ipa = [n for n in z.namelist() if n.endswith(".ipa")]
    apk = [n for n in z.namelist() if n.endswith(".apk")]
    size = os.path.getsize(p)
    print("%s" % name)
    print("  items   : %d" % len(z.namelist()))
    print("  CRC     : %s" % ("OK" if bad is None else "BAD " + str(bad)))
    print("  size    : %.2f MB" % (size / 1024 / 1024))
    print("  IPA     : %s" % ipa)
    print("  APK     : %s" % apk)
    print("  sha256  : %s" % hashlib.sha256(open(p, "rb").read()).hexdigest())
    print()
