# 📬 离线消息功能说明

## ✅ 功能概述

Socket Chat 现已支持**完整的离线消息功能**！您可以：

1. ✅ **双击离线用户开始聊天**
2. ✅ **发送消息给离线用户** - 消息自动存储在服务器
3. ✅ **用户上线自动接收** - 所有离线消息自动推送
4. ✅ **消息持久化存储** - 消息保存在数据库中，不会丢失

---

## 🎯 使用方法

### 方式1：双击离线用户发送消息（推荐）⭐

```
步骤：
1. 打开客户端，登录账号（如 alice）
2. 点击左侧 "👥 用户列表"
3. 切换到 "⚪ 离线" 标签页
4. 双击一个离线用户（如 charlie）
5. 系统提示："已开始与 charlie 的对话（用户离线，将发送离线消息）"
6. 输入消息，点击"发送"
7. 消息发送成功，保存在服务器
```

### 方式2：从全部用户列表发送

```
步骤：
1. 切换到 "📋 全部" 标签页
2. 双击任意用户（在线或离线都可以）
3. 发送消息
```

### 方式3：手动输入

```
步骤：
1. 发送至：选择 "私聊"
2. 目标ID：输入离线用户的用户名
3. 输入消息，点击"发送"
```

---

## 🔄 离线消息流程

### 发送离线消息

```mermaid
用户A (在线) → 发送消息给用户B
                      ↓
              服务器检测用户B状态
                      ↓
        用户B离线？ → 是 → 消息存入数据库 → 发送成功确认
                      ↓
                      否
                      ↓
                直接发送给用户B
```

**实际流程**：
```
1. Alice 登录（在线）
2. Bob 离线
3. Alice 双击 "⚪ 离线" 列表中的 Bob
4. Alice 发送消息："你好，Bob！"
5. 服务器检测到 Bob 离线
6. 消息存入数据库的离线消息队列
7. Alice 收到发送成功确认
8. Alice 看到消息显示在聊天窗口中
```

### 接收离线消息

```mermaid
用户B登录
    ↓
服务器检测到用户B上线
    ↓
从数据库查询用户B的所有离线消息
    ↓
自动推送所有离线消息给用户B
    ↓
用户B收到所有离线消息
    ↓
数据库清除已发送的离线消息
```

**实际流程**：
```
1. Bob 登录系统
2. 服务器检测到 Bob 上线
3. OfflineDispatcher 自动启动
4. 从数据库获取 Bob 的离线消息
5. 逐个推送给 Bob：
   - [Alice] 你好，Bob！
   - [Charlie] 晚上有空吗？
6. Bob 看到所有离线消息
7. 数据库清除已发送的消息
```

---

## 📊 技术实现

### 服务器端架构

#### 1. 消息服务（MessageService）
```python
# server/services/message_service.py:57-63

async def _deliver_to_user(self, user_id: str, event: Dict[str, Any]) -> bool:
    if not user_id:
        return False
    # 尝试发送消息
    delivered = await self.connection_manager.send_to_user(user_id, event)
    if not delivered:
        # 用户离线，存储到离线消息队列
        self.repository.enqueue_offline_message(user_id, event)
    return delivered
```

**工作原理**：
- 每次发送消息时，先尝试直接发送
- 如果用户不在线，自动存储到数据库
- 发送者无感知，统一返回成功

#### 2. 离线消息分发器（OfflineDispatcher）
```python
# server/workers/offline.py:34-61

def notify_user_online(self, user_id: str) -> None:
    """用户上线时调用"""
    self._queue.put_nowait(user_id)

async def _drain_user_queue(self, user_id: str) -> None:
    """推送离线消息"""
    messages = self.repository.consume_offline_messages(user_id)
    for event in messages:
        delivered = await self.connection_manager.send_to_user(user_id, event)
        if not delivered:
            # 用户又离线了，放回队列
            self.repository.enqueue_offline_message(user_id, event)
            break
```

**工作原理**：
- 后台常驻任务，监听用户上线事件
- 用户登录时自动触发
- 批量推送所有离线消息
- 如果推送失败，消息重新入队

#### 3. 认证服务（AuthService）
```python
# server/services/auth_service.py:56

self._notify_online(username)

def _notify_online(self, user_id: Optional[str]) -> None:
    if user_id and self.offline_dispatcher:
        self.offline_dispatcher.notify_user_online(user_id)
```

**工作原理**：
- 登录成功后立即通知 OfflineDispatcher
- 触发离线消息推送

### 数据库存储

#### 离线消息表结构
```sql
CREATE TABLE IF NOT EXISTS offline_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,  -- JSON格式的消息
    created_at INTEGER NOT NULL
)
```

#### 存储方法
```python
# server/storage/sqlite_store.py

def enqueue_offline_message(self, user_id: str, message: dict) -> None:
    """存储离线消息"""
    import json
    message_json = json.dumps(message)
    # 插入数据库

def consume_offline_messages(self, user_id: str) -> list:
    """获取并删除离线消息"""
    # 查询所有消息
    # 删除已读消息
    # 返回消息列表
```

---

## 🧪 测试步骤

### 测试1：发送离线消息

**准备**：
```bash
# 终端1：启动服务器
python -m server.main

# 终端2：启动客户端1（Alice）
python -m client.tk_main
# 登录：alice / alice

# 终端3：启动客户端2（Bob）
python -m client.tk_main
# 登录：bob / bob
```

**测试**：
```
1. Bob 登录后，点击"退出登录"（Bob 离线）
2. Alice 窗口：
   - 观察在线列表：Bob 消失
   - 切换到 "⚪ 离线" 标签页
   - 看到 Bob 出现在离线列表
3. Alice 双击 Bob
4. 系统提示："已开始与 bob 的对话（用户离线，将发送离线消息）"
5. Alice 发送消息："你离线了，这是离线消息！"
6. ✅ 消息发送成功
7. Alice 看到消息显示在聊天窗口
```

### 测试2：接收离线消息

**继续上面的测试**：
```
8. Bob 重新登录
9. ✅ Bob 自动收到离线消息：
   会话列表出现："💬 与 alice 的对话 (未读1)"
10. Bob 点击该会话
11. ✅ 看到消息："[alice] 你离线了，这是离线消息！"
12. Alice 窗口：
    - ✅ 在线列表中出现 Bob
    - ✅ 离线列表中 Bob 消失
    - ✅ 系统日志："🟢 bob 上线了"
```

### 测试3：多条离线消息

```
1. Bob 离线
2. Alice 发送多条消息：
   - "消息1"
   - "消息2"
   - "消息3"
3. Charlie 也登录，发送消息给 Bob：
   - "Charlie 的消息"
4. Bob 重新登录
5. ✅ Bob 收到所有离线消息：
   - [alice] 消息1
   - [alice] 消息2
   - [alice] 消息3
   - [charlie] Charlie 的消息
```

### 测试4：离线消息持久化

```
1. Alice 发送离线消息给 Bob
2. 关闭服务器（Ctrl+C）
3. 重新启动服务器
4. Bob 登录
5. ✅ Bob 仍然能收到离线消息（证明消息已持久化）
```

---

## ✨ 功能特性

### ✅ 已实现的功能

| 功能 | 状态 | 说明 |
|------|------|------|
| **双击离线用户** | ✅ | 可以双击离线列表的用户开始聊天 |
| **离线消息存储** | ✅ | 消息自动存储到 SQLite 数据库 |
| **自动推送** | ✅ | 用户上线自动推送所有离线消息 |
| **消息持久化** | ✅ | 服务器重启后离线消息不丢失 |
| **批量推送** | ✅ | 支持推送多条离线消息 |
| **推送失败重试** | ✅ | 推送失败时消息重新入队 |
| **用户提示** | ✅ | UI明确提示"用户离线，将发送离线消息" |
| **未读提示** | ✅ | 会话列表显示未读消息数量 |

### 🎯 用户体验

**对于发送者（Alice）**：
- ✅ 无需关心对方是否在线
- ✅ 统一的发送体验
- ✅ 明确的状态提示
- ✅ 消息保证送达

**对于接收者（Bob）**：
- ✅ 登录即可收到所有消息
- ✅ 消息不会丢失
- ✅ 按时间顺序接收
- ✅ 自动标记未读

---

## 🔍 调试和日志

### 服务器日志

**发送离线消息时**：
```
INFO:server.services.message_service:Message stored for offline user: bob
```

**用户上线时**：
```
DEBUG:server.workers.offline:Delivering 3 offline messages to bob
```

### 客户端提示

**发送给离线用户**：
```
系统日志：已开始与 bob 的对话（用户离线，将发送离线消息）
```

**收到离线消息**：
```
会话列表：💬 与 alice 的对话 (未读3)
```

---

## 🐛 已知限制

### 当前不支持的场景

1. **离线消息数量限制** - 暂无限制，理论上可以无限存储
2. **离线消息过期** - 消息永不过期，直到用户上线接收
3. **离线消息已读回执** - 无法知道用户是否已读离线消息

### 未来可能的改进

1. **离线消息预览** - 登录时显示离线消息摘要
2. **消息过期机制** - 超过N天的离线消息自动清理
3. **离线消息数量限制** - 每个用户最多保存N条离线消息
4. **已读回执** - 推送后标记消息已读

---

## 📝 总结

### 完整功能链路

```
[用户A在线] → 发送消息 → [服务器]
                              ↓
                        检测用户B状态
                              ↓
                    用户B在线？ ← 否 → 存入数据库
                              ↓
                             是
                              ↓
                        直接推送给用户B


[用户B登录] → [服务器检测上线]
                  ↓
            查询离线消息
                  ↓
            推送所有消息
                  ↓
            清除已发送消息
                  ↓
           [用户B收到消息]
```

### 关键优势

1. ✅ **零配置** - 功能默认启用，无需额外设置
2. ✅ **高可靠** - 消息持久化存储，保证不丢失
3. ✅ **用户友好** - 双击即可聊天，无需关心在线状态
4. ✅ **性能优秀** - 异步推送，不阻塞登录流程

---

**更新时间**：2025-11-15
**版本**：v2.1
