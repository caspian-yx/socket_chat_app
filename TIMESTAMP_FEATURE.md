# 消息时间戳功能说明

## ✅ 已实现功能

现在每条消息都会显示发送时间，格式如下：

```
[时:分:秒] [发送者] 消息内容
```

### 示例

```
[14:23:45] [alice] 你好，Bob！
[14:23:50] [bob] 你好，Alice！
[14:24:12] [系统] ✅ 已连接到服务器
```

---

## 📝 修改内容

### 1. 消息格式化 (client/ui/tk_chat.py:1932-1935)

```python
# 格式化时间戳
if timestamp is None:
    timestamp = int(time.time())
time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))

# 新格式：[时间] [发送者] 消息内容
line = f"[{time_str}] [{sender}] {text}"
```

### 2. 接收消息时提取时间戳 (client/ui/tk_chat.py:1405)

```python
timestamp = message.get("timestamp", int(time.time()))
```

### 3. 发送消息时使用当前时间 (client/ui/tk_chat.py:1120, 1124)

```python
# 私聊消息
self._add_message_to_conversation(
    result["conversation_id"],
    "user",
    self.current_user or "我",
    result["text"],
    other_id=result.get("target_id"),
    timestamp=int(time.time()),  # 添加当前时间戳
)

# 群聊消息
self._add_message_to_conversation(
    convo_id, "room",
    self.current_user or "我",
    result["text"],
    timestamp=int(time.time())  # 添加当前时间戳
)
```

### 4. 系统日志也显示时间 (client/ui/tk_chat.py:1430)

```python
def _append_log(self, text: str) -> None:
    self._add_message_to_conversation(
        self.system_conv_id, "system", "系统", text,
        timestamp=int(time.time())
    )
```

---

## 🎨 显示效果

### 私聊对话

```
[14:23:45] [alice] 你好，我是 Alice
[14:23:50] [bob] 你好，我是 Bob
[14:24:05] [alice] 我们可以开始聊天了
[14:24:10] [bob] 太好了！
```

### 群聊对话

```
[14:30:12] [alice] 大家好！
[14:30:15] [bob] 你好 Alice
[14:30:20] [caspian] 欢迎欢迎
[14:30:25] [alice] 很高兴见到大家
```

### 系统日志

```
[14:20:00] [系统] 🟢 已连接 | 服务器：127.0.0.1 | 用户：alice
[14:25:30] [系统] 🟢 bob 上线了
[14:28:45] [系统] ✅ bob 接受了你的好友请求
[14:35:20] [系统] ⚪ bob 离线了
```

---

## 🕐 时间格式说明

- **格式**: HH:MM:SS (24小时制)
- **时区**: 使用本地时区
- **精度**: 秒级精度

### 时间来源

1. **接收的消息**: 使用服务器发送的 `message["timestamp"]`
2. **发送的消息**: 使用客户端本地时间 `int(time.time())`
3. **历史消息**: 使用存储的时间戳
4. **系统日志**: 使用客户端本地时间

---

## 🔧 自定义时间格式

如果需要修改时间显示格式，可以编辑 `client/ui/tk_chat.py` 第1932行：

```python
# 当前格式：14:23:45
time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))

# 其他可选格式：
# 12小时制：02:23:45 PM
# time_str = time.strftime("%I:%M:%S %p", time.localtime(timestamp))

# 包含日期：2025-01-17 14:23:45
# time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

# 包含日期（短格式）：01-17 14:23
# time_str = time.strftime("%m-%d %H:%M", time.localtime(timestamp))

# 只显示时:分：14:23
# time_str = time.strftime("%H:%M", time.localtime(timestamp))
```

---

## ✨ 优点

1. **时间可视化**: 用户可以清楚看到每条消息的发送时间
2. **历史回顾**: 更容易追溯对话历史
3. **时间感知**: 知道消息是刚发送的还是之前的
4. **调试友好**: 帮助定位问题发生的时间点

---

## 📊 测试建议

1. **发送消息**: 发送几条消息，观察时间戳是否正确显示
2. **接收消息**: 让另一个用户发送消息，确认时间戳显示
3. **历史消息**: 退出重新登录，查看历史消息时间是否保留
4. **系统日志**: 查看系统事件的时间戳
5. **不同会话**: 切换不同会话，确认时间戳格式一致

---

## 🎉 使用效果

现在启动客户端，你将看到：

```
💬 Socket Chat - alice

=== 消息记录 ===
[14:20:00] [系统] 🟢 已连接 | 服务器：127.0.0.1 | 用户：alice

=== 与 bob 的对话 ===
[14:23:45] [alice] 你好，Bob！
[14:23:50] [bob] 你好，Alice！
[14:24:05] [alice] 我们成为好友了
[14:24:10] [bob] 是的，很高兴！

=== 群聊：tech_talk ===
[14:30:12] [alice] 大家好！
[14:30:15] [bob] 你好 Alice
[14:30:20] [caspian] 欢迎欢迎
```

所有消息都会显示精确到秒的时间戳，让对话历史更加清晰！
