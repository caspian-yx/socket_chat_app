#!/usr/bin/env python3
"""
数据库清空工具
清空服务器和客户端的所有数据库数据
"""
import os
import sqlite3
from pathlib import Path


def clear_database(db_path: str) -> None:
    """清空指定数据库的所有数据"""
    if not os.path.exists(db_path):
        print(f"❌ 数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 获取所有表名
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()

        if not tables:
            print(f"✅ {db_path} 中没有表")
            conn.close()
            return

        # 删除所有表中的数据
        deleted_count = 0
        for table_name in tables:
            table = table_name[0]
            if table == 'sqlite_sequence':
                continue
            cursor.execute(f"DELETE FROM {table}")
            count = cursor.rowcount
            deleted_count += count
            print(f"   - 清空表 {table}: 删除 {count} 条记录")

        # 重置自增ID
        cursor.execute("DELETE FROM sqlite_sequence")

        conn.commit()
        conn.close()
        print(f"✅ 成功清空 {db_path}，共删除 {deleted_count} 条记录\n")

    except Exception as e:
        print(f"❌ 清空数据库失败: {e}\n")


def delete_private_conversations(db_path: str) -> None:
    """删除双人通信相关的消息记录（包含'|'的conversation_id）"""
    if not os.path.exists(db_path):
        print(f"[错误] 数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 删除客户端数据库中的双人通信记录
        cursor.execute("DELETE FROM messages WHERE conversation_id LIKE '%|%'")
        count = cursor.rowcount

        conn.commit()
        conn.close()
        print(f"[成功] 删除双人通信记录: {count} 条")

    except Exception as e:
        print(f"[错误] 删除失败: {e}")


def main():
    import sys
    import io

    # 设置stdout编码为utf-8
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    if len(sys.argv) > 1 and sys.argv[1] == "--clear-private":
        # 只清除双人通信记录
        print("=" * 60)
        print("清除双人通信记录")
        print("=" * 60)
        print()

        client_db = "client_data.db"
        print(f"处理客户端数据库: {client_db}")
        delete_private_conversations(client_db)

        print()
        print("=" * 60)
        print("双人通信记录已清除！")
        print("=" * 60)
        return

    print("=" * 60)
    print("🗑️  Socket Chat 数据库清空工具")
    print("=" * 60)
    print()

    # 服务器数据库
    server_db = "data/server.db"
    print(f"📂 清空服务器数据库: {server_db}")
    clear_database(server_db)

    # 客户端数据库
    client_db = "client_data.db"
    print(f"📂 清空客户端数据库: {client_db}")
    clear_database(client_db)

    print("=" * 60)
    print("✅ 数据库清空完成！")
    print("=" * 60)
    print()
    print("💡 提示：")
    print("   1. 所有用户账号已删除")
    print("   2. 所有聊天记录已清空")
    print("   3. 所有会话已清除")
    print("   4. 可以重新启动服务器和客户端使用")
    print()
    print("💡 仅清除双人通信记录：")
    print("   python clear_database.py --clear-private")
    print()


if __name__ == "__main__":
    main()
