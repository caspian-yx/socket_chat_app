#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清空客户端和服务器数据库中的所有数据"""

import sqlite3
import sys
from pathlib import Path

# 设置输出编码为 UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def clear_database(db_path: str, description: str):
    """清空指定数据库中的所有表数据"""
    path = Path(db_path)
    if not path.exists():
        print(f"[X] {description} 不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取所有表名
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        # 清空每个表
        cleared_count = 0
        for table in tables:
            table_name = table[0]
            if table_name != 'sqlite_sequence':  # 跳过 SQLite 内部表
                cursor.execute(f"DELETE FROM {table_name};")
                cleared_count += 1
                print(f"  [OK] 清空表: {table_name}")

        # 重置自增计数器
        cursor.execute("DELETE FROM sqlite_sequence;")

        conn.commit()
        conn.close()
        print(f"[SUCCESS] {description} 数据已清空 (共清空 {cleared_count} 个表)\n")

    except Exception as e:
        print(f"[ERROR] 清空 {description} 失败: {e}\n")

if __name__ == "__main__":
    print("=" * 60)
    print("清空数据库数据")
    print("=" * 60)

    # 清空客户端数据库
    clear_database("client_data.db", "客户端数据库")

    # 清空服务器数据库
    clear_database("data/server.db", "服务器数据库")

    print("=" * 60)
    print("[SUCCESS] 数据库清空完成!")
    print("=" * 60)
    print("\n现在可以重新测试了:")
    print("1. 重新启动服务器和客户端")
    print("2. 测试离线消息功能")
    print("3. 验证消息不重复、未读提示正常")
