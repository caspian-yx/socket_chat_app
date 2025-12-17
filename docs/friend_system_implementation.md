# 好友系统实现说明

## 📋 功能概述

实现了一个完整的好友系统，用户必须先添加好友才能进行双人通信。

### 主要功能

1. **好友申请** - 输入用户ID发送好友请求
2. **请求处理** - 接受或拒绝好友请求
3. **好友列表** - 查看好友列表（只显示好友在线状态）
4. **删除好友** - 删除好友并清理所有相关数据

---

## ✅ 已完成部分

### 1. 协议层 (`shared/protocol/commands.py`)

添加了好友系统相关命令：
```python
FRIEND_REQUEST = "friend/request"        # 发送好友请求
FRIEND_REQUEST_ACK = "friend/request_ack"
FRIEND_ACCEPT = "friend/accept"          # 接受好友请求
FRIEND_ACCEPT_ACK = "friend/accept_ack"
FRIEND_REJECT = "friend/reject"          # 拒绝好友请求
FRIEND_REJECT_ACK = "friend/reject_ack"
FRIEND_DELETE = "friend/delete"          # 删除好友
FRIEND_DELETE_ACK = "friend/delete_ack"
FRIEND_LIST = "friend/list"              # 获取好友列表
FRIEND_LIST_ACK = "friend/list_ack"
FRIEND_EVENT = "friend/event"            # 好友事件通知
```

### 2. 数据库存储 (`server/storage/sqlite_store.py`)

#### 数据表设计

**friend_requests 表**（好友请求）:
```sql
CREATE TABLE friend_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user TEXT NOT NULL,
    to_user TEXT NOT NULL,
    message TEXT,                          -- 可选的请求消息
    status TEXT NOT NULL DEFAULT 'pending', -- pending/accepted/rejected
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(from_user, to_user)
);
```

**friends 表**（好友关系）:
```sql
CREATE TABLE friends (
    user1 TEXT NOT NULL,
    user2 TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user1, user2),
    CHECK (user1 < user2)                  -- 确保user1 < user2，避免重复
);
```

#### 数据库API

- `send_friend_request(from_user, to_user, message)` - 发送好友请求
- `get_pending_friend_requests(user_id)` - 获取待处理的好友请求
- `get_sent_friend_requests(user_id)` - 获取已发送的好友请求
- `accept_friend_request(request_id)` - 接受好友请求
- `reject_friend_request(request_id)` - 拒绝好友请求
- `delete_friend(user1, user2)` - 删除好友关系
- `list_friends(user_id)` - 获取好友列表
- `are_friends(user1, user2)` - 检查是否是好友

### 3. 服务器端服务 (`server/services/friend_service.py`)

实现了 `FriendService` 类，处理所有好友相关请求：

- `handle_request()` - 处理好友请求
- `handle_accept()` - 处理接受请求
- `handle_reject()` - 处理拒绝请求
- `handle_delete()` - 处理删除好友
- `handle_list()` - 处理获取好友列表

**事件通知**：
- `new_request` - 收到新的好友请求
- `request_accepted` - 好友请求被接受
- `request_rejected` - 好友请求被拒绝
- `friend_deleted` - 被删除好友

### 4. 服务器端注册 (`server/main.py`)

```python
from server.services.friend_service import FriendService

friend_service = FriendService(connection_manager, repository)

router.register(MsgType.FRIEND_REQUEST, friend_service.handle_request)
router.register(MsgType.FRIEND_ACCEPT, friend_service.handle_accept)
router.register(MsgType.FRIEND_REJECT, friend_service.handle_reject)
router.register(MsgType.FRIEND_DELETE, friend_service.handle_delete)
router.register(MsgType.FRIEND_LIST, friend_service.handle_list)
```

### 5. 在线列表修改 (`server/services/presence_service.py`)

修改了 `handle_list()` 方法，只返回好友中的在线用户：

```python
async def handle_list(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
    # 获取用户的好友列表
    friends = set(self.repository.list_friends(ctx.user_id))

    # 获取所有在线用户
    all_online = set(self.repository.list_online_users())

    # 只返回好友中在线的用户
    online_friends = list(friends & all_online)

    return _ok_response(message, {"users": online_friends})
```

### 6. 客户端好友管理 (`client/features/friends.py`)

实现了 `FriendsManager` 类：

```python
class FriendsManager:
    async def send_friend_request(target_id, message)
    async def accept_friend_request(request_id)
    async def reject_friend_request(request_id)
    async def delete_friend(friend_id)
    async def refresh_friends()

    def get_friends() -> list[str]
    def get_pending_requests() -> list[Dict]
    def get_sent_requests() -> list[Dict]
    def is_friend(user_id) -> bool
```

### 7. 客户端集成 (`client/ui/tk_chat.py`)

- 导入了 `FriendsManager`
- 在 `ClientRuntime.__init__` 中创建了 `self.friends` 实例
- 注册了 `friend/event` 事件处理器

---

## ⏳ 剩余工作

### 1. ClientRuntime API 方法

需要在 `ClientRuntime` 类中添加好友相关的方法：

```python
# 在 client/ui/tk_chat.py 的 ClientRuntime 类中添加：

async def send_friend_request(self, target_id: str, message: str = "") -> Dict[str, Any]:
    """发送好友请求"""
    return await self.friends.send_friend_request(target_id, message)

async def accept_friend_request(self, request_id: int) -> Dict[str, Any]:
    """接受好友请求"""
    return await self.friends.accept_friend_request(request_id)

async def reject_friend_request(self, request_id: int) -> Dict[str, Any]:
    """拒绝好友请求"""
    return await self.friends.reject_friend_request(request_id)

async def delete_friend(self, friend_id: str) -> Dict[str, Any]:
    """删除好友"""
    return await self.friends.delete_friend(friend_id)

async def refresh_friends(self) -> Dict[str, Any]:
    """刷新好友列表"""
    return await self.friends.refresh_friends()
```

### 2. GUI界面修改

#### 2.1 添加好友管理按钮

在左侧面板添加"好友管理"按钮：

```python
# 在 _build_layout() 方法中，在房间管理按钮下方添加：
friend_btn = tk.Button(
    left,
    text="👥 好友管理",
    font=ModernStyle.FONTS["small"],
    bg=ModernStyle.COLORS["success"],
    fg=ModernStyle.COLORS["light"],
    relief="flat",
    padx=15,
    pady=6,
    cursor="hand2",
    command=self._open_friend_window
)
friend_btn.pack(fill=tk.X, padx=10, pady=5)
```

#### 2.2 处理好友事件

在 `_process_queue()` 方法中添加好友事件处理：

```python
def _process_queue(self) -> None:
    try:
        while True:
            kind, payload = self.ui_queue.get_nowait()
            if kind == "message":
                self._append_message(payload)
            elif kind == "future":
                tag, future = payload
                self._handle_future(tag, future)
            elif kind == "status":
                self._set_status(str(payload))
            elif kind == "file":
                self._handle_file_event(payload)
            elif kind == "presence_update":
                self._handle_presence_update(payload)
            elif kind == "voice":
                self._handle_voice_event(payload)
            elif kind == "friend_event":  # 新增
                self._handle_friend_event_ui(payload)
    except queue.Empty:
        pass
```

#### 2.3 实现好友事件UI处理

```python
def _handle_friend_event_ui(self, event: Dict[str, Any]) -> None:
    """处理好友事件UI"""
    event_type = event.get("event_type")

    if event_type == "new_request":
        # 收到新的好友请求
        from_user = event.get("from_user")
        message = event.get("message", "")
        request_id = event.get("request_id")

        # 弹窗显示好友请求
        result = messagebox.askyesno(
            "好友请求",
            f"{from_user} 想添加你为好友\n\n消息：{message}\n\n是否同意？"
        )

        if result:
            # 接受请求
            self.runtime.submit(
                self.runtime.accept_friend_request(request_id),
                ("friend_accept", request_id)
            )
        else:
            # 拒绝请求
            self.runtime.submit(
                self.runtime.reject_friend_request(request_id),
                ("friend_reject", request_id)
            )

    elif event_type == "request_accepted":
        # 好友请求被接受
        user_id = event.get("user_id")
        self._append_log(f"✅ {user_id} 接受了你的好友请求")
        # 刷新好友列表
        self._refresh_friends()

    elif event_type == "request_rejected":
        # 好友请求被拒绝
        user_id = event.get("user_id")
        self._append_log(f"❌ {user_id} 拒绝了你的好友请求")

    elif event_type == "friend_deleted":
        # 被删除好友
        user_id = event.get("user_id")
        self._append_log(f"⚠️ {user_id} 删除了你")
        # 删除相关会话和聊天记录
        self._cleanup_friend_data(user_id)
        # 刷新好友列表
        self._refresh_friends()
```

#### 2.4 创建好友管理窗口

```python
def _open_friend_window(self) -> None:
    """打开好友管理窗口"""
    if hasattr(self, 'friend_window') and self.friend_window and self.friend_window.winfo_exists():
        self.friend_window.lift()
        self.friend_window.focus_set()
        return
    self.friend_window = FriendManagerWindow(self)

class FriendManagerWindow(tk.Toplevel):
    """好友管理窗口"""

    def __init__(self, app: TkChatApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("👥 好友管理")
        self.geometry("800x600")
        self.configure(bg=ModernStyle.COLORS["darkest"])

        # 创建标签页
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 好友列表标签页
        friends_frame = tk.Frame(notebook, bg=ModernStyle.COLORS["dark"])
        notebook.add(friends_frame, text="好友列表")

        # 添加好友标签页
        add_frame = tk.Frame(notebook, bg=ModernStyle.COLORS["dark"])
        notebook.add(add_frame, text="添加好友")

        # 好友请求标签页
        requests_frame = tk.Frame(notebook, bg=ModernStyle.COLORS["dark"])
        notebook.add(requests_frame, text="好友请求")

        self._build_friends_tab(friends_frame)
        self._build_add_tab(add_frame)
        self._build_requests_tab(requests_frame)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_add_tab(self, parent: tk.Frame) -> None:
        """添加好友标签页"""
        tk.Label(
            parent,
            text="输入用户ID添加好友",
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        ).pack(pady=20)

        form_frame = tk.Frame(parent, bg=ModernStyle.COLORS["dark"])
        form_frame.pack(fill=tk.X, padx=50, pady=10)

        tk.Label(
            form_frame,
            text="用户ID:",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["lighter"]
        ).pack(anchor=tk.W)

        self.target_id_var = tk.StringVar()
        tk.Entry(
            form_frame,
            textvariable=self.target_id_var,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"]
        ).pack(fill=tk.X, pady=5)

        tk.Label(
            form_frame,
            text="附加消息（可选）:",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["lighter"]
        ).pack(anchor=tk.W, pady=(10, 0))

        self.message_var = tk.StringVar()
        tk.Entry(
            form_frame,
            textvariable=self.message_var,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"]
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            form_frame,
            text="📤 发送请求",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            command=self._send_request
        ).pack(pady=20)

    def _send_request(self) -> None:
        """发送好友请求"""
        target_id = self.target_id_var.get().strip()
        message = self.message_var.get().strip()

        if not target_id:
            messagebox.showwarning("提示", "请输入用户ID")
            return

        self.app.runtime.submit(
            self.app.runtime.send_friend_request(target_id, message),
            ("friend_request", target_id)
        )

        messagebox.showinfo("成功", f"已向 {target_id} 发送好友请求")
        self.target_id_var.set("")
        self.message_var.set("")

    # 其他方法...
```

### 3. 删除好友数据清理

```python
def _cleanup_friend_data(self, friend_id: str) -> None:
    """清理删除好友的相关数据"""
    # 生成会话ID
    users = sorted([self.current_user, friend_id])
    conversation_id = f"{users[0]}|{users[1]}"

    # 从会话列表中移除
    if conversation_id in self.conversations:
        conv = self.conversations[conversation_id]
        tree_id = conv.get("tree_id")

        # 删除Tree节点
        if tree_id:
            try:
                self.conversation_tree.delete(tree_id)
            except:
                pass

        # 删除会话数据
        del self.conversations[conversation_id]
        if tree_id in self.tree_to_conversation:
            del self.tree_to_conversation[tree_id]

    # 从数据库中删除聊天记录
    # TODO: 添加数据库删除方法
    # self.runtime.db.delete_conversation_messages(conversation_id)

    self._append_log(f"已清理与 {friend_id} 的聊天记录")
```

### 4. 权限检查

修改消息发送逻辑，添加好友权限检查：

```python
def _send_message(self) -> None:
    if not self.current_user:
        messagebox.showwarning("提示", "请先登录")
        return

    text = self.message_input.get("1.0", tk.END).strip()
    if not text:
        return

    mode = self.target_mode.get()
    target = self.target_var.get().strip()

    if mode == "user":
        if not target:
            messagebox.showwarning("提示", "请输入目标用户 ID")
            return

        # 检查是否是好友
        if not self.runtime.friends.is_friend(target):
            messagebox.showwarning(
                "提示",
                f"你和 {target} 不是好友\n请先添加好友后再聊天"
            )
            return

        conversation_id = self.conversation_var.get().strip() or f"{self.current_user}|{target}"
        self.runtime.submit(
            self.runtime.send_direct(conversation_id, target, text),
            ("send_text", conversation_id),
        )
    else:
        # 房间消息不需要好友检查
        if not target:
            messagebox.showwarning("提示", "请输入房间 ID")
            return
        self.runtime.submit(
            self.runtime.send_room(target, text, None),
            ("send_room", target),
        )

    self.message_input.delete("1.0", tk.END)
```

---

## 🎯 测试步骤

1. **启动服务器**
   ```bash
   python -m server.main
   ```

2. **测试好友申请流程**
   - Alice 登录 → 打开好友管理 → 添加好友 → 输入 "bob" → 发送请求
   - Bob 登录 → 收到弹窗通知 → 选择接受/拒绝
   - Alice 收到接受通知
   - 双方的在线列表只显示对方（如果对方在线）

3. **测试消息权限**
   - Alice 尝试给非好友 "charlie" 发消息 → 应该被阻止
   - Alice 给好友 Bob 发消息 → 成功

4. **测试删除好友**
   - Alice 打开好友管理 → 删除 Bob
   - 确认聊天记录被清除
   - Bob 收到删除通知

---

## 📝 注意事项

1. **数据持久化**
   - 好友关系存储在服务器数据库中
   - 聊天记录存储在客户端本地数据库中
   - 删除好友时需要清理本地数据

2. **在线状态**
   - `presence/list` 现在只返回好友中的在线用户
   - 非好友用户不会出现在在线列表中

3. **兼容性**
   - 现有的房间功能不受影响
   - 群聊不需要好友关系
   - 旧数据库需要迁移（会自动创建新表）

4. **权限控制**
   - 只能给好友发送私聊消息
   - 文件传输也应该限制为好友（可选）
   - 语音通话也应该限制为好友（可选）

---

## 🚀 总结

已完成：
- ✅ 协议层命令定义
- ✅ 数据库存储层
- ✅ 服务器端好友服务
- ✅ 在线列表限制为好友
- ✅ 客户端好友管理类
- ✅ 客户端事件处理集成

待完成：
- ⏳ ClientRuntime API方法
- ⏳ 好友管理UI窗口
- ⏳ 好友事件弹窗处理
- ⏳ 删除好友数据清理
- ⏳ 消息发送权限检查

完成以上剩余工作后，好友系统将全面可用！
