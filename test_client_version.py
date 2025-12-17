# -*- coding: utf-8 -*-
"""测试客户端是否加载了正确的代码"""
import sys
import time

print("=" * 60)
print("客户端代码版本验证")
print("=" * 60)
print()

# 测试1: 检查VoiceManager是否使用session.build_headers
print("[测试1] 检查VoiceManager.initiate_call...")
try:
    import inspect
    from client.features.voice import VoiceManager
    source = inspect.getsource(VoiceManager.initiate_call)
    if "self.session.build_headers()" in source:
        print("[PASS] VoiceManager使用session.build_headers()")
    else:
        print("[FAIL] VoiceManager未使用session.build_headers()")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

# 测试2: 验证DEFAULT_VERSION
print("\n[测试2] 检查DEFAULT_VERSION...")
try:
    from shared.protocol import DEFAULT_VERSION
    print(f"DEFAULT_VERSION = {DEFAULT_VERSION}")
    if DEFAULT_VERSION == "1.0":
        print("[PASS] DEFAULT_VERSION正确")
    else:
        print(f"[FAIL] DEFAULT_VERSION错误: {DEFAULT_VERSION}")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

# 测试3: 测试session.build_headers()
print("\n[测试3] 测试session.build_headers()...")
try:
    from client.core.network import NetworkClient
    from client.core.session import ClientSession
    from client.config import CLIENT_CONFIG

    nc = NetworkClient(CLIENT_CONFIG)
    sess = ClientSession(nc)
    headers = sess.build_headers(require_auth=False)
    print(f"Headers: {headers}")

    if "version" in headers and headers["version"] == "1.0":
        print("[PASS] Headers包含正确的version")
    else:
        print(f"[FAIL] Headers中的version错误: {headers.get('version')}")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

print()
print("=" * 60)
print("[SUCCESS] 所有测试通过！代码已正确更新。")
print("=" * 60)
print()
print("现在可以安全启动客户端进行测试")
print("如果仍然出现426错误，说明启动的是旧的客户端进程")
print()
