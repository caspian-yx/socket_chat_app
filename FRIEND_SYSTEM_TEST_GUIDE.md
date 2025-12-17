# 好友系统测试指南

## ✅ 系统状态

### 已完成的修复
1. ✅ 修复了 NetworkClient API 错误（实现了 _request 方法）
2. ✅ 修复了协议版本不匹配（所有响应都包含 headers: {"version": "1.0"}）
3. ✅ 修复了命令类型不匹配（客户端注册 ACK 命令）
4. ✅ 修复了事件通知命令（使用 MsgType.FRIEND_EVENT.value）
5. ✅ 修复了窗口销毁错误（添加 winfo_exists() 检查）
6. ✅ 修复了在线列表为空（显示所有在线用户）
7. ✅ 清理了所有旧数据（消息、聊天记录）

### 数据库当前状态
- **用户**: 8个（alice, bob, caspian, CASPIAN, pec, sh, whr, xk）
- **消息**: 0条（已清理）
- **好友关系**: 0个（已清理）
- **好友请求**: 0个（已清理）

---

## 🧪 测试步骤

### 测试1：发送好友请求

1. **启动服务器**
   ```bash
   python -m server.main
   ```

2. **Alice 登录**
   - 运行客户端：`python -m client.ui.tk_chat`
   - 用户名：`alice`，密码：`alice`
   - 登录成功后应该看到：
     - 左侧在线列表显示其他在线用户（如果有）
     - 底部状态栏显示"已连接"

3. **Bob 登录**
   - 在另一个终端运行第二个客户端：`python -m client.ui.tk_chat`
   - 用户名：`bob`，密码：`bob`
   - 登录成功后：
     - Alice 的在线列表应该显示 "bob"
     - Bob 的在线列表应该显示 "alice"

4. **Alice 发送好友请求**
   - 点击左侧 "👥 好友管理" 按钮
   - 切换到 "➕ 添加好友" 标签页
   - 输入用户ID：`bob`
   - 输入附加消息（可选）：`你好，我是 Alice`
   - 点击 "📤 发送好友请求"
   - 应该看到：
     - 弹窗提示 "已向 bob 发送好友请求"
     - 切换到 "📬 好友请求" → "📤 已发送的好友请求" 应该看到对 bob 的请求

5. **Bob 收到请求**
   - Bob 的窗口应该**立即弹出对话框**：
     ```
     好友请求
     👤 alice 想添加你为好友
     💬 消息：你好，我是 Alice
     是否同意？
     [是] [否]
     ```
   - 或者打开 "👥 好友管理" → "📬 好友请求" → "📥 收到的好友请求"
   - 应该看到来自 alice 的请求

### 测试2：接受好友请求

1. **Bob 接受请求**
   - 在弹窗中点击 "是"，或者在好友管理窗口中：
     - 选中 alice 的请求
     - 点击 "✅ 接受请求"
   - 应该看到：
     - 弹窗提示 "已接受 alice 的好友请求"
     - "📋 好友列表" 中出现 "alice"

2. **Alice 收到通知**
   - Alice 的系统日志应该显示：`✅ bob 接受了你的好友请求`
   - 打开 "👥 好友管理" → "📋 好友列表"
   - 应该看到 "bob" 在列表中
   - 状态显示 "🟢 在线"

### 测试3：发送私聊消息

1. **Alice 给 Bob 发消息**
   - 在好友管理窗口中，选中 bob
   - 点击 "💬 开始聊天"
   - 主窗口应该：
     - 自动切换到与 bob 的对话
     - 会话ID 自动填充为 "alice|bob"
     - 目标ID 自动填充为 "bob"
     - 模式自动选择为 "私聊"
   - 输入消息：`你好，Bob！`
   - 点击 "📤 发送" 或按 Enter
   - 消息应该成功发送

2. **Bob 收到消息**
   - Bob 的窗口应该：
     - 会话列表中出现 "双人通信：alice-bob"（如果之前没有）
     - 如果未查看该会话，显示 "(未读1)"
     - 点击该会话，消息区域显示：`[alice] 你好，Bob！`

3. **Bob 回复**
   - 输入消息：`你好，Alice！`
   - 发送成功
   - Alice 应该收到消息

### 测试4：非好友限制

1. **Bob 尝试给非好友 caspian 发消息**
   - 在发送区域：
     - 模式：私聊
     - 会话ID：`bob|caspian`
     - 目标ID：`caspian`
     - 消息：`你好`
   - 点击发送
   - 应该弹出对话框：
     ```
     你和 caspian 不是好友

     私聊需要先添加好友

     是否打开好友管理窗口？
     [是] [否]
     ```
   - 消息不应该被发送

### 测试5：删除好友

1. **Alice 删除 Bob**
   - 打开 "👥 好友管理" → "📋 好友列表"
   - 选中 "bob"
   - 点击 "🗑️ 删除好友"
   - 确认对话框：
     ```
     确定要删除好友 bob 吗？

     删除后将清除所有聊天记录，此操作不可撤销！
     ```
   - 点击 "是"
   - 应该看到：
     - 弹窗提示 "已删除好友 bob"
     - bob 从好友列表中消失
     - 系统日志显示：`已清理与 bob 的聊天记录`
     - 主窗口中与 bob 的对话窗口消失

2. **Bob 收到通知**
   - Bob 的系统日志应该显示：`⚠️ alice 删除了你`
   - 如果 Bob 打开好友列表，alice 应该不在其中了

3. **验证删除后无法通信**
   - Alice 尝试给 bob 发消息
   - 应该被阻止（提示不是好友）

### 测试6：拒绝好友请求

1. **Alice 再次向 Bob 发送请求**
   - 好友管理 → 添加好友 → 输入 bob → 发送

2. **Bob 拒绝请求**
   - 在弹窗中点击 "否"，或者：
   - 好友管理 → 好友请求 → 选中 alice → 点击 "❌ 拒绝请求"
   - 确认对话框点击 "是"

3. **Alice 收到通知**
   - 系统日志显示：`❌ bob 拒绝了你的好友请求`

---

## ⚠️ 常见问题

### 问题1：收不到好友请求弹窗
**可能原因**：
- 事件处理器未正确注册
- 服务器未发送事件通知

**检查**：
- 查看服务器控制台是否有错误
- 确认 `client/ui/tk_chat.py` 第52-54行注册了 FRIEND_EVENT 处理器
- 确认 `server/services/friend_service.py` 使用了 `MsgType.FRIEND_EVENT.value`

### 问题2：在线列表为空
**可能原因**：
- presence_service.py 过滤了非好友用户

**检查**：
- 确认 `server/services/presence_service.py` 第31行返回所有在线用户：
  ```python
  all_online = self.repository.list_online_users()
  return _ok_response(message, {"users": all_online})
  ```

### 问题3：TimeoutError
**可能原因**：
- 客户端注册了错误的命令类型

**检查**：
- 确认 `client/features/friends.py` 第27-33行注册的是 ACK 命令：
  ```python
  for command in (
      MsgType.FRIEND_REQUEST_ACK,
      MsgType.FRIEND_ACCEPT_ACK,
      # ...
  ```

### 问题4：Protocol version mismatch
**可能原因**：
- 服务器响应缺少 headers 字段

**检查**：
- 确认 `server/services/friend_service.py` 的响应包含 headers：
  ```python
  headers = request.get("headers", {}) or {}
  headers.setdefault("version", "1.0")
  ```

---

## 📊 预期结果

测试完成后：
- ✅ 可以成功发送好友请求
- ✅ 接收方实时收到弹窗通知
- ✅ 可以接受/拒绝请求
- ✅ 好友列表正确显示在线/离线状态
- ✅ 只能给好友发送私聊消息
- ✅ 非好友被阻止并提示添加好友
- ✅ 删除好友后聊天记录被清除
- ✅ 被删除方收到通知

---

## 🔧 调试建议

如果遇到问题：

1. **查看服务器日志**
   - 服务器控制台会显示所有请求和错误
   - 查找 `StatusCode.XXX` 错误码

2. **查看客户端日志**
   - 系统日志会显示所有事件通知
   - 查找 `[DEBUG]` 或 `[错误]` 消息

3. **检查数据库**
   ```bash
   python -c "import sqlite3; conn = sqlite3.connect('data/server.db'); cursor = conn.cursor(); cursor.execute('SELECT * FROM friend_requests'); print(cursor.fetchall())"
   ```

4. **重启并清理数据**
   - 关闭所有客户端
   - 重启服务器
   - 清理数据库（如上文所示）
   - 重新测试

---

## ✨ 功能完整性确认

好友系统包含以下完整功能：

### 后端功能
- [x] 好友请求数据库表
- [x] 好友关系数据库表
- [x] 发送好友请求 API
- [x] 接受好友请求 API
- [x] 拒绝好友请求 API
- [x] 删除好友 API
- [x] 获取好友列表 API
- [x] 实时事件推送
- [x] 权限验证

### 前端功能
- [x] 好友管理窗口
- [x] 添加好友界面
- [x] 好友列表显示
- [x] 好友请求列表（收到/发送）
- [x] 接受/拒绝请求按钮
- [x] 删除好友按钮
- [x] 开始聊天按钮
- [x] 好友请求弹窗通知
- [x] 消息发送权限检查
- [x] 删除好友数据清理

---

**系统已准备就绪，可以开始测试！** 🎉
