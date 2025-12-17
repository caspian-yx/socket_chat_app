#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证语音通话修复是否生效"""
import sys
import inspect
from client.features.voice import VoiceManager

# 检查 initiate_call 方法
source = inspect.getsource(VoiceManager.initiate_call)

if "self.session.build_headers()" in source:
    print("[OK] Fix is effective: initiate_call uses session.build_headers()")
elif '"version": DEFAULT_VERSION' in source or '{"version": DEFAULT_VERSION}' in source:
    print("[ERROR] Fix NOT effective: initiate_call still uses hardcoded version")
    print("\nPossible reasons:")
    print("1. Python cache not cleared")
    print("2. Client process not fully restarted")
    print("\nSolution:")
    print("1. Close ALL client windows")
    print("2. Kill any python.exe processes")
    print("3. Restart client")
else:
    print("[WARN] Cannot determine fix status")

print("\nCurrent initiate_call method (first 25 lines):")
print("=" * 60)
for i, line in enumerate(source.split('\n')[:25], 1):
    if 'headers' in line.lower():
        print(f">>> {i:3d}: {line}")
    else:
        print(f"    {i:3d}: {line}")
