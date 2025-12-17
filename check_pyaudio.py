# -*- coding: utf-8 -*-
"""检查PyAudio安装状态"""
import sys

print("=" * 60)
print("PyAudio 安装检查")
print("=" * 60)
print()

# 检查1: 尝试导入PyAudio
print("[检查1] 尝试导入 PyAudio...")
try:
    import pyaudio
    print(f"[成功] PyAudio 已安装，版本: {pyaudio.__version__}")
    PYAUDIO_OK = True
except ImportError as e:
    print(f"[失败] PyAudio 未安装: {e}")
    print("       请运行: pip install pyaudio")
    PYAUDIO_OK = False
except Exception as e:
    print(f"[错误] PyAudio 导入失败: {e}")
    print(f"       错误类型: {type(e).__name__}")
    PYAUDIO_OK = False

if not PYAUDIO_OK:
    print()
    print("=" * 60)
    print("请先安装 PyAudio:")
    print("  pip install pyaudio")
    print("=" * 60)
    sys.exit(1)

print()

# 检查2: 尝试创建PyAudio实例
print("[检查2] 尝试创建 PyAudio 实例...")
try:
    pa = pyaudio.PyAudio()
    print("[成功] PyAudio 实例创建成功")

    # 检查音频设备
    print()
    print("[检查3] 音频设备信息...")
    device_count = pa.get_device_count()
    print(f"  检测到 {device_count} 个音频设备")

    # 默认输入设备
    try:
        default_input = pa.get_default_input_device_info()
        print(f"  默认输入设备: {default_input['name']}")
    except Exception as e:
        print(f"  [警告] 无默认输入设备: {e}")

    # 默认输出设备
    try:
        default_output = pa.get_default_output_device_info()
        print(f"  默认输出设备: {default_output['name']}")
    except Exception as e:
        print(f"  [警告] 无默认输出设备: {e}")

    pa.terminate()

except Exception as e:
    print(f"[失败] PyAudio 初始化失败: {e}")
    print(f"       错误类型: {type(e).__name__}")
    sys.exit(1)

print()
print("=" * 60)
print("[成功] PyAudio 工作正常！")
print("=" * 60)
