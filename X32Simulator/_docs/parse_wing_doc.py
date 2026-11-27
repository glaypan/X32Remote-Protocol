"""把《WING Remote Protocols》markdown 里的通道地址表解析为 JSON。

脚注引用 <a href="#fnN" class="footnote-ref">N</a> 必须在去标签前整体删除，
否则地址会被吃成 /dyn/thr6（真地址是 /dyn/thr）。
"""
import html
import json
import re

SRC = "WingRemoteProtocols.md"
OUT = "wing_raw_addrs.json"

SECTIONS = [
    ("ch",    "### Channel Settings", "### Aux Settings"),
    ("auxin", "### Aux Settings",     "### Bus Settings"),
    ("bus",   "### Bus Settings",     "### Mains Settings"),
    ("main",  "### Mains Settings",   "### Matrix Settings"),
    ("mtx",   "### Matrix Settings",  "### DCA Settings"),
]

_FNREF = re.compile(r'<a[^>]*class="footnote-ref"[^>]*>.*?</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def clean(cell):
    s = _FNREF.sub("", cell)
    s = _TAG.sub("", s)
    return html.unescape(s).replace("\u2011", "-").strip()


def parse_rows(seg):
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", seg, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        if len(cells) < 5:
            continue
        addr = clean(cells[0])
        if not addr.startswith("/"):
            continue
        rows.append({
            "a": addr,
            "t": clean(cells[1]),
            "r": clean(cells[2]),
            "x": clean(cells[3]),
            "d": clean(cells[4]),
        })
    return rows


def main():
    lines = open(SRC, encoding="utf-8").read().splitlines()
    out = {}
    for name, head, nxt in SECTIONS:
        i = next(k for k, l in enumerate(lines) if l.strip() == head)
        j = next(k for k, l in enumerate(lines) if l.strip() == nxt)
        out[name] = parse_rows("\n".join(lines[i:j]))
        print("%-6s %d 条" % (name, len(out[name])))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已写", OUT)


if __name__ == "__main__":
    main()
