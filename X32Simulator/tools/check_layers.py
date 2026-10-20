#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 x32sim 包内**顶层**相对导入是否有环 (延迟导入不算)。"""
import ast
import os
import sys

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "x32sim")


def top_imports(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    deps = set()
    for node in tree.body:                      # 只看顶层 (函数体内的延迟导入不算)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            deps.add(node.module)
    return deps


def main():
    mods = sorted(f[:-3] for f in os.listdir(PKG)
                  if f.endswith(".py") and f != "__init__.py" and f != "__main__.py")
    graph = {m: top_imports(os.path.join(PKG, m + ".py")) & set(mods) for m in mods}

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {m: WHITE for m in mods}
    stack = []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in sorted(graph[u]):
            if color[v] == GRAY:
                cyc = stack[stack.index(v):] + [v]
                print("!! 循环导入: " + " -> ".join(cyc))
                return False
            if color[v] == WHITE and not dfs(v):
                return False
        stack.pop()
        color[u] = BLACK
        return True

    ok = all(dfs(m) for m in mods if color[m] == WHITE)
    if ok:
        print("无环 ✓  (%d 个模块)" % len(mods))
        for m in mods:
            if graph[m]:
                print("  %-12s -> %s" % (m, ", ".join(sorted(graph[m]))))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
