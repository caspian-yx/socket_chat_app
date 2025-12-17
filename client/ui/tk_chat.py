from __future__ import annotations

import asyncio
import logging
import queue
import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, Optional, Tuple

from client.config import CLIENT_CONFIG
from client.core import ClientSession, NetworkClient
from client.features import AuthManager, FileTransferManager, FriendsManager, MessagingManager, PresenceManager, RoomManager, VoiceManager
from client.storage import LocalDatabase
from client.ui.modern_style import ModernStyle
from shared.protocol.commands import MsgType
from shared.protocol.errors import StatusCode
from shared.utils.common import sha256_hex

logger = logging.getLogger(__name__)

UIEvent = Tuple[str, Any]


class ClientRuntime:
    """Runs the asyncio client stack in a background event loop for the Tk UI."""

    def __init__(self, ui_queue: queue.Queue[UIEvent], config_override: Optional[Dict[str, Any]] = None) -> None:
        self.ui_queue = ui_queue
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="TkClientLoop", daemon=True)
        self.config = CLIENT_CONFIG.copy()
        if config_override:
            self.config.update(config_override)
        self.network = NetworkClient(self.config)
        self.session = ClientSession(self.network)
        self.db = LocalDatabase(self.config["local_db_path"])
        self.auth = AuthManager(self.network, self.session)
        self.messaging = MessagingManager(self.network, self.session, self.db)
        self.presence = PresenceManager(self.network, self.session)
        self.friends = FriendsManager(self.network, self.session)
        self.rooms = RoomManager(self.network, self.session)
        self.download_dir = Path(self.config.get("download_dir", "downloads"))
        self.file_transfer = FileTransferManager(self.network, self.session, self.ui_queue, self.download_dir)
        self.voice = VoiceManager(self.network, self.session, self.ui_queue)
        self._message_task: Optional[asyncio.Task] = None
        self._started = False
        self._connected = False  # 标记是否已连接到服务器

        # 注册presence事件监听器，实时更新在线列表
        self.network.register_handler(MsgType.PRESENCE_EVENT, self._handle_presence_event)
        # 注册好友事件监听器
        self.network.register_handler(MsgType.FRIEND_EVENT, self._handle_friend_event)

    async def _handle_presence_event(self, message: Dict[str, Any]) -> None:
        """处理在线状态变化事件"""
        payload = message.get("payload", {})
        user_id = payload.get("user_id")
        state = payload.get("state")
        if user_id and state:
            # 推送到UI队列
            self.ui_queue.put(("presence_update", {"user_id": user_id, "state": state}))

    async def _handle_friend_event(self, message: Dict[str, Any]) -> None:
        """处理好友事件"""
        payload = message.get("payload", {})
        # 推送到UI队列
        self.ui_queue.put(("friend_event", payload))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.thread.start()
        self.submit(self._startup(), ("startup", None))

    async def _startup(self) -> str:
        await self.network.connect()
        self._start_message_listener()
        self._connected = True  # 标记连接完成
        return "连接服务器成功"

    def _start_message_listener(self) -> None:
        if self._message_task and not self._message_task.done():
            return

        async def _run() -> None:
            while True:
                try:
                    msg = await self.messaging.next_message()
                except asyncio.CancelledError:
                    break
                self.ui_queue.put(("message", msg))

        def _create_task() -> None:
            self._message_task = asyncio.create_task(_run())

        self.loop.call_soon_threadsafe(_create_task)

    def submit(self, coro: asyncio.Future, tag: Tuple[str, Optional[str]]) -> None:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        future.add_done_callback(lambda fut: self.ui_queue.put(("future", (tag, fut))))

    async def login(self, username: str, password: str) -> Dict[str, Any]:
        # 登录前，先记录当前本地数据库中已有的消息ID
        # 这样可以区分历史消息和登录后收到的离线消息
        existing_messages = self.db.load_all_messages()
        existing_message_ids = {msg.get("message", {}).get("id") for msg in existing_messages if msg.get("message", {}).get("id")}
        print(f"[DEBUG] 登录前已有 {len(existing_message_ids)} 条消息")

        success = await self.auth.login(username, sha256_hex(password))
        roster: list[str] = []
        rooms: list[str] = []
        history: list[Dict[str, Any]] = []
        if success:
            roster = await self.presence.request_roster()
            rooms = await self.rooms.list_rooms()

            # 加载好友列表（修复：登录后自动加载好友列表）
            await self.friends.refresh_friends()
            print(f"[DEBUG] 已加载好友列表，共 {len(self.friends.get_friends())} 个好友")

            # 加载历史消息：只包含登录前已有的消息
            # 登录后新收到的离线消息不应该被当作历史消息
            all_messages = self.db.load_all_messages()
            history = [msg for msg in all_messages if msg.get("message", {}).get("id") in existing_message_ids]
            new_messages_count = len(all_messages) - len(history)
            print(f"[DEBUG] 登录后数据库有 {len(all_messages)} 条消息")
            print(f"[DEBUG] 加载 {len(history)} 条历史消息")
            print(f"[DEBUG] 新收到 {new_messages_count} 条离线消息（将作为新消息处理）")

        return {"success": success, "username": username, "roster": roster, "rooms": rooms, "history": history, "existing_ids": existing_message_ids}

    async def register(self, username: str, password: str) -> Dict[str, Any]:
        success = await self.auth.register(username, sha256_hex(password))
        # 注册成功后不获取数据，因为用户还没有真正登录（没有token）
        # 用户需要重新登录才能获取roster、rooms等数据
        roster: list[str] = []
        rooms: list[str] = []
        history: list[Dict[str, Any]] = []
        return {"success": success, "username": username, "roster": roster, "rooms": rooms, "history": history}

    async def send_file(self, target_id: str, file_path: str, target_type: str = "user") -> Dict[str, Any]:
        return await self.file_transfer.request_send_file(target_id, Path(file_path), target_type)

    async def accept_file_transfer(self, session_id: str, save_path: str) -> None:
        await self.file_transfer.accept_request(session_id, Path(save_path))

    async def reject_file_transfer(self, session_id: str) -> None:
        await self.file_transfer.reject_request(session_id)

    async def logout(self) -> bool:
        await self.auth.logout()
        self.session.clear()
        return True

    async def send_direct(self, conversation_id: str, target_id: str, text: str, reply_to: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        await self.messaging.send_text(conversation_id, target_id, text, reply_to)
        return {"conversation_id": conversation_id, "target_id": target_id, "text": text, "reply_to": reply_to}

    async def send_room(self, room_id: str, text: str, conversation_id: Optional[str], reply_to: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        await self.messaging.send_room_text(room_id, text, conversation_id, reply_to)
        return {"room_id": room_id, "text": text, "conversation_id": conversation_id, "reply_to": reply_to}

    async def refresh_presence(self) -> list[str]:
        return await self.presence.request_roster()

    async def refresh_rooms(self) -> list[str]:
        return await self.rooms.list_rooms()

    async def create_room(self, room_id: str, encrypted: bool, password: Optional[str] = None) -> Dict[str, Any]:
        return await self.rooms.create_room(room_id, encrypted, password)

    async def join_room(self, room_id: str, password: Optional[str] = None) -> Dict[str, Any]:
        return await self.rooms.join_room(room_id, password)

    async def leave_room(self, room_id: str) -> Dict[str, Any]:
        return await self.rooms.leave_room(room_id)

    async def list_room_members(self, room_id: str) -> list[str]:
        return await self.rooms.list_members(room_id)

    async def room_info(self, room_id: str) -> Dict[str, Any]:
        return await self.rooms.room_info(room_id)

    async def kick_room_member(self, room_id: str, user_id: str) -> Dict[str, Any]:
        return await self.rooms.kick_member(room_id, user_id)

    async def delete_room(self, room_id: str) -> Dict[str, Any]:
        return await self.rooms.delete_room(room_id)

    # Friend management methods
    async def send_friend_request(self, target_id: str, message: str = "") -> Dict[str, Any]:
        """Send a friend request."""
        return await self.friends.send_friend_request(target_id, message)

    async def accept_friend_request(self, request_id: int) -> Dict[str, Any]:
        """Accept a friend request."""
        return await self.friends.accept_friend_request(request_id)

    async def reject_friend_request(self, request_id: int) -> Dict[str, Any]:
        """Reject a friend request."""
        return await self.friends.reject_friend_request(request_id)

    async def delete_friend(self, friend_id: str) -> Dict[str, Any]:
        """Delete a friend."""
        return await self.friends.delete_friend(friend_id)

    async def refresh_friends(self) -> Dict[str, Any]:
        """Refresh friend list and pending requests."""
        return await self.friends.refresh_friends()

    async def initiate_voice_call(self, target_type: str, target_id: str, call_type: str = "direct") -> Dict[str, Any]:
        """Initiate a voice call."""
        return await self.voice.initiate_call(target_type, target_id, call_type)

    async def answer_voice_call(self, call_id: str) -> Dict[str, Any]:
        """Answer an incoming voice call."""
        return await self.voice.answer_call(call_id)

    async def reject_voice_call(self, call_id: str) -> Dict[str, Any]:
        """Reject an incoming voice call."""
        return await self.voice.reject_call(call_id)

    async def end_voice_call(self) -> Dict[str, Any]:
        """End the current voice call."""
        return await self.voice.end_call()

    def get_current_voice_call(self) -> Optional[Dict[str, Any]]:
        """Get current voice call info."""
        return self.voice.get_current_call()

    def shutdown(self) -> None:
        async def _shutdown() -> None:
            if self.session.is_online():
                try:
                    await self.auth.logout()
                except Exception:
                    pass
            if self._message_task:
                self._message_task.cancel()
            await self.network.close()

        if not self._started:
            return
        fut = asyncio.run_coroutine_threadsafe(_shutdown(), self.loop)
        try:
            fut.result(timeout=2)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self._started = False


class LoginWindow(tk.Tk):
    """现代化登录窗口，支持配置服务器 IP 并在成功后进入主界面。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("💬 Socket Chat - 登录")
        self.geometry("450x500")
        self.resizable(False, False)
        self.configure(bg=ModernStyle.COLORS["darkest"])

        self.host_var = tk.StringVar(value=CLIENT_CONFIG["server_host"])
        self.username_var = tk.StringVar(value="alice")
        self.password_var = tk.StringVar(value="alice")
        self.status_var = tk.StringVar(value="")

        self.ui_queue: queue.Queue[UIEvent] = queue.Queue()
        self.runtime: Optional[ClientRuntime] = None
        self.auth_payload: Optional[Dict[str, Any]] = None
        self._current_host: Optional[str] = None
        self._buffered_events: list[UIEvent] = []
        self.after_ids: list[str] = []

        self._center_window()
        self._create_gradient_bg()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._process_queue)
        self.bind("<Return>", lambda e: self._login())

    def _center_window(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _create_gradient_bg(self) -> None:
        self.bg_canvas = tk.Canvas(self, bg=ModernStyle.COLORS["darkest"], highlightthickness=0)
        self.bg_canvas.pack(fill=tk.BOTH, expand=True)

        # 添加装饰性星点
        for i in range(20):
            x = random.randint(0, 450)
            y = random.randint(0, 500)
            size = random.randint(2, 6)
            color = random.choice([
                ModernStyle.COLORS["primary"],
                ModernStyle.COLORS["secondary"],
                ModernStyle.COLORS["success"]
            ])
            self.bg_canvas.create_oval(x, y, x + size, y + size, fill=color, outline="")

    def _build_layout(self) -> None:
        # 卡片容器
        card_frame = tk.Frame(
            self.bg_canvas,
            bg=ModernStyle.COLORS["card_bg"],
            relief="flat",
            bd=0,
            highlightbackground=ModernStyle.COLORS["gray"],
            highlightthickness=1
        )
        card_frame.place(relx=0.5, rely=0.5, anchor="center", width=380, height=420)

        # 标题区域
        title_frame = tk.Frame(card_frame, bg=ModernStyle.COLORS["card_bg"])
        title_frame.pack(fill=tk.X, pady=(25, 15))

        title_label = tk.Label(
            title_frame,
            text="💬 Socket Chat",
            font=ModernStyle.FONTS["title"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["light"]
        )
        title_label.pack()

        subtitle_label = tk.Label(
            title_frame,
            text="安全、高效的即时通信平台",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["gray_light"]
        )
        subtitle_label.pack(pady=(5, 0))

        # 表单区域
        form_frame = tk.Frame(card_frame, bg=ModernStyle.COLORS["card_bg"])
        form_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=15)

        self._create_input_field(form_frame, "🌐 服务器IP:", self.host_var, 0)
        self._create_input_field(form_frame, "👤 用户名:", self.username_var, 1)
        self._create_input_field(form_frame, "🔒 密码:", self.password_var, 2, show="*")

        # 按钮区域
        button_frame = tk.Frame(form_frame, bg=ModernStyle.COLORS["card_bg"])
        button_frame.pack(fill=tk.X, pady=(20, 10))

        self.login_btn = tk.Button(
            button_frame,
            text="🚀 登录系统",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=10,
            cursor="hand2",
            command=self._login
        )
        self.login_btn.pack(fill=tk.X, pady=(0, 5))

        self.register_btn = tk.Button(
            button_frame,
            text="📝 注册账号",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["secondary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["secondary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._register_user
        )
        self.register_btn.pack(fill=tk.X)

        # 状态标签
        self.status_label = tk.Label(
            form_frame,
            textvariable=self.status_var,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["warning"],
            height=2
        )
        self.status_label.pack(fill=tk.X)

        # 底部提示
        footer_label = tk.Label(
            card_frame,
            text="基于 Socket 通信 • 安全可靠",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["gray"]
        )
        footer_label.pack(side=tk.BOTTOM, pady=10)

    def _create_input_field(self, parent: tk.Frame, label_text: str, var: tk.StringVar, row: int, show: Optional[str] = None) -> None:
        field_frame = tk.Frame(parent, bg=ModernStyle.COLORS["card_bg"])
        field_frame.pack(fill=tk.X, pady=8)

        label = tk.Label(
            field_frame,
            text=label_text,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"],
            width=12,
            anchor="w"
        )
        label.pack(side=tk.LEFT)

        entry = tk.Entry(
            field_frame,
            textvariable=var,
            font=ModernStyle.FONTS["normal"],
            show=show,
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=2,
            highlightthickness=1,
            highlightcolor=ModernStyle.COLORS["primary"],
            highlightbackground=ModernStyle.COLORS["gray"]
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))

        # 焦点高亮效果
        entry.bind("<FocusIn>", lambda e: entry.config(highlightbackground=ModernStyle.COLORS["primary"]))
        entry.bind("<FocusOut>", lambda e: entry.config(highlightbackground=ModernStyle.COLORS["gray"]))

    def _ensure_runtime(self, host: str) -> ClientRuntime:
        if self.runtime and self._current_host == host:
            return self.runtime
        if self.runtime:
            self.runtime.shutdown()
        CLIENT_CONFIG["server_host"] = host
        self.runtime = ClientRuntime(self.ui_queue, {"server_host": host})
        self.runtime.start()
        self._current_host = host
        return self.runtime

    def _login(self) -> None:
        self._submit_auth("login")

    def _register_user(self) -> None:
        self._submit_auth("register")

    def _submit_auth(self, action: str) -> None:
        host = self.host_var.get().strip()
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not host or not username or not password:
            messagebox.showwarning("提示", "请填写服务器、账号和密码")
            return
        runtime = self._ensure_runtime(host)

        # 检查是否已连接到服务器
        if not runtime._connected:
            self.status_var.set("正在连接服务器，请稍候...")
            # 延迟重试，等待连接完成
            self.after(500, lambda: self._submit_auth(action))
            return

        if action == "login":
            runtime.submit(runtime.login(username, password), ("login", username))
            self.status_var.set("正在登录...")
        else:
            runtime.submit(runtime.register(username, password), ("register", username))
            self.status_var.set("正在注册...")

    def _process_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind != "future":
                    # Offline/real-time events should reach the main UI.
                    self.ui_queue.put((kind, payload))
                    break
                tag, future = payload
                self._handle_future(tag, future)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            if self.winfo_exists():
                self.after(120, self._process_queue)
        except tk.TclError:
            return

    def _handle_future(self, tag: Tuple[str, Optional[str]], future: Any) -> None:
        action, meta = tag
        try:
            result = future.result()
        except Exception as exc:
            self._show_status(f"❌ {action} 失败：{exc}", "danger")
            return

        if action == "startup":
            self._show_status(f"✅ {result}", "success")
        elif action in {"login", "register"}:
            if result["success"]:
                if action == "register":
                    # 注册成功：显示提示并保持在登录界面
                    username = result.get("username", "")
                    self._show_status(f"✅ 注册成功！账号 {username} 已创建，请登录", "success")
                    # 清空密码框，方便用户重新输入密码登录
                    self.password_var.set("")
                else:
                    # 登录成功：进入主界面
                    self._show_status(f"✅ {action}成功，正在进入主界面", "success")
                    self.auth_payload = result
                    self.auth_payload["host"] = self._current_host
                    self.auth_payload["success"] = True
                    after_id = self.after(1000, self.destroy)
                    self.after_ids.append(after_id)
            else:
                action_text = "注册" if action == "register" else "登录"
                self._show_status(f"❌ {action_text}失败，请检查账号信息", "danger")

    def _show_status(self, message: str, status_type: str = "normal") -> None:
        colors = {
            "success": ModernStyle.COLORS["success"],
            "warning": ModernStyle.COLORS["warning"],
            "danger": ModernStyle.COLORS["danger"],
            "normal": ModernStyle.COLORS["gray"]
        }
        self.status_var.set(message)
        self.status_label.config(fg=colors.get(status_type, ModernStyle.COLORS["gray"]))

    def _on_close(self) -> None:
        for after_id in self.after_ids:
            try:
                self.after_cancel(after_id)
            except:
                pass
        if self.runtime:
            self.runtime.shutdown()
        self.destroy()


class TkChatApp(tk.Tk):
    """现代化 Tkinter 聊天界面，覆盖在线 / 房间 / 消息等功能（登录流程已前置）。"""

    def __init__(self, runtime: ClientRuntime, auth_payload: Dict[str, Any]) -> None:
        super().__init__()
        self.title(f"💬 Socket Chat - {auth_payload.get('username', '用户')}")
        self.geometry("1400x850")  # 增大默认窗口尺寸
        self.minsize(1200, 700)    # 增大最小尺寸
        self.configure(bg=ModernStyle.COLORS["darkest"])

        self.runtime = runtime
        self.ui_queue = runtime.ui_queue
        self.current_user: Optional[str] = auth_payload.get("username")
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.tree_to_conversation: Dict[str, str] = {}
        self.current_conversation_id: Optional[str] = None
        self.rooms_cache: set[str] = set(auth_payload.get("rooms", []))
        self.room_metadata: Dict[str, Dict[str, Any]] = {}
        self.pending_room_member_callbacks: Dict[str, list] = {}
        self.pending_room_info_callbacks: Dict[str, list] = {}
        self.system_conv_id = "__system__"

        host = self.runtime.config.get("server_host")
        self.status_var = tk.StringVar(value=f"🟢 已连接 | 服务器：{host} | 用户：{self.current_user or '未知'}")
        self.conversation_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.target_mode = tk.StringVar(value="user")
        self.room_metadata: Dict[str, Dict[str, Any]] = {}
        self.pending_room_member_callbacks: Dict[str, list] = {}
        self.room_window: Optional["RoomManagerWindow"] = None
        self.file_transfers: Dict[str, Dict[str, Any]] = {}
        self.file_rows: Dict[str, str] = {}
        self._displayed_message_ids: set[str] = set()  # 添加消息ID去重集合

        # 引用回复相关
        self.reply_to_message: Optional[Dict[str, Any]] = None  # 当前正在引用的消息
        self.message_registry: Dict[str, Dict[str, Any]] = {}  # 消息ID -> 消息完整信息的映射

        self._build_layout()
        self._build_file_panel(self._right_paned)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._queue_job = self.after(100, self._process_queue)

        # 初始化用户列表
        self._initialize_user_lists(auth_payload)

        self._populate_presence(auth_payload.get("roster", []))
        self._populate_rooms(auth_payload.get("rooms", []))
        self._ensure_conversation(self.system_conv_id, "system", "系统日志")
        self._set_current_conversation(self.system_conv_id)
        self._load_initial_history(auth_payload.get("history", []))

    def _initialize_user_lists(self, auth_payload: Dict[str, Any]) -> None:
        """初始化全部用户列表"""
        # 从在线列表开始
        all_users_set = set(auth_payload.get("roster", []))

        # 从历史消息中提取用户
        history = auth_payload.get("history", [])
        for record in history:
            message = record.get("message", {})
            payload = message.get("payload", {})
            sender = payload.get("sender_id")
            if sender:
                all_users_set.add(sender)
            # 从会话ID中提取用户
            conv_id = record.get("conversation_id") or payload.get("conversation_id")
            if conv_id and "|" in conv_id:
                for user in conv_id.split("|"):
                    if user:
                        all_users_set.add(user)

        # 更新全部用户列表
        self._populate_all_users(sorted(all_users_set))

    def _build_layout(self) -> None:
        # 顶部Header
        header = tk.Frame(self, bg=ModernStyle.COLORS["dark"], height=60)
        header.pack(fill=tk.X, padx=10, pady=10)
        header.pack_propagate(False)

        # 标题区域
        title_frame = tk.Frame(header, bg=ModernStyle.COLORS["dark"])
        title_frame.pack(side=tk.LEFT, padx=20)

        icon_label = tk.Label(
            title_frame,
            text="💬",
            font=("Arial", 20),
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["primary"]
        )
        icon_label.pack(side=tk.LEFT)

        title_label = tk.Label(
            title_frame,
            text="Socket Chat",
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        )
        title_label.pack(side=tk.LEFT, padx=(10, 0))

        # 用户信息区域
        user_frame = tk.Frame(header, bg=ModernStyle.COLORS["dark"])
        user_frame.pack(side=tk.RIGHT, padx=20)

        connection_indicator = tk.Label(
            user_frame,
            text="●",
            font=("Arial", 12),
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["success"]
        )
        connection_indicator.pack(side=tk.LEFT, padx=(0, 10))

        user_label = tk.Label(
            user_frame,
            text=f"用户: {self.current_user}",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["lighter"]
        )
        user_label.pack(side=tk.LEFT, padx=(0, 20))

        logout_btn = tk.Button(
            user_frame,
            text="退出登录",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["danger"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["danger_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._logout
        )
        logout_btn.pack(side=tk.LEFT)

        # 主内容区
        main = tk.Frame(self, bg=ModernStyle.COLORS["darkest"])
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 左侧面板
        left = tk.Frame(main, bg=ModernStyle.COLORS["dark"], width=380)  # 再次增大左侧面板宽度
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        # 会话列表
        convo_title = tk.Label(
            left,
            text="💬 会话列表",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"],
            pady=10
        )
        convo_title.pack(fill=tk.X)

        self.conversation_tree = ttk.Treeview(left, show="tree", selectmode="browse", height=15)  # 减小会话列表高度，为用户列表留空间
        tree_scroll = ttk.Scrollbar(left, orient="vertical", command=self.conversation_tree.yview)
        self.conversation_tree.configure(yscrollcommand=tree_scroll.set)
        self.conversation_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        # 只保留群聊和系统，移除双人通信
        self.conv_room_root = self.conversation_tree.insert("", "end", text="🏠 群聊", open=True)
        self.conv_system_root = self.conversation_tree.insert("", "end", text="⚙️ 系统", open=False)
        self.conversation_tree.bind("<<TreeviewSelect>>", self._on_conversation_select)

        # 用户列表（使用Notebook标签页）
        users_title = tk.Label(
            left,
            text="👥 用户列表",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"],
            pady=10
        )
        users_title.pack(fill=tk.X, pady=(10, 0))

        # 创建Notebook来显示不同类型的用户列表
        users_notebook = ttk.Notebook(left)
        users_notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 在线用户标签页
        online_frame = tk.Frame(users_notebook, bg=ModernStyle.COLORS["darker"])
        users_notebook.add(online_frame, text="🟢 在线")

        self.presence_list = tk.Listbox(
            online_frame,
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            selectbackground=ModernStyle.COLORS["primary"],
            selectforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            font=ModernStyle.FONTS["normal"]
        )
        self.presence_list.pack(fill=tk.BOTH, expand=True)
        # 双击在线用户开始聊天
        self.presence_list.bind("<Double-1>", self._on_online_user_double_click)

        # 离线用户标签页
        offline_frame = tk.Frame(users_notebook, bg=ModernStyle.COLORS["darker"])
        users_notebook.add(offline_frame, text="⚪ 离线")

        self.offline_list = tk.Listbox(
            offline_frame,
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            selectbackground=ModernStyle.COLORS["secondary"],
            selectforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            font=ModernStyle.FONTS["normal"]
        )
        self.offline_list.pack(fill=tk.BOTH, expand=True)
        # 双击离线用户也可以开始聊天（发送离线消息）
        self.offline_list.bind("<Double-1>", self._on_offline_user_double_click)

        # 全部用户标签页
        all_users_frame = tk.Frame(users_notebook, bg=ModernStyle.COLORS["darker"])
        users_notebook.add(all_users_frame, text="📋 全部")

        self.all_users_list = tk.Listbox(
            all_users_frame,
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            selectbackground=ModernStyle.COLORS["success"],
            selectforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            font=ModernStyle.FONTS["normal"]
        )
        self.all_users_list.pack(fill=tk.BOTH, expand=True)
        # 双击全部用户列表也可以开始聊天
        self.all_users_list.bind("<Double-1>", self._on_all_users_double_click)

        refresh_btn = tk.Button(
            left,
            text="🔄 刷新用户列表",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,  # 增大按钮内边距
            pady=6,
            cursor="hand2",
            command=self._refresh_presence
        )
        refresh_btn.pack(fill=tk.X, padx=10, pady=5)

        # 房间管理按钮
        room_btn = tk.Button(
            left,
            text="🏠 房间管理",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["secondary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["secondary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,  # 增大按钮内边距
            pady=6,
            cursor="hand2",
            command=self._open_room_window
        )
        room_btn.pack(fill=tk.X, padx=10, pady=5)

        # 好友管理按钮
        friend_btn = tk.Button(
            left,
            text="👥 好友管理",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["success"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["success_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=6,
            cursor="hand2",
            command=self._open_friend_window
        )
        friend_btn.pack(fill=tk.X, padx=10, pady=5)

        # 右侧面板
        right = tk.Frame(main, bg=ModernStyle.COLORS["dark"])
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._right_panel = right

        # 创建主PanedWindow（上中下三部分）
        right_paned = tk.PanedWindow(right, orient=tk.VERTICAL, bg=ModernStyle.COLORS["dark"], sashwidth=5, sashrelief=tk.RAISED)
        right_paned.pack(fill=tk.BOTH, expand=True)
        self._right_paned = right_paned  # 保存引用供后续使用

        # 第一部分：消息显示区域
        msg_display_container = tk.Frame(right_paned, bg=ModernStyle.COLORS["dark"])
        right_paned.add(msg_display_container, minsize=150)

        # 消息记录标题
        chat_title = tk.Label(
            msg_display_container,
            text="💬 消息记录",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"],
            pady=10
        )
        chat_title.pack(fill=tk.X)

        msg_frame = tk.Frame(msg_display_container, bg=ModernStyle.COLORS["darker"])
        msg_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.chat_display = scrolledtext.ScrolledText(
            msg_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            padx=15,
            pady=10,
            relief="flat",
            bd=0
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True)

        # 配置消息标签样式
        self.chat_display.tag_config("system", foreground=ModernStyle.COLORS["gray"], justify="center")
        self.chat_display.tag_config("time", foreground=ModernStyle.COLORS["gray_light"], font=ModernStyle.FONTS["small"])
        self.chat_display.tag_config("self", foreground=ModernStyle.COLORS["primary_light"])
        self.chat_display.tag_config("other", foreground=ModernStyle.COLORS["secondary"])
        self.chat_display.tag_config("command", foreground=ModernStyle.COLORS["success"], font=ModernStyle.FONTS["monospace"])
        self.chat_display.tag_config("quoted", foreground=ModernStyle.COLORS["gray"], background=ModernStyle.COLORS["darker"], lmargin1=20, lmargin2=20)

        # 添加右键菜单用于引用回复
        self.chat_context_menu = tk.Menu(self.chat_display, tearoff=0, bg=ModernStyle.COLORS["card_bg"], fg=ModernStyle.COLORS["light"])
        self.chat_context_menu.add_command(label="📝 引用回复", command=self._quote_selected_message)
        self.chat_display.bind("<Button-3>", self._show_context_menu)  # 右键点击

        # 第二部分：发送消息区域
        composer_container = tk.Frame(right_paned, bg=ModernStyle.COLORS["dark"])
        right_paned.add(composer_container, minsize=180)

        # 发送消息区域
        composer_title = tk.Label(
            composer_container,
            text="📤 发送消息",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"],
            pady=8
        )
        composer_title.pack(fill=tk.X)

        composer = tk.Frame(composer_container, bg=ModernStyle.COLORS["card_bg"])
        composer.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # 模式选择
        mode_frame = tk.Frame(composer, bg=ModernStyle.COLORS["card_bg"])
        mode_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(
            mode_frame,
            text="发送至:",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"]
        ).pack(side=tk.LEFT)

        tk.Radiobutton(
            mode_frame,
            text="私聊",
            value="user",
            variable=self.target_mode,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"],
            selectcolor=ModernStyle.COLORS["primary"],
            activebackground=ModernStyle.COLORS["card_bg"],
            activeforeground=ModernStyle.COLORS["light"]
        ).pack(side=tk.LEFT, padx=10)

        tk.Radiobutton(
            mode_frame,
            text="房间",
            value="room",
            variable=self.target_mode,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"],
            selectcolor=ModernStyle.COLORS["primary"],
            activebackground=ModernStyle.COLORS["card_bg"],
            activeforeground=ModernStyle.COLORS["light"]
        ).pack(side=tk.LEFT)

        # 目标输入
        form_row = tk.Frame(composer, bg=ModernStyle.COLORS["card_bg"])
        form_row.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(
            form_row,
            text="会话ID:",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"],
            width=10
        ).grid(row=0, column=0, sticky=tk.W, pady=2)

        tk.Entry(
            form_row,
            textvariable=self.conversation_var,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=1
        ).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)

        tk.Label(
            form_row,
            text="目标ID:",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"],
            width=10
        ).grid(row=1, column=0, sticky=tk.W, pady=2)

        tk.Entry(
            form_row,
            textvariable=self.target_var,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=1
        ).grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)

        form_row.columnconfigure(1, weight=1)

        # 引用预览区域（默认隐藏）
        self.reply_preview_frame = tk.Frame(composer, bg=ModernStyle.COLORS["card_bg"])
        # 默认不pack，只在有引用时显示

        reply_preview_header = tk.Frame(self.reply_preview_frame, bg=ModernStyle.COLORS["darker"])
        reply_preview_header.pack(fill=tk.X, padx=10, pady=(5, 0))

        tk.Label(
            reply_preview_header,
            text="💬 引用回复",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["primary"]
        ).pack(side=tk.LEFT, padx=5)

        self.cancel_reply_btn = tk.Button(
            reply_preview_header,
            text="✕",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["danger"],
            activebackground=ModernStyle.COLORS["danger"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=5,
            pady=0,
            cursor="hand2",
            command=self._cancel_reply
        )
        self.cancel_reply_btn.pack(side=tk.RIGHT)

        self.reply_preview_label = tk.Label(
            self.reply_preview_frame,
            text="",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["gray_light"],
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=600
        )
        self.reply_preview_label.pack(fill=tk.X, padx=10, pady=(5, 5))

        # 消息输入框
        self.message_input = scrolledtext.ScrolledText(
            composer,
            height=3,
            wrap=tk.WORD,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=1
        )
        self.message_input.pack(fill=tk.X, padx=10, pady=5)
        self.message_input.bind("<Return>", lambda e: "break" if e.state & 0x1 else self._send_message())

        # 操作按钮
        action_row = tk.Frame(composer, bg=ModernStyle.COLORS["card_bg"])
        action_row.pack(fill=tk.X, padx=10, pady=(5, 10))

        send_btn = tk.Button(
            action_row,
            text="📤 发送",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._send_message
        )
        send_btn.pack(side=tk.LEFT, padx=2)

        voice_call_btn = tk.Button(
            action_row,
            text="📞 语音通话",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["success"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["success_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._start_voice_call
        )
        voice_call_btn.pack(side=tk.LEFT, padx=2)

        # 底部状态栏
        footer = tk.Frame(self, bg=ModernStyle.COLORS["dark"], height=25)
        footer.pack(fill=tk.X, padx=10, pady=(0, 10))
        footer.pack_propagate(False)

        status_label = tk.Label(
            footer,
            textvariable=self.status_var,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["gray_light"]
        )
        status_label.pack(side=tk.LEFT, padx=10)

    def _process_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                try:
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
                    elif kind == "friend_event":
                        self._handle_friend_event_ui(payload)
                except Exception as e:
                    # 捕获处理事件时的所有异常，防止程序崩溃
                    logger.error(f"Error processing queue event {kind}: {e}", exc_info=True)
                    try:
                        self._append_log(f"[错误] 处理事件 {kind} 时出错: {e}")
                    except:
                        pass
        except queue.Empty:
            pass
        except tk.TclError:
            return
        except Exception as e:
            # 捕获队列处理中的意外异常
            logger.error(f"Unexpected error in _process_queue: {e}", exc_info=True)
        self._queue_job = self.after(120, self._process_queue)

    def _handle_future(self, tag: Tuple[str, Optional[str]], future: Any) -> None:
        action, meta = tag
        try:
            result = future.result()
        except Exception as exc:
            import traceback
            error_detail = f"{type(exc).__name__}: {exc}"
            self._append_log(f"[错误] {action}: {error_detail}")
            self._set_status(f"{action} 失败：{error_detail}")
            # 打印详细的错误堆栈（调试用）
            print(f"Error in {action}:")
            traceback.print_exc()
            return

        if action and action.startswith("file_"):
            self._handle_file_future(action, result, meta)
            return

        if action == "startup":
            self._append_log(result)
            self._set_status(result)
        elif action == "logout":
            self.current_user = None
            self._set_status("已退出登录，正在返回登录界面...")
            self._append_log("✅ 已退出登录")
            # 延迟关闭主窗口并重新打开登录窗口
            self.after(1000, self._return_to_login)
        elif action == "send_text":
            self._add_message_to_conversation(
                result["conversation_id"],
                "user",
                self.current_user or "我",
                result["text"],
                other_id=result.get("target_id"),
                timestamp=int(time.time()),
                reply_to=result.get("reply_to"),
            )
        elif action == "send_room":
            convo_id = result.get("conversation_id") or result.get("room_id")
            self._add_message_to_conversation(convo_id, "room", self.current_user or "我", result["text"], timestamp=int(time.time()), reply_to=result.get("reply_to"))
        elif action == "presence_refresh":
            self._populate_presence(result)
            self._set_status("在线列表已刷新")
        elif action == "rooms_refresh":
            self._populate_rooms(result)
            self._set_status("房间列表已刷新")
        elif action == "room_create":
            room_id = result.get('room_id')
            status = result.get('status')
            self._set_status(f"创建房间 {room_id} 状态：{status}")
            self._append_log(f"创建房间 {room_id} 状态：{status}")
            self._update_room_metadata(result)
            # 立即将房间添加到 rooms_cache 中，确保后续消息能正确识别为群聊
            if room_id and str(status) == str(int(StatusCode.SUCCESS)):
                self.rooms_cache.add(room_id)
                self._ensure_conversation(room_id, "room", f"群聊：{room_id}")
            self._refresh_rooms()
            self._notify_room_window(action, result, meta)
        elif action == "room_join":
            room_id = result.get('room_id')
            status = result.get('status')
            self._set_status(f"加入房间 {room_id} 状态：{status}")
            self._append_log(f"加入房间 {room_id} 状态：{status}")
            self._update_room_metadata(result)
            # 立即将房间添加到 rooms_cache 中，确保后续消息能正确识别为群聊
            if room_id and str(status) == str(int(StatusCode.SUCCESS)):
                self.rooms_cache.add(room_id)
                self._ensure_conversation(room_id, "room", f"群聊：{room_id}")
            self._refresh_rooms()
            self._notify_room_window(action, result, meta)
        elif action == "room_leave":
            room_id = result.get('room_id')
            status = result.get('status')
            self._set_status(f"离开房间 {room_id} 状态：{status}")
            self._append_log(f"离开房间 {room_id} 状态：{status}")
            # 从 rooms_cache 中移除
            if room_id and str(status) == str(int(StatusCode.SUCCESS)):
                self.rooms_cache.discard(room_id)
            self._refresh_rooms()
            self._notify_room_window(action, result, meta)
        elif action == "room_members":
            self._dispatch_room_members(meta, result)
        elif action == "room_info":
            self._dispatch_room_info(meta, result)
        elif action == "room_kick":
            room_id = result.get('room_id')
            user_id = result.get('user_id')
            status = result.get('status')
            self._set_status(f"踢出成员 {user_id} 状态：{status}")
            self._append_log(f"踢出成员 {user_id} 状态：{status}")
            # 刷新房间信息
            if room_id and str(status) == str(int(StatusCode.SUCCESS)):
                self._refresh_rooms()
                # 通知房间窗口刷新成员列表
                if self.room_window and self.room_window.winfo_exists():
                    self.room_window._request_details(room_id)
        elif action == "room_delete":
            room_id = result.get('room_id')
            status = result.get('status')
            self._set_status(f"解散房间 {room_id} 状态：{status}")
            self._append_log(f"解散房间 {room_id} 状态：{status}")
            # 从 rooms_cache 中移除
            if room_id and str(status) == str(int(StatusCode.SUCCESS)):
                self.rooms_cache.discard(room_id)
            self._refresh_rooms()
            self._notify_room_window(action, result, meta)
        elif action == "room_invite":
            room_id = result.get('room_id')
            user_id = result.get('user_id')
            status = result.get('status')
            message = result.get('message', '')

            if str(status) == str(int(StatusCode.SUCCESS)):
                self._set_status(f"✅ 已向 {user_id} 发送房间邀请")
                self._append_log(f"✅ 已向 {user_id} 发送房间 {room_id} 的邀请")
            else:
                error_msg = result.get('error_message', '邀请失败')
                self._set_status(f"❌ 邀请失败：{error_msg}")
                self._append_log(f"❌ 邀请 {user_id} 加入房间 {room_id} 失败：{error_msg}")

    def _handle_file_future(self, action: str, result: Any, meta: Optional[str]) -> None:
        if action == "file_send" and isinstance(result, dict):
            sessions = result.get("sessions") or []
            if not sessions and result.get("session_id"):
                sessions = [{"session_id": result.get("session_id")}]
            for session in sessions:
                self._update_file_transfer(
                    session.get("session_id"),
                    file_name=result.get("file_name"),
                    direction="发送",
                    total=result.get("file_size"),
                    status="等待对方确认",
                )
            self._set_status(f"文件 {result.get('file_name')} 请求已发送")
        elif action == "file_accept":
            self._set_status("已同意文件传输请求")
        elif action == "file_reject":
            self._set_status("已拒绝文件传输请求")

    def _handle_file_event(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type")
        print(f"[DEBUG] 文件事件: type={event_type}, session_id={event.get('session_id')}, file={event.get('file_name')}")

        if event_type == "incoming_request":
            self._prompt_file_request(event)
        elif event_type == "request_sent":
            session_id = event.get("session_id")
            file_name = event.get("file_name")
            print(f"[DEBUG] 发送文件请求: session_id={session_id}, file={file_name}")
            self._update_file_transfer(
                session_id,
                file_name=file_name,
                total=event.get("file_size"),
                direction="发送",
                status="等待对方确认",
            )
        elif event_type == "progress":
            direction = "发送" if event.get("direction") == "send" else "接收"
            self._update_file_transfer(
                event.get("session_id"),
                bytes_transferred=event.get("bytes"),
                total=event.get("total"),
                direction=direction,
                status="传输中",
            )
        elif event_type == "completed":
            # 完成时确保进度是100%
            session_id = event.get("session_id")
            if session_id and session_id in self.file_transfers:
                total = self.file_transfers[session_id].get("total", 0)
                self._update_file_transfer(
                    session_id,
                    bytes_transferred=total,  # 设置为总字节数，确保100%
                    status="✅ 完成"
                )
            else:
                self._update_file_transfer(session_id, status="✅ 完成")
        elif event_type == "failed":
            self._update_file_transfer(event.get("session_id"), status="失败")
            self._append_log(f"文件传输失败：{event.get('error')}")
        elif event_type == "saved":
            self._append_log(f"文件已保存到 {event.get('path')}")
        elif event_type == "rejected":
            self._update_file_transfer(event.get("session_id"), status="已被拒绝")

    def _prompt_file_request(self, event: Dict[str, Any]) -> None:
        session_id = event.get("session_id")
        file_name = event.get("file_name") or "未知文件"
        from_user = event.get("from_user") or "未知用户"
        size_text = self._format_size(event.get("file_size") or 0)
        if not session_id:
            return
        accept = messagebox.askyesno(
            "文件请求",
            f"{from_user} 想发送文件 {file_name} ({size_text})，是否接收？",
        )
        if accept:
            save_path = filedialog.asksaveasfilename(title="保存文件", initialfile=file_name)
            if save_path:
                self._update_file_transfer(
                    session_id,
                    file_name=file_name,
                    total=event.get("file_size"),
                    direction="接收",
                    status="等待建立通道",
                )
                self.runtime.submit(self.runtime.accept_file_transfer(session_id, save_path), ("file_accept", session_id))
            else:
                self.runtime.submit(self.runtime.reject_file_transfer(session_id), ("file_reject", session_id))
        else:
            self.runtime.submit(self.runtime.reject_file_transfer(session_id), ("file_reject", session_id))

    def _update_file_transfer(
        self,
        session_id: Optional[str],
        *,
        file_name: Optional[str] = None,
        direction: Optional[str] = None,
        total: Optional[int] = None,
        bytes_transferred: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        if not session_id:
            return

        print(f"[DEBUG] _update_file_transfer: session_id={session_id}, file={file_name}, status={status}")
        print(f"[DEBUG] 当前 file_rows keys: {list(self.file_rows.keys())}")

        info = self.file_transfers.setdefault(
            session_id,
            {
                "file": file_name or "未知文件",
                "direction": direction or "发送",
                "total": total or 0,
                "bytes": 0,
                "status": status or "",
            },
        )
        if file_name:
            info["file"] = file_name
        if direction:
            info["direction"] = direction
        if total is not None:
            info["total"] = total or 0
        if bytes_transferred is not None:
            info["bytes"] = bytes_transferred or 0
        if status:
            info["status"] = status

        # 计算进度
        progress_text = "--"
        if info["total"]:
            percent = min(100, int(info["bytes"] * 100 / info["total"]))
            # 创建进度条（20个字符宽）
            filled = int(percent / 5)  # 每5%一个方块
            bar = "█" * filled + "░" * (20 - filled)
            progress_text = f"{bar} {percent}%"

        # 格式化文件大小
        size_text = self._format_size(info["total"])

        row_id = self.file_rows.get(session_id)
        values = (
            info["file"],
            size_text,
            info["direction"],
            progress_text,
            info.get("status", "")
        )
        if not row_id:
            print(f"[DEBUG] 插入新行: session_id={session_id}, file={info['file']}")
            row_id = self.file_tree.insert("", "end", values=values)
            self.file_rows[session_id] = row_id
            print(f"[DEBUG] 新行已插入: row_id={row_id}")
        else:
            print(f"[DEBUG] 更新现有行: session_id={session_id}, row_id={row_id}")
            self.file_tree.item(row_id, values=values)

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}TB"

    def _send_file(self) -> None:
        if not self.current_user:
            messagebox.showwarning("提示", "请先登录并选择目标")
            return
        target = self.target_var.get().strip()
        if not target:
            messagebox.showwarning("提示", "请输入目标 ID")
            return
        file_path = filedialog.askopenfilename(title="选择要发送的文件")
        if not file_path:
            return
        target_type = "room" if self.target_mode.get() == "room" else "user"
        self.runtime.submit(
            self.runtime.send_file(target, file_path, target_type),
            ("file_send", target),
        )

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        return f"{size / (1024 * 1024 * 1024):.1f}GB"

    def _append_message(self, message: Dict[str, Any], is_history: bool = False) -> None:
        payload = message.get("payload", {})
        content = payload.get("content", {})
        text = content.get("text") if isinstance(content, dict) else str(content)
        convo = payload.get("conversation_id") or "N/A"
        sender = payload.get("sender_id", "unknown")
        conv_type = "room" if convo in self.rooms_cache else "user"
        other_id = sender if conv_type == "user" and sender != self.current_user else None
        timestamp = message.get("timestamp", int(time.time()))
        reply_to = content.get("reply_to") if isinstance(content, dict) else None

        # 调试输出：打印消息信息
        message_id = message.get("id")
        text_preview = str(text)[:20] if text else "(空消息)"
        print(f"[DEBUG] _append_message: msg_id={message_id}, sender={sender}, current_user={self.current_user}, is_history={is_history}, text={text_preview}...")

        # 将新用户添加到全部用户列表
        self._add_to_all_users(sender)

        # 使用消息ID进行去重
        self._add_message_to_conversation(convo, conv_type, sender, text or "", other_id=other_id, message_id=message_id, is_history=is_history, timestamp=timestamp, reply_to=reply_to)

    def _add_to_all_users(self, user_id: str) -> None:
        """将用户添加到全部用户列表（如果不存在）"""
        if not user_id or user_id == self.current_user:
            return

        all_users = set(self.all_users_list.get(0, tk.END))
        if user_id not in all_users:
            # 按字母顺序插入
            all_users.add(user_id)
            self._populate_all_users(sorted(all_users))

    def _append_log(self, text: str) -> None:
        self._add_message_to_conversation(self.system_conv_id, "system", "系统", text, timestamp=int(time.time()))

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _notify_room_window(self, action: str, payload: Dict[str, Any], room_id: Optional[str]) -> None:
        if self.room_window and self.room_window.winfo_exists():
            if action == "room_members":
                self.room_window.handle_members(room_id, payload.get("members", []))
            elif action == "room_info":
                self.room_window.handle_room_info(room_id, payload)
            else:
                self.room_window.handle_action(action, payload, room_id)

    def _dispatch_room_members(self, room_id: Optional[str], payload: Dict[str, Any]) -> None:
        members = payload.get("members", [])
        if room_id:
            meta = self.room_metadata.setdefault(room_id, {})
            meta["members"] = members
        callbacks = self.pending_room_member_callbacks.pop(room_id, []) if room_id else []
        for callback in callbacks:
            try:
                callback(members)
            except Exception:
                pass
        self._notify_room_window("room_members", payload, room_id)

    def _dispatch_room_info(self, room_id: Optional[str], payload: Dict[str, Any]) -> None:
        if not room_id:
            return
        meta = self.room_metadata.setdefault(room_id, {})
        for key in ("owner", "created_at", "encrypted"):
            if payload.get(key) is not None:
                meta[key] = payload[key]
        if payload.get("members") is not None:
            meta["members"] = payload["members"]
        callbacks = self.pending_room_info_callbacks.pop(room_id, [])
        for callback in callbacks:
            try:
                callback(payload)
            except Exception:
                pass
        self._notify_room_window("room_info", payload, room_id)

    def _update_room_metadata(self, payload: Dict[str, Any]) -> None:
        room_id = payload.get("room_id")
        if not room_id:
            return
        meta = self.room_metadata.setdefault(room_id, {})
        for key in ("encrypted", "owner", "created_at"):
            if key in payload and payload[key] is not None:
                meta[key] = payload[key]
        if "members" in payload and payload["members"]:
            meta["members"] = payload["members"]

    def _populate_presence(self, roster: list[str]) -> None:
        """更新在线用户列表，同时更新离线和全部用户列表"""
        # 更新在线用户列表
        self.presence_list.delete(0, tk.END)
        for user in roster:
            if user != self.current_user:  # 不显示自己
                self.presence_list.insert(tk.END, user)

        # 更新离线用户列表（从全部用户中排除在线用户）
        all_users = set(self.all_users_list.get(0, tk.END))
        online_users = set(roster)
        offline_users = all_users - online_users - {self.current_user}

        self.offline_list.delete(0, tk.END)
        for user in sorted(offline_users):
            self.offline_list.insert(tk.END, user)

    def _populate_all_users(self, all_users: list[str]) -> None:
        """更新全部用户列表"""
        self.all_users_list.delete(0, tk.END)
        for user in sorted(all_users):
            if user != self.current_user:  # 不显示自己
                self.all_users_list.insert(tk.END, user)

    def _on_online_user_double_click(self, event: Any) -> None:
        """双击在线用户开始聊天"""
        selection = self.presence_list.curselection()
        if not selection:
            return
        target_user = self.presence_list.get(selection[0])
        self._start_private_chat(target_user)

    def _on_offline_user_double_click(self, event: Any) -> None:
        """双击离线用户开始聊天（离线消息）"""
        selection = self.offline_list.curselection()
        if not selection:
            return
        target_user = self.offline_list.get(selection[0])
        self._start_private_chat(target_user, is_offline=True)

    def _on_all_users_double_click(self, event: Any) -> None:
        """双击全部用户列表开始聊天"""
        selection = self.all_users_list.curselection()
        if not selection:
            return
        target_user = self.all_users_list.get(selection[0])
        self._start_private_chat(target_user)

    def _start_private_chat(self, target_user: str, is_offline: bool = False) -> None:
        """开始与指定用户的私聊"""
        if not target_user or not self.current_user:
            return

        # 创建会话ID（按字母顺序排序）
        users = sorted([self.current_user, target_user])
        conversation_id = f"{users[0]}|{users[1]}"

        # 确保会话存在
        self._ensure_conversation(
            conversation_id,
            "user",
            f"💬 与 {target_user} 的对话",
            other_id=target_user
        )

        # 切换到该会话
        self._set_current_conversation(conversation_id)

        # 自动设置目标ID
        self.target_mode.set("user")
        self.conversation_var.set(conversation_id)
        self.target_var.set(target_user)

        if is_offline:
            self._append_log(f"已开始与 {target_user} 的对话（用户离线，将发送离线消息）")
        else:
            self._append_log(f"已开始与 {target_user} 的对话")

    def _handle_presence_update(self, event: Dict[str, Any]) -> None:
        """处理实时在线状态更新"""
        user_id = event.get("user_id")
        state = event.get("state")

        if not user_id or user_id == self.current_user:
            return

        # 确保用户在全部用户列表中
        self._add_to_all_users(user_id)

        # 获取当前在线列表
        current_online = set(self.presence_list.get(0, tk.END))

        if state == "online":
            # 用户上线
            if user_id not in current_online:
                self.presence_list.insert(tk.END, user_id)
                self._append_log(f"🟢 {user_id} 上线了")

                # 从离线列表中移除
                for i in range(self.offline_list.size()):
                    if self.offline_list.get(i) == user_id:
                        self.offline_list.delete(i)
                        break
        elif state == "offline":
            # 用户下线
            if user_id in current_online:
                # 从在线列表中删除
                for i in range(self.presence_list.size()):
                    if self.presence_list.get(i) == user_id:
                        self.presence_list.delete(i)
                        break
                self._append_log(f"⚪ {user_id} 离线了")

                # 添加到离线列表
                # 检查是否在全部用户列表中
                all_users = set(self.all_users_list.get(0, tk.END))
                if user_id in all_users:
                    # 按字母顺序插入
                    offline_users = list(self.offline_list.get(0, tk.END))
                    offline_users.append(user_id)
                    offline_users.sort()
                    self.offline_list.delete(0, tk.END)
                    for user in offline_users:
                        self.offline_list.insert(tk.END, user)

    def _prompt_room_password(self, room_id: str) -> Optional[str]:
        """提示用户输入房间密码"""
        dialog = tk.Toplevel(self)
        dialog.title("输入房间密码")
        dialog.geometry("350x120")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        result = {"password": None}

        ttk.Label(dialog, text=f"房间 {room_id} 需要密码:").pack(pady=(20, 10))

        password_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=password_var, width=30, show="*")
        entry.pack(pady=5)
        entry.focus()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=15)

        def on_confirm():
            result["password"] = password_var.get().strip()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(button_frame, text="确定", command=on_confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=on_cancel).pack(side=tk.LEFT, padx=5)

        entry.bind("<Return>", lambda e: on_confirm())
        entry.bind("<Escape>", lambda e: on_cancel())

        dialog.wait_window()
        return result["password"] if result["password"] else None

    def _populate_rooms(self, rooms: list[str]) -> None:
        rooms = rooms or []
        self.rooms_cache = set(rooms)
        self._sync_room_conversations(rooms)
        self._notify_room_window("rooms_refresh", {"rooms": rooms, "status": int(StatusCode.SUCCESS)}, None)

    def _build_file_panel(self, parent: tk.Widget) -> None:
        # 下半部分：文件传输面板容器
        file_container = tk.Frame(parent, bg=ModernStyle.COLORS["dark"])
        parent.add(file_container, minsize=200)  # 增大最小高度以容纳进度条

        # 文件传输标题
        file_title = tk.Label(
            file_container,
            text="📎 文件传输",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"],
            pady=8
        )
        file_title.pack(fill=tk.X)

        frame = tk.Frame(file_container, bg=ModernStyle.COLORS["card_bg"])
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        controls = tk.Frame(frame, bg=ModernStyle.COLORS["card_bg"])
        controls.pack(fill=tk.X, padx=10, pady=10)

        send_file_btn = tk.Button(
            controls,
            text="📎 选择文件并发送",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._send_file
        )
        send_file_btn.pack(side=tk.LEFT, padx=2)

        # 创建包含表格和进度条的容器
        tree_container = tk.Frame(frame, bg=ModernStyle.COLORS["card_bg"])
        tree_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.file_tree = ttk.Treeview(
            tree_container,
            columns=("file", "size", "direction", "progress", "status"),
            show="headings",
            height=5,
        )
        for col, title, width, anchor in (
            ("file", "文件", 200, tk.W),
            ("size", "大小", 80, tk.CENTER),
            ("direction", "方向", 60, tk.CENTER),
            ("progress", "进度", 150, tk.CENTER),
            ("status", "状态", 120, tk.W),
        ):
            self.file_tree.heading(col, text=title)
            self.file_tree.column(col, width=width, anchor=anchor)

        # 添加滚动条
        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=tree_scroll.set)

        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 存储进度条的字典
        self.file_progress_bars = {}

    def _load_initial_history(self, records: Optional[list[Dict[str, Any]]]) -> None:
        if not records:
            return
        for record in records:
            message = self._convert_history_record(record)
            if not message:
                continue
            # 过滤掉无效消息：没有msg_id或内容为空的消息
            msg_id = message.get("id")
            payload = message.get("payload", {})
            content = payload.get("content", {})
            text = content.get("text") if isinstance(content, dict) else str(content) if content else None

            if not msg_id or not text or not str(text).strip():
                print(f"[DEBUG] 过滤无效消息：msg_id={msg_id}, text={text}")
                continue

            self._append_message(message, is_history=True)
        # 不要清空未读计数！历史消息加载后，未读计数应该保持
        # 只有当用户真正查看会话时，才在 _set_current_conversation 中清空未读

    def _convert_history_record(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        payload = (message.get("payload") or {}).copy()
        conversation_id = record.get("conversation_id") or payload.get("conversation_id")
        if not conversation_id:
            return None
        payload.setdefault("conversation_id", conversation_id)
        direction = record.get("direction")

        # 调试输出：查看原始数据
        original_sender = payload.get("sender_id", "无")
        print(f"[DEBUG] _convert_history_record: direction={direction}, original_sender={original_sender}, msg_id={message.get('id')}")

        # 只为 outbound 消息（用户自己发送的）设置 sender_id
        # 对于 inbound 消息（接收到的），必须保留原始的 sender_id，不能修改
        if direction == "outbound":
            # 只有当消息中没有 sender_id 时，才设置为当前用户
            if not payload.get("sender_id"):
                payload["sender_id"] = self.current_user or "me"
                print(f"[DEBUG]   -> outbound 设置 sender_id: {payload['sender_id']}")

            # 确保消息格式正确
            if message.get("type") != "event":
                message = {
                    "id": message.get("id"),
                    "type": "event",
                    "timestamp": message.get("timestamp") or record.get("created_at") or int(time.time()),
                    "command": MsgType.MESSAGE_EVENT.value,
                    "headers": message.get("headers") or {},
                    "payload": payload,
                }
            else:
                message["payload"] = payload
        else:
            # inbound 消息：保留原始的 sender_id，不做任何修改
            # 这样接收到的消息才能正确显示发送者
            print(f"[DEBUG]   -> inbound 保留原始 sender_id: {payload.get('sender_id', '无')}")
            message["payload"] = payload

        return message

    def _sync_room_conversations(self, rooms: list[str]) -> None:
        for room in rooms:
            self._ensure_conversation(room, "room", f"群聊：{room}")

    def _ensure_conversation(
        self,
        conv_id: str,
        conv_type: str,
        title: Optional[str] = None,
        other_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not conv_id:
            return None
        conversation = self.conversations.get(conv_id)
        if conversation:
            if title and title != conversation["base_title"]:
                conversation["base_title"] = title
                self._update_conversation_tree_text(conv_id)
            if other_id:
                conversation["other_id"] = other_id
            return conversation

        if conv_type == "room":
            parent = self.conv_room_root
            base_title = title or f"群聊：{conv_id}"
        elif conv_type == "user":
            # 私聊不再有父节点，直接显示在顶层最前面
            parent = ""
            other = other_id or self._extract_other_from_conversation_id(conv_id)
            base_title = title or self._format_private_title(other, conv_id)
        else:
            parent = self.conv_system_root
            base_title = title or "系统日志"

        # 私聊插入到最前面（在群聊和系统之前）
        if conv_type == "user":
            tree_id = self.conversation_tree.insert("", 0, text=base_title, open=False)
        else:
            tree_id = self.conversation_tree.insert(parent, "end", text=base_title, open=False)

        conversation = {
            "type": conv_type,
            "base_title": base_title,
            "messages": [],
            "unread": 0,
            "tree_id": tree_id,
        }
        if conv_type == "user" and other_id:
            conversation["other_id"] = other_id
        if conv_type == "room":
            conversation["room_id"] = conv_id
        self.conversations[conv_id] = conversation
        self.tree_to_conversation[tree_id] = conv_id
        if self.current_conversation_id is None and conv_type != "system":
            self._set_current_conversation(conv_id)
        return conversation

    def _update_conversation_tree_text(self, conv_id: str) -> None:
        conversation = self.conversations.get(conv_id)
        if not conversation:
            return
        text = conversation["base_title"]
        unread = conversation.get("unread", 0)
        if unread and conversation["type"] != "system":
            text = f"{text} (未读{unread})"
        self.conversation_tree.item(conversation["tree_id"], text=text)

    def _set_current_conversation(self, conv_id: str) -> None:
        conversation = self.conversations.get(conv_id)
        if not conversation:
            return
        self.current_conversation_id = conv_id
        conversation["unread"] = 0
        current_selection = self.conversation_tree.selection()
        if conversation["tree_id"] not in current_selection:
            self.conversation_tree.selection_set(conversation["tree_id"])
        self._update_conversation_tree_text(conv_id)
        self._render_conversation_messages(conv_id)
        if conversation["type"] == "user":
            self.target_mode.set("user")
            self.conversation_var.set(conv_id)
            other = conversation.get("other_id") or self._extract_other_from_conversation_id(conv_id)
            if other:
                conversation["other_id"] = other
                self.target_var.set(other)
        elif conversation["type"] == "room":
            self.target_mode.set("room")
            self.conversation_var.set(conv_id)
            self.target_var.set(conv_id)

    def _render_conversation_messages(self, conv_id: str) -> None:
        conversation = self.conversations.get(conv_id)
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        if conversation:
            for line in conversation["messages"]:
                self.chat_display.insert(tk.END, f"{line}\n")
        self.chat_display.configure(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _append_to_chat_display(self, line: str) -> None:
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"{line}\n")
        self.chat_display.configure(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _add_message_to_conversation(
        self,
        conv_id: str,
        conv_type: str,
        sender: str,
        text: str,
        other_id: Optional[str] = None,
        message_id: Optional[str] = None,
        is_history: bool = False,
        timestamp: Optional[int] = None,
        reply_to: Optional[Dict[str, Any]] = None,
    ) -> None:
        # 消息去重：检查消息ID是否已经显示过
        if message_id and str(message_id).strip():  # 确保 message_id 不为空
            if message_id in self._displayed_message_ids:
                # 消息已经显示过，跳过
                print(f"[DEBUG] 消息去重：跳过重复消息 msg_id={message_id}, sender={sender}")
                return
            self._displayed_message_ids.add(message_id)
            text_preview = str(text)[:20] if text else "(空消息)"
            print(f"[DEBUG] 添加新消息：msg_id={message_id}, sender={sender}, is_history={is_history}, text={text_preview}...")

        conversation = self._ensure_conversation(conv_id, conv_type, other_id=other_id)
        if conversation is None:
            return
        if conv_type == "user":
            other = other_id or conversation.get("other_id")
            if other:
                conversation["other_id"] = other
                conversation["base_title"] = self._format_private_title(other, conv_id)
                self._update_conversation_tree_text(conv_id)

        # 格式化时间戳
        if timestamp is None:
            timestamp = int(time.time())
        time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))

        # 保存到消息注册表
        if message_id:
            self.message_registry[message_id] = {
                "sender": sender,
                "text": text,
                "time_str": time_str,
                "timestamp": timestamp,
                "reply_to": reply_to
            }

        # 如果有引用，添加引用信息行
        lines = []
        if reply_to:
            quoted_sender = reply_to.get("sender", "未知")
            quoted_text = reply_to.get("text", "")
            quoted_time = reply_to.get("time_str", "")
            # 引用消息显示为灰色缩进
            lines.append(f"  ┌ 引用 [{quoted_time}] {quoted_sender}: {quoted_text[:50]}{'...' if len(quoted_text) > 50 else ''}")

        # 新格式：[时间] [发送者] 消息内容
        lines.append(f"[{time_str}] [{sender}] {text}")

        for line in lines:
            conversation["messages"].append(line)

        if self.current_conversation_id == conv_id:
            for line in lines:
                self._append_to_chat_display(line)
        else:
            # 历史消息不应该增加未读计数
            if conv_type != "system" and not is_history:
                conversation["unread"] = conversation.get("unread", 0) + 1
            self._update_conversation_tree_text(conv_id)

    def _format_private_title(self, other_id: Optional[str], conversation_id: Optional[str]) -> str:
        names = [self.current_user, other_id]
        names = [name for name in names if name]
        if len(names) == 2:
            ordered = sorted(names)
            return f"双人通信：{ordered[0]}-{ordered[1]}"
        if conversation_id and "|" in conversation_id:
            ordered = "-".join(part for part in conversation_id.split("|") if part)
            return f"双人通信：{ordered}"
        if other_id and other_id != self.current_user:
            return f"双人通信：{other_id}"
        return f"双人通信：{conversation_id or '未知'}"

    def _extract_other_from_conversation_id(self, conversation_id: Optional[str]) -> Optional[str]:
        if not conversation_id:
            return None
        if "|" in conversation_id:
            for part in conversation_id.split("|"):
                if part and part != self.current_user:
                    return part
        if conversation_id != self.current_user:
            return conversation_id
        return None

    def request_room_members(self, room_id: str, callback) -> None:
        if not room_id:
            return
        self.pending_room_member_callbacks.setdefault(room_id, []).append(callback)
        self.runtime.submit(self.runtime.list_room_members(room_id), ("room_members", room_id))

    def request_room_info(self, room_id: str, callback) -> None:
        if not room_id:
            return
        self.pending_room_info_callbacks.setdefault(room_id, []).append(callback)
        self.runtime.submit(self.runtime.room_info(room_id), ("room_info", room_id))

    def submit_room_create(self, room_id: str, encrypted: bool, password: Optional[str] = None) -> None:
        if room_id:
            self.runtime.submit(
                self.runtime.create_room(room_id, encrypted, password),
                ("room_create", room_id),
            )

    def submit_room_join(self, room_id: str, password: Optional[str] = None) -> None:
        if room_id:
            self.runtime.submit(self.runtime.join_room(room_id, password), ("room_join", room_id))

    def submit_room_leave(self, room_id: str) -> None:
        if room_id:
            self.runtime.submit(self.runtime.leave_room(room_id), ("room_leave", room_id))

    def _open_room_window(self) -> None:
        if self.room_window and self.room_window.winfo_exists():
            self.room_window.lift()
            self.room_window.focus_set()
            return
        self.room_window = RoomManagerWindow(self)

    def _open_friend_window(self) -> None:
        if hasattr(self, 'friend_window') and self.friend_window and self.friend_window.winfo_exists():
            self.friend_window.lift()
            self.friend_window.focus_set()
            return
        self.friend_window = FriendManagerWindow(self)


    def _logout(self) -> None:
        if not self.current_user:
            return
        self.runtime.submit(self.runtime.logout(), ("logout", self.current_user))

    def _refresh_presence(self) -> None:
        self.runtime.submit(self.runtime.refresh_presence(), ("presence_refresh", None))

    def _refresh_rooms(self) -> None:
        self.runtime.submit(self.runtime.refresh_rooms(), ("rooms_refresh", None))

    def _send_message(self) -> None:
        if not self.current_user:
            messagebox.showwarning("提示", "请先登录")
            return
        text = self.message_input.get("1.0", tk.END).strip()
        if not text:
            return
        mode = self.target_mode.get()
        conversation = self.conversation_var.get().strip()
        target = self.target_var.get().strip()

        # 准备引用信息
        reply_to = None
        if self.reply_to_message:
            reply_to = {
                "message_id": self.reply_to_message["message_id"],
                "sender": self.reply_to_message["sender"],
                "text": self.reply_to_message["text"],
                "time_str": self.reply_to_message["time_str"]
            }

        if mode == "user":
            if not target:
                messagebox.showwarning("提示", "请输入目标用户 ID")
                return

            # 检查是否是好友
            if not self.runtime.friends.is_friend(target):
                result = messagebox.askyesno(
                    "提示",
                    f"你和 {target} 不是好友\n\n私聊需要先添加好友\n\n是否打开好友管理窗口？"
                )
                if result:
                    self._open_friend_window()
                return

            conversation_id = conversation or f"{self.current_user}|{target}"
            self.runtime.submit(
                self.runtime.send_direct(conversation_id, target, text, reply_to),
                ("send_text", conversation_id),
            )
        else:
            if not target:
                messagebox.showwarning("提示", "请输入房间 ID")
                return
            self.runtime.submit(
                self.runtime.send_room(target, text, conversation or None, reply_to),
                ("send_room", target),
            )

        # 清空输入框和引用
        self.message_input.delete("1.0", tk.END)
        if self.reply_to_message:
            self._cancel_reply()

    def _on_conversation_select(self, event: Any) -> None:
        selection = self.conversation_tree.selection()
        if not selection:
            return
        conv_id = self.tree_to_conversation.get(selection[0])
        if conv_id:
            self._set_current_conversation(conv_id)

    def _return_to_login(self) -> None:
        """退出登录后返回登录界面"""
        # 关闭当前主窗口
        self._on_close()
        # 重新启动登录窗口
        login = LoginWindow()
        login.mainloop()
        # 如果登录成功，打开新的聊天窗口
        runtime = getattr(login, "runtime", None)
        auth_payload = getattr(login, "auth_payload", None)
        if runtime and auth_payload and auth_payload.get("success"):
            app = TkChatApp(runtime, auth_payload)
            app.mainloop()

    def _start_voice_call(self) -> None:
        """Start a voice call to the current target."""
        if not self.current_user:
            messagebox.showwarning("提示", "请先登录")
            return

        mode = self.target_mode.get()
        target = self.target_var.get().strip()

        if not target:
            messagebox.showwarning("提示", "请先选择通话对象")
            return

        target_type = "room" if mode == "room" else "user"
        call_type = "group" if mode == "room" else "direct"

        self.runtime.submit(
            self.runtime.initiate_voice_call(target_type, target, call_type),
            ("voice_call", target)
        )
        self._set_status(f"正在呼叫 {target}...")
        # 立即显示通话控制窗口
        self.after(500, self._show_voice_call_controls)

    def _handle_voice_event(self, event: Dict[str, Any]) -> None:
        """Handle voice call events."""
        event_type = event.get("type")
        data = event.get("data")

        if event_type == "incoming_call":
            # Incoming call notification
            self._show_incoming_call_dialog(data)
        elif event_type == "status":
            # Status update
            self._set_status(str(data))
            self._append_log(f"[语音通话] {data}")
            # 如果通话已接通且没有显示控制窗口，则显示
            if "已接通" in str(data):
                if not (hasattr(self, 'voice_control_window') and
                       self.voice_control_window and
                       self.voice_control_window.winfo_exists()):
                    self.after(100, self._show_voice_call_controls)
        elif event_type == "call_ended":
            # 处理通话结束事件
            self._handle_call_ended(data)
        elif event_type == "error":
            # Error message
            self._set_status(f"语音通话错误: {data}")
            self._append_log(f"[语音通话错误] {data}")
            messagebox.showerror("语音通话错误", str(data))
        elif event_type == "members_changed":
            # Group call members changed
            members = data if isinstance(data, list) else []
            members_str = ', '.join(members)
            self._append_log(f"[语音通话] 当前参与者 ({len(members)}人): {members_str}")
            # 更新状态栏
            self._set_status(f"语音通话中 - {len(members)}人参与")

    def _handle_call_ended(self, call_info: Dict[str, Any]) -> None:
        """
        处理通话结束事件，向聊天框添加通话结束消息。
        注意：窗口关闭由update_duration自动处理，这里不关闭窗口。

        Args:
            call_info: 通话结束信息,包含 duration_str, end_source, other_party, target_type等
        """
        try:
            logger.info(f"[CALL_ENDED] Starting to handle call ended event: {call_info}")

            # 注意：不在这里关闭窗口，让update_duration检测通话结束后自己关闭
            # 这样可以避免与update_duration的竞态条件

            # 提取通话信息
            duration_str = call_info.get("duration_str", "00:00")
            duration = call_info.get("duration", 0)
            end_source = call_info.get("end_source", "unknown")  # "local" 或 "remote"
            other_party = call_info.get("other_party", "未知")
            target_type = call_info.get("target_type", "user")
            call_type = call_info.get("call_type", "direct")
            participants = call_info.get("participants", [])
            was_connected = call_info.get("was_connected", False)
            add_to_conversation = call_info.get("add_to_conversation", True)  # 是否添加到会话

            logger.info(f"[CALL_ENDED] Call info: duration={duration_str}, end_source={end_source}, other_party={other_party}, was_connected={was_connected}, call_type={call_type}, participants={participants}, add_to_conversation={add_to_conversation}")

            # 构建通话结束消息
            if call_type == "group":
                # 群聊通话
                if end_source == "local":
                    # 自己退出群聊通话
                    end_msg = f"📞 你退出了群语音通话"
                else:
                    # 通话真正结束（最后一人退出）
                    if was_connected:
                        participants_str = "、".join(participants) if participants else "无"
                        end_msg = f"📞 群语音通话已结束\n通话时长：{duration_str}\n参与者：{participants_str}"
                    else:
                        end_msg = f"📞 群语音通话已结束（未接通）"
            else:
                # 私人通话
                if end_source == "local":
                    # 本地用户挂断
                    if was_connected:
                        end_msg = f"📞 通话已结束。你挂断了通话，通话时长：{duration_str}"
                    else:
                        end_msg = f"📞 通话已结束。你取消了呼叫"
                else:
                    # 对方挂断
                    if was_connected:
                        end_msg = f"📞 通话已结束。{other_party} 挂断了通话，通话时长：{duration_str}"
                    else:
                        end_msg = f"📞 通话已结束。{other_party} 拒绝了通话"

            logger.info(f"[CALL_ENDED] End message: {end_msg}")

            # 根据通话类型确定会话ID
            try:
                # 只有标记为add_to_conversation时才添加到会话
                if add_to_conversation:
                    if target_type == "room":
                        # 群聊语音通话
                        conversation_id = call_info.get("target_id", other_party)
                        conv_type = "room"
                        logger.info(f"[CALL_ENDED] Room call, conversation_id={conversation_id}")
                    else:
                        # 私人语音通话
                        if not self.current_user:
                            logger.warning("[CALL_ENDED] No current_user, using other_party as conversation_id")
                            conversation_id = other_party
                        else:
                            # 创建会话ID（按字母顺序排序）
                            users = sorted([self.current_user, other_party])
                            conversation_id = f"{users[0]}|{users[1]}"
                            logger.info(f"[CALL_ENDED] Private call, conversation_id={conversation_id}")
                        conv_type = "user"

                    # 添加消息到会话（使用after确保在主线程执行）
                    logger.info(f"[CALL_ENDED] Adding message to conversation: {conversation_id}")
                    try:
                        # 检查窗口是否仍然存在
                        if self.winfo_exists():
                            self._add_message_to_conversation(
                                conversation_id,
                                conv_type,
                                "系统",
                                end_msg,
                                other_id=other_party if conv_type == "user" else None,
                                timestamp=int(time.time())
                            )
                            logger.info("[CALL_ENDED] Message added to conversation successfully")
                        else:
                            logger.warning("[CALL_ENDED] Main window no longer exists, skipping conversation add")
                    except tk.TclError as e:
                        logger.warning(f"[CALL_ENDED] TclError when adding to conversation (window may be closing): {e}")
                    except Exception as e:
                        logger.error(f"[CALL_ENDED] Unexpected error adding to conversation: {e}", exc_info=True)
                else:
                    logger.info("[CALL_ENDED] Skipping adding to conversation (add_to_conversation=False)")

            except Exception as e:
                logger.error(f"[CALL_ENDED] Failed to add message to conversation: {e}", exc_info=True)
                # 即使会话消息添加失败，也要添加到系统日志
                pass

            # 总是添加到系统日志
            try:
                logger.info("[CALL_ENDED] Adding to system log")
                # 检查窗口是否仍然存在
                if self.winfo_exists():
                    self._append_log(end_msg)
                    logger.info("[CALL_ENDED] Added to system log successfully")
                else:
                    logger.warning("[CALL_ENDED] Main window no longer exists, skipping system log add")
            except tk.TclError as e:
                logger.warning(f"[CALL_ENDED] TclError when adding to system log (window may be closing): {e}")
            except Exception as e:
                logger.error(f"[CALL_ENDED] Failed to add to system log: {e}", exc_info=True)

            logger.info("[CALL_ENDED] Call ended handling completed successfully")

        except Exception as e:
            logger.error(f"[CALL_ENDED] Unexpected error in _handle_call_ended: {e}", exc_info=True)
            # 确保即使出错也不会导致程序崩溃
            try:
                self._append_log(f"通话结束处理出错: {e}")
            except:
                pass

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
                f"👤 {from_user} 想添加你为好友\n\n{('💬 消息：' + message) if message else ''}\n\n是否同意？"
            )

            if result:
                # 接受请求
                self.runtime.submit(
                    self.runtime.accept_friend_request(request_id),
                    ("friend_accept", str(request_id))
                )
            else:
                # 拒绝请求
                self.runtime.submit(
                    self.runtime.reject_friend_request(request_id),
                    ("friend_reject", str(request_id))
                )

        elif event_type == "request_accepted":
            # 好友请求被接受
            user_id = event.get("user_id")
            self._append_log(f"✅ {user_id} 接受了你的好友请求")
            # 刷新好友列表和在线状态
            self._refresh_friends_and_presence()

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
            # 刷新好友列表和在线状态
            self._refresh_friends_and_presence()

    def _show_incoming_call_dialog(self, call_data: Dict[str, Any]) -> None:
        """Show incoming call dialog."""
        call_id = call_data.get("call_id")
        from_user = call_data.get("from_user", "未知用户")
        call_type = call_data.get("call_type", "direct")

        dialog = tk.Toplevel(self)
        dialog.title("来电")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        dialog.configure(bg=ModernStyle.COLORS["dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # 来电提示
        call_type_text = "群聊语音" if call_type == "group" else "语音通话"
        tk.Label(
            dialog,
            text=f"📞 来自 {from_user} 的{call_type_text}",
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        ).pack(pady=20)

        # 按钮区域
        button_frame = tk.Frame(dialog, bg=ModernStyle.COLORS["dark"])
        button_frame.pack(fill=tk.X, padx=30, pady=10)

        def on_answer():
            dialog.destroy()
            self.runtime.submit(
                self.runtime.answer_voice_call(call_id),
                ("voice_answer", call_id)
            )
            self._show_voice_call_controls()

        def on_reject():
            dialog.destroy()
            self.runtime.submit(
                self.runtime.reject_voice_call(call_id),
                ("voice_reject", call_id)
            )

        tk.Button(
            button_frame,
            text="✅ 接听",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["success"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["success_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=on_answer
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        tk.Button(
            button_frame,
            text="❌ 拒绝",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["danger"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["danger_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=on_reject
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

    def _show_voice_call_controls(self) -> None:
        """Show voice call control window."""
        if hasattr(self, 'voice_control_window') and self.voice_control_window and self.voice_control_window.winfo_exists():
            self.voice_control_window.lift()
            return

        call_info = self.runtime.get_current_voice_call()
        if not call_info:
            return

        dialog = tk.Toplevel(self)
        dialog.title("语音通话中")
        dialog.geometry("350x480")  # 增加高度以显示所有内容
        dialog.resizable(False, False)
        dialog.configure(bg=ModernStyle.COLORS["dark"])
        dialog.transient(self)

        self.voice_control_window = dialog

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (175)
        y = (dialog.winfo_screenheight() // 2) - (240)
        dialog.geometry(f"350x480+{x}+{y}")

        # 获取通话信息
        is_initiator = call_info.get("is_initiator", True)
        if is_initiator:
            # 呼叫方，显示目标
            other_name = call_info.get("target_id", "未知")
        else:
            # 接听方，显示呼叫者
            other_name = call_info.get("from_user", "未知")

        status = call_info.get("status", "unknown")
        call_type = call_info.get("call_type", "direct")

        status_text = {
            "calling": "呼叫中...",
            "ringing": "响铃中...",
            "connected": "通话中",
            "incoming": "来电"
        }.get(status, status)

        # 通话类型标识
        call_type_emoji = "👥" if call_type == "group" else "📞"
        call_type_text = "群聊语音" if call_type == "group" else "语音通话"

        # 标题区域
        title_frame = tk.Frame(dialog, bg=ModernStyle.COLORS["dark"])
        title_frame.pack(pady=(20, 10))

        tk.Label(
            title_frame,
            text=call_type_emoji,
            font=("Arial", 40),
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["primary"]
        ).pack()

        tk.Label(
            title_frame,
            text=call_type_text,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["gray"]
        ).pack()

        # 对方名称
        tk.Label(
            dialog,
            text=other_name,
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        ).pack(pady=(5, 5))

        # 状态和时长
        status_frame = tk.Frame(dialog, bg=ModernStyle.COLORS["dark"])
        status_frame.pack(pady=10)

        status_label = tk.Label(
            status_frame,
            text=status_text,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["success"] if status == "connected" else ModernStyle.COLORS["warning"]
        )
        status_label.pack()

        # 通话时长标签
        duration_label = tk.Label(
            status_frame,
            text="00:00",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        )
        duration_label.pack(pady=(5, 0))

        # 参与者列表（仅群聊时显示）
        participants_frame = tk.Frame(dialog, bg=ModernStyle.COLORS["dark"])

        # 参与者标题
        participants_title = tk.Label(
            participants_frame,
            text="",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["gray"]
        )
        participants_title.pack(pady=(0, 3))

        # 参与者列表容器
        participants_list = tk.Label(
            participants_frame,
            text="",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["primary"],
            justify=tk.LEFT,
            wraplength=250
        )
        participants_list.pack()

        # 存储after回调ID和关闭标记，用于窗口关闭时取消
        duration_update_job = None
        is_closing = False

        # 更新通话时长的函数
        def update_duration():
            nonlocal duration_update_job, is_closing

            # 如果正在关闭，停止更新
            if is_closing:
                return

            try:
                if not dialog.winfo_exists():
                    return

                call_info = self.runtime.get_current_voice_call()
                if not call_info:
                    # 通话已结束，关闭窗口
                    logger.info("[UPDATE_DURATION] Call ended, closing window")
                    is_closing = True
                    self.voice_control_window = None
                    try:
                        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
                        dialog.destroy()
                    except:
                        pass
                    return

                # 计算时长
                connect_time = call_info.get("connect_time")
                if connect_time:
                    # 已接通，显示通话时长
                    elapsed = int(time.time() - connect_time)
                    minutes = elapsed // 60
                    seconds = elapsed % 60
                    duration_label.config(text=f"{minutes:02d}:{seconds:02d}")
                else:
                    # 未接通，显示等待时长
                    start_time = call_info.get("start_time", time.time())
                    elapsed = int(time.time() - start_time)
                    if elapsed > 60:
                        duration_label.config(text=f"等待中 {elapsed}秒", fg=ModernStyle.COLORS["warning"])
                    else:
                        duration_label.config(text="等待接听...")

                # 更新状态
                current_status = call_info.get("status", "unknown")
                new_status_text = {
                    "calling": "呼叫中...",
                    "ringing": "响铃中...",
                    "connected": "通话中",
                    "incoming": "来电"
                }.get(current_status, current_status)
                status_label.config(text=new_status_text)

                # 更新参与者列表（仅群聊时显示）
                call_type_value = call_info.get("call_type", "direct")
                participants = call_info.get("participants", [])
                if call_type_value == "group" and participants:
                    # 显示参与者列表
                    participants_title.config(text=f"参与者 ({len(participants)})")
                    participants_text = "\n".join([f"• {p}" for p in participants])
                    participants_list.config(text=participants_text)
                    # 确保frame可见
                    if not participants_frame.winfo_ismapped():
                        participants_frame.pack(pady=(10, 0))
                else:
                    # 隐藏参与者列表
                    if participants_frame.winfo_ismapped():
                        participants_frame.pack_forget()

                # 每秒更新一次
                duration_update_job = dialog.after(1000, update_duration)
            except tk.TclError:
                # 窗口已经被销毁，停止更新
                return
            except Exception as e:
                # 其他错误，记录日志但不崩溃
                logger.debug(f"Error in update_duration: {e}")
                return

        # 启动时长更新
        update_duration()

        # 挂断按钮
        def on_hang_up():
            nonlocal duration_update_job, is_closing

            # 设置关闭标记，停止update_duration
            is_closing = True

            # 取消定时更新回调
            if duration_update_job:
                try:
                    dialog.after_cancel(duration_update_job)
                except (tk.TclError, RuntimeError):
                    pass
                duration_update_job = None

            # 立即清除窗口引用，防止 _handle_call_ended 重复关闭
            self.voice_control_window = None

            # 解除WM_DELETE_WINDOW绑定，避免递归调用
            try:
                dialog.protocol("WM_DELETE_WINDOW", lambda: None)
            except (tk.TclError, RuntimeError):
                pass

            # 发起结束通话的异步操作
            self.runtime.submit(
                self.runtime.end_voice_call(),
                ("voice_end", None)
            )

            # 立即关闭窗口（不延迟）
            try:
                if dialog.winfo_exists():
                    dialog.destroy()
            except (tk.TclError, RuntimeError):
                pass

        tk.Button(
            dialog,
            text="📵 挂断",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["danger"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["danger_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=40,
            pady=12,
            cursor="hand2",
            command=on_hang_up
        ).pack(pady=(15, 20))

        # 窗口关闭时挂断通话
        def on_close():
            on_hang_up()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

    def _refresh_friends_and_presence(self) -> None:
        """刷新好友列表和在线状态"""
        self.runtime.submit(self.runtime.refresh_friends(), ("friend_refresh", None))
        self.runtime.submit(self.runtime.refresh_presence(), ("presence_refresh", None))

    def _cleanup_friend_data(self, friend_id: str) -> None:
        """清理删除好友的相关数据"""
        if not self.current_user:
            return

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
                except Exception:
                    pass

            # 删除会话数据
            del self.conversations[conversation_id]
            if tree_id and tree_id in self.tree_to_conversation:
                del self.tree_to_conversation[tree_id]

        # 如果当前正在查看这个会话，切换到系统日志
        if self.current_conversation_id == conversation_id:
            self._set_current_conversation(self.system_conv_id)

        self._append_log(f"已清理与 {friend_id} 的聊天记录")

    def _on_close(self) -> None:
        if hasattr(self, "_queue_job"):
            try:
                self.after_cancel(self._queue_job)
            except tk.TclError:
                pass
        self.runtime.shutdown()
        self.destroy()

    def _show_context_menu(self, event: Any) -> None:
        """显示右键菜单"""
        try:
            # 获取点击位置的行号
            index = self.chat_display.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])

            # 获取该行的内容
            line_content = self.chat_display.get(f"{line_num}.0", f"{line_num}.end")

            # 解析消息信息（格式：[时间] [发送者] 消息内容）
            if line_content.strip() and line_content.startswith("["):
                # 存储当前选中的行号，用于后续引用
                self.selected_message_line = line_num
                self.chat_context_menu.post(event.x_root, event.y_root)
        except Exception as e:
            print(f"[DEBUG] 显示右键菜单失败: {e}")

    def _quote_selected_message(self) -> None:
        """引用选中的消息"""
        if not hasattr(self, 'selected_message_line'):
            return

        try:
            # 获取选中行的内容
            line_num = self.selected_message_line
            line_content = self.chat_display.get(f"{line_num}.0", f"{line_num}.end")

            # 解析消息格式：[时间] [发送者] 消息内容
            if not line_content.strip() or not line_content.startswith("["):
                return

            # 提取时间、发送者和消息内容
            parts = line_content.split("]", 2)
            if len(parts) < 3:
                return

            time_str = parts[0][1:].strip()  # 去掉 [
            sender = parts[1][1:].strip()     # 去掉 [
            text = parts[2].strip()

            # 查找对应的消息ID（从message_registry中查找）
            message_id = None
            for msg_id, msg_info in self.message_registry.items():
                if (msg_info.get("sender") == sender and
                    msg_info.get("text") == text and
                    msg_info.get("time_str") == time_str):
                    message_id = msg_id
                    break

            # 设置引用消息
            self.reply_to_message = {
                "message_id": message_id or f"msg_{line_num}",
                "sender": sender,
                "text": text[:100],  # 只保留前100个字符
                "time_str": time_str
            }

            # 显示引用预览
            preview_text = f"回复 {sender}：{text[:50]}{'...' if len(text) > 50 else ''}"
            self.reply_preview_label.config(text=preview_text)
            self.reply_preview_frame.pack(fill=tk.X, padx=10, pady=(0, 5), before=self.message_input)

            # 焦点移到输入框
            self.message_input.focus_set()

        except Exception as e:
            print(f"[DEBUG] 引用消息失败: {e}")

    def _cancel_reply(self) -> None:
        """取消引用回复"""
        self.reply_to_message = None
        self.reply_preview_frame.pack_forget()
        self.message_input.focus_set()


class RoomManagerWindow(tk.Toplevel):
    """现代化房间相关操作弹窗。"""

    def __init__(self, app: TkChatApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("🏠 房间管理")
        self.geometry("1000x700")  # 增大窗口尺寸
        self.resizable(True, True)  # 允许调节大小
        self.configure(bg=ModernStyle.COLORS["darkest"])
        self.status_var = tk.StringVar(value="请选择操作")
        self.current_room: Optional[str] = None

        # 居中窗口
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.overview_frame = tk.Frame(self.notebook, bg=ModernStyle.COLORS["dark"])
        self.ops_frame = tk.Frame(self.notebook, bg=ModernStyle.COLORS["dark"])
        self.notebook.add(self.overview_frame, text="房间概览")
        self.notebook.add(self.ops_frame, text="房间操作")

        self._build_overview_tab()
        self._build_ops_tab()
        self._populate_overview()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_overview_tab(self) -> None:
        columns = ("room", "encrypted", "members")
        self.overview_tree = ttk.Treeview(
            self.overview_frame, columns=columns, show="headings", height=18  # 增大高度
        )
        self.overview_tree.heading("room", text="房间 ID")
        self.overview_tree.heading("encrypted", text="加密")
        self.overview_tree.heading("members", text="成员数")
        self.overview_tree.column("room", width=300)  # 增大列宽
        self.overview_tree.column("encrypted", width=100, anchor=tk.CENTER)
        self.overview_tree.column("members", width=100, anchor=tk.CENTER)
        self.overview_tree.pack(fill=tk.BOTH, expand=True)
        self.overview_tree.bind("<Double-1>", self._open_conversation)
        self.overview_tree.bind("<<TreeviewSelect>>", self._on_select)

        btn_row = ttk.Frame(self.overview_frame)
        btn_row.pack(fill=tk.X, pady=6)
        ttk.Button(btn_row, text="刷新概览", command=self._populate_overview).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="刷新详情", command=self._refresh_members).pack(side=tk.LEFT, padx=4)

        detail = ttk.LabelFrame(self.overview_frame, text="房间详情", padding=8)
        detail.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.detail_room_var = tk.StringVar(value="-")
        self.detail_owner_var = tk.StringVar(value="-")
        self.detail_created_var = tk.StringVar(value="-")
        self.detail_encrypted_var = tk.StringVar(value="-")

        info_grid = ttk.Frame(detail)
        info_grid.pack(fill=tk.X)
        ttk.Label(info_grid, text="房间：").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Label(info_grid, textvariable=self.detail_room_var).grid(row=0, column=1, sticky=tk.W)
        ttk.Label(info_grid, text="房主：").grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(info_grid, textvariable=self.detail_owner_var).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(info_grid, text="创建时间：").grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Label(info_grid, textvariable=self.detail_created_var).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(info_grid, text="加密：").grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Label(info_grid, textvariable=self.detail_encrypted_var).grid(row=3, column=1, sticky=tk.W)

        ttk.Label(detail, text="成员列表").pack(anchor=tk.W, pady=(8, 2))

        # 成员列表框架
        members_frame = ttk.Frame(detail)
        members_frame.pack(fill=tk.BOTH, expand=True)

        self.detail_members = tk.Listbox(members_frame, height=10)  # 增大高度
        self.detail_members.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 成员管理按钮（右侧）
        member_buttons_frame = ttk.Frame(members_frame)
        member_buttons_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0))

        self.kick_member_btn = ttk.Button(
            member_buttons_frame,
            text="踢出成员",
            command=self._kick_member
        )
        # 初始隐藏踢人按钮，只有群主才显示

        # 房间管理按钮（底部）
        room_actions = ttk.Frame(detail)
        room_actions.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(
            room_actions,
            text="退出房间",
            command=self._leave_current_room
        ).pack(side=tk.LEFT, padx=2)

        self.delete_room_btn = ttk.Button(
            room_actions,
            text="解散房间（群主）",
            command=self._delete_current_room
        )
        # 初始隐藏解散按钮，只有群主才显示

    def _build_ops_tab(self) -> None:
        form = ttk.Frame(self.ops_frame, padding=5)
        form.pack(fill=tk.X, pady=4)

        ttk.Label(form, text="房间 ID").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.room_input = tk.StringVar()
        ttk.Entry(form, textvariable=self.room_input, width=32).grid(row=0, column=1, sticky=tk.W)

        self.encrypt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="加密", variable=self.encrypt_var).grid(row=1, column=1, sticky=tk.W, pady=4)

        ttk.Label(form, text="房间密码").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.password_input = tk.StringVar()
        ttk.Entry(form, textvariable=self.password_input, width=32, show="*").grid(row=2, column=1, sticky=tk.W)

        btns = ttk.Frame(self.ops_frame)
        btns.pack(fill=tk.X, pady=8)
        ttk.Button(btns, text="创建房间", command=self._create_room).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(btns, text="加入房间", command=self._join_room).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(btns, text="离开房间", command=self._leave_room).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)

        ttk.Label(self.ops_frame, textvariable=self.status_var, foreground="#1a73e8").pack(anchor=tk.W, pady=6)

    def _populate_overview(self) -> None:
        for item in self.overview_tree.get_children():
            self.overview_tree.delete(item)
        rooms = sorted(self.app.rooms_cache)
        for room_id in rooms:
            meta = self.app.room_metadata.get(room_id, {})
            encrypted = "是" if meta.get("encrypted") else "否"
            members = meta.get("members")
            member_text = str(len(members)) if isinstance(members, list) else "-"
            self.overview_tree.insert("", "end", iid=room_id, values=(room_id, encrypted, member_text))
        if self.current_room and self.overview_tree.exists(self.current_room):
            self.overview_tree.selection_set(self.current_room)

    def _refresh_members(self) -> None:
        targets = self.overview_tree.selection()
        if not targets:
            targets = self.overview_tree.get_children()
        if targets:
            self.status_var.set("正在刷新房间详情...")
        for room_id in targets:
            self._request_details(room_id)

    def _open_conversation(self, _event: Any = None) -> None:
        selection = self.overview_tree.selection()
        if not selection:
            return
        room_id = selection[0]
        self.app._ensure_conversation(room_id, "room", f"群聊：{room_id}")
        self.app._set_current_conversation(room_id)
        self.status_var.set(f"已切换到房间 {room_id}")
        self._request_details(room_id)

    def _on_select(self, _event: Any = None) -> None:
        selection = self.overview_tree.selection()
        if not selection:
            return
        room_id = selection[0]
        self.current_room = room_id
        self._request_details(room_id)

    def _request_details(self, room_id: str) -> None:
        if not room_id:
            return
        self.current_room = room_id
        self.status_var.set(f"正在获取房间 {room_id} 信息...")
        self.app.request_room_info(
            room_id,
            lambda payload, room=room_id: self._on_info(room, payload),
        )

    def _create_room(self) -> None:
        room_id = self.room_input.get().strip()
        if not room_id:
            messagebox.showwarning("提示", "请输入房间 ID")
            return
        password = self.password_input.get().strip() or None
        self.status_var.set("正在创建房间...")
        self.app.submit_room_create(room_id, self.encrypt_var.get(), password)
        self.password_input.set("")

    def _join_room(self) -> None:
        room_id = self.room_input.get().strip()
        if not room_id:
            messagebox.showwarning("提示", "请输入房间 ID")
            return
        password = self.password_input.get().strip() or None
        self.status_var.set("正在加入房间...")
        self.app.submit_room_join(room_id, password)
        self.password_input.set("")

    def _leave_room(self) -> None:
        room_id = self.room_input.get().strip()
        if not room_id:
            messagebox.showwarning("提示", "请输入房间 ID")
            return
        self.status_var.set("正在离开房间...")
        self.app.submit_room_leave(room_id)
        self.password_input.set("")

    def handle_action(self, action: str, payload: Dict[str, Any], room_id: Optional[str]) -> None:
        if not self.winfo_exists():
            return
        status = payload.get("status")
        ok = str(status) == str(int(StatusCode.SUCCESS))
        labels = {
            "room_create": "创建房间",
            "room_join": "加入房间",
            "room_leave": "离开房间",
            "rooms_refresh": "刷新房间",
        }
        prefix = labels.get(action, "房间操作")
        suffix = "成功" if ok else f"失败：{payload.get('error_message', '未知错误')}"
        info = f"{prefix} {room_id or ''} {suffix}"
        self.status_var.set(info)
        if ok:
            self._populate_overview()
            if room_id:
                self._request_details(room_id)

    def handle_members(self, room_id: Optional[str], members: list[str]) -> None:
        if not self.winfo_exists():
            return
        if not room_id or not self.overview_tree.exists(room_id):
            return
        values = list(self.overview_tree.item(room_id, "values"))
        if len(values) < 3:
            values = [room_id, "否", "-"]
        values[2] = str(len(members))
        self.overview_tree.item(room_id, values=values)
        if self.current_room == room_id:
            self.detail_members.delete(0, tk.END)
            for member in members:
                self.detail_members.insert(tk.END, member)
        self.status_var.set(f"{room_id} 成员数：{len(members)}")

    def handle_room_info(self, room_id: Optional[str], payload: Dict[str, Any]) -> None:
        self._on_info(room_id, payload)

    def _on_info(self, room_id: Optional[str], payload: Dict[str, Any]) -> None:
        if not self.winfo_exists() or not room_id:
            return
        if payload.get("status") and int(payload["status"]) != int(StatusCode.SUCCESS):
            self.status_var.set(payload.get("error_message", "获取房间信息失败"))
            return
        encrypted = "是" if payload.get("encrypted") else "否"
        members = payload.get("members") or []
        if self.overview_tree.exists(room_id):
            self.overview_tree.item(room_id, values=(room_id, encrypted, str(len(members))))
        if self.current_room == room_id or not self.current_room:
            self.current_room = room_id
            self._set_details(payload)

    def _set_details(self, info: Dict[str, Any]) -> None:
        self.detail_room_var.set(info.get("room_id") or "-")
        owner = info.get("owner") or "-"
        self.detail_owner_var.set(owner)
        created_at = info.get("created_at")
        if created_at:
            self.detail_created_var.set(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at)))
        else:
            self.detail_created_var.set("-")
        self.detail_encrypted_var.set("是" if info.get("encrypted") else "否")
        self.detail_members.delete(0, tk.END)
        for member in info.get("members") or []:
            self.detail_members.insert(tk.END, member)
        self.status_var.set(f"已获取房间 {info.get('room_id') or ''} 信息")

        # 只有群主才显示踢人和解散按钮
        is_owner = (owner != "-" and owner == self.app.current_user)
        if is_owner:
            self.kick_member_btn.pack(fill=tk.X, pady=2)
            self.delete_room_btn.pack(side=tk.LEFT, padx=2)
        else:
            self.kick_member_btn.pack_forget()
            self.delete_room_btn.pack_forget()

    def _kick_member(self) -> None:
        """踢出选中的成员"""
        if not self.current_room:
            messagebox.showwarning("提示", "请先选择房间")
            return

        selection = self.detail_members.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要踢出的成员")
            return

        member_id = self.detail_members.get(selection[0])
        if not member_id:
            return

        # 确认对话框
        if not messagebox.askyesno("确认", f"确定要踢出成员 {member_id} 吗？"):
            return

        # 调用API踢出成员
        self.status_var.set(f"正在踢出成员 {member_id}...")
        self.app.runtime.submit(
            self.app.runtime.kick_room_member(self.current_room, member_id),
            ("room_kick", self.current_room)
        )

    def _leave_current_room(self) -> None:
        """退出当前房间"""
        if not self.current_room:
            messagebox.showwarning("提示", "请先选择房间")
            return

        # 确认对话框
        if not messagebox.askyesno("确认", f"确定要退出房间 {self.current_room} 吗？"):
            return

        self.status_var.set(f"正在退出房间 {self.current_room}...")
        self.app.submit_room_leave(self.current_room)

    def _delete_current_room(self) -> None:
        """解散当前房间（仅群主）"""
        if not self.current_room:
            messagebox.showwarning("提示", "请先选择房间")
            return

        # 确认对话框
        if not messagebox.askyesno(
            "确认",
            f"确定要解散房间 {self.current_room} 吗？\n此操作不可撤销！",
            icon="warning"
        ):
            return

        self.status_var.set(f"正在解散房间 {self.current_room}...")
        self.app.runtime.submit(
            self.app.runtime.delete_room(self.current_room),
            ("room_delete", self.current_room)
        )

    def _on_close(self) -> None:
        self.app.room_window = None
        self.destroy()


class FriendManagerWindow(tk.Toplevel):
    """好友管理窗口"""

    def __init__(self, app: TkChatApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("👥 好友管理")
        self.geometry("900x700")
        self.resizable(True, True)
        self.configure(bg=ModernStyle.COLORS["darkest"])
        self.status_var = tk.StringVar(value="请选择操作")

        # 居中窗口
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        # 创建标签页
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 好友列表标签页
        self.friends_frame = tk.Frame(self.notebook, bg=ModernStyle.COLORS["dark"])
        self.notebook.add(self.friends_frame, text="📋 好友列表")

        # 添加好友标签页
        self.add_frame = tk.Frame(self.notebook, bg=ModernStyle.COLORS["dark"])
        self.notebook.add(self.add_frame, text="➕ 添加好友")

        # 好友请求标签页
        self.requests_frame = tk.Frame(self.notebook, bg=ModernStyle.COLORS["dark"])
        self.notebook.add(self.requests_frame, text="📬 好友请求")

        self._build_friends_tab()
        self._build_add_tab()
        self._build_requests_tab()

        # 底部状态栏
        status_frame = tk.Frame(self, bg=ModernStyle.COLORS["dark"], height=30)
        status_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        status_frame.pack_propagate(False)

        tk.Label(
            status_frame,
            textvariable=self.status_var,
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["gray_light"]
        ).pack(side=tk.LEFT, padx=10)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 初始加载好友数据
        self._refresh_all_data()

    def _build_friends_tab(self) -> None:
        """构建好友列表标签页"""
        # 标题
        tk.Label(
            self.friends_frame,
            text="我的好友",
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        ).pack(pady=20)

        # 好友列表框架
        list_frame = tk.Frame(self.friends_frame, bg=ModernStyle.COLORS["darker"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        # 使用 Treeview 显示好友列表
        columns = ("user_id", "status")
        self.friends_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            height=20
        )
        self.friends_tree.heading("user_id", text="用户ID")
        self.friends_tree.heading("status", text="状态")
        self.friends_tree.column("user_id", width=400)
        self.friends_tree.column("status", width=200, anchor=tk.CENTER)

        # 滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.friends_tree.yview)
        self.friends_tree.configure(yscrollcommand=scrollbar.set)

        self.friends_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 操作按钮
        btn_frame = tk.Frame(self.friends_frame, bg=ModernStyle.COLORS["dark"])
        btn_frame.pack(fill=tk.X, padx=20, pady=10)

        tk.Button(
            btn_frame,
            text="🔄 刷新列表",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._refresh_friends_list
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame,
            text="💬 开始聊天",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["success"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["success_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._start_chat_with_friend
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            btn_frame,
            text="🗑️ 删除好友",
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["danger"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["danger_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._delete_friend
        ).pack(side=tk.LEFT, padx=5)

    def _build_add_tab(self) -> None:
        """构建添加好友标签页"""
        # 标题
        tk.Label(
            self.add_frame,
            text="添加新好友",
            font=ModernStyle.FONTS["heading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        ).pack(pady=30)

        # 表单容器
        form_container = tk.Frame(self.add_frame, bg=ModernStyle.COLORS["card_bg"])
        form_container.pack(fill=tk.BOTH, expand=True, padx=100, pady=20)

        form_frame = tk.Frame(form_container, bg=ModernStyle.COLORS["card_bg"])
        form_frame.pack(padx=40, pady=40)

        # 用户ID输入
        tk.Label(
            form_frame,
            text="👤 用户ID:",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"]
        ).pack(anchor=tk.W, pady=(0, 5))

        self.target_id_var = tk.StringVar()
        tk.Entry(
            form_frame,
            textvariable=self.target_id_var,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=2,
            width=40
        ).pack(fill=tk.X, pady=(0, 20))

        # 附加消息输入
        tk.Label(
            form_frame,
            text="💬 附加消息 (可选):",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["card_bg"],
            fg=ModernStyle.COLORS["lighter"]
        ).pack(anchor=tk.W, pady=(10, 5))

        self.message_var = tk.StringVar()
        tk.Entry(
            form_frame,
            textvariable=self.message_var,
            font=ModernStyle.FONTS["normal"],
            bg=ModernStyle.COLORS["darker"],
            fg=ModernStyle.COLORS["light"],
            relief="flat",
            bd=2,
            width=40
        ).pack(fill=tk.X, pady=(0, 30))

        # 发送按钮
        tk.Button(
            form_frame,
            text="📤 发送好友请求",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=40,
            pady=12,
            cursor="hand2",
            command=self._send_friend_request
        ).pack(pady=20)

    def _build_requests_tab(self) -> None:
        """构建好友请求标签页"""
        # 分为两部分：收到的请求和发送的请求

        # 收到的请求
        received_label = tk.Label(
            self.requests_frame,
            text="📥 收到的好友请求",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        )
        received_label.pack(pady=(20, 10))

        received_frame = tk.Frame(self.requests_frame, bg=ModernStyle.COLORS["darker"])
        received_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        columns = ("from_user", "message", "time")
        self.received_tree = ttk.Treeview(
            received_frame,
            columns=columns,
            show="headings",
            height=8
        )
        self.received_tree.heading("from_user", text="用户ID")
        self.received_tree.heading("message", text="消息")
        self.received_tree.heading("time", text="时间")
        self.received_tree.column("from_user", width=200)
        self.received_tree.column("message", width=350)
        self.received_tree.column("time", width=150)

        received_scroll = ttk.Scrollbar(received_frame, orient="vertical", command=self.received_tree.yview)
        self.received_tree.configure(yscrollcommand=received_scroll.set)

        self.received_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        received_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 收到请求的操作按钮
        received_btn_frame = tk.Frame(self.requests_frame, bg=ModernStyle.COLORS["dark"])
        received_btn_frame.pack(fill=tk.X, padx=20, pady=5)

        tk.Button(
            received_btn_frame,
            text="✅ 接受请求",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["success"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["success_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=6,
            cursor="hand2",
            command=self._accept_request
        ).pack(side=tk.LEFT, padx=5)

        tk.Button(
            received_btn_frame,
            text="❌ 拒绝请求",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["danger"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["danger_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=6,
            cursor="hand2",
            command=self._reject_request
        ).pack(side=tk.LEFT, padx=5)

        # 分隔线
        ttk.Separator(self.requests_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=20, pady=15)

        # 发送的请求
        sent_label = tk.Label(
            self.requests_frame,
            text="📤 已发送的好友请求",
            font=ModernStyle.FONTS["subheading"],
            bg=ModernStyle.COLORS["dark"],
            fg=ModernStyle.COLORS["light"]
        )
        sent_label.pack(pady=(10, 10))

        sent_frame = tk.Frame(self.requests_frame, bg=ModernStyle.COLORS["darker"])
        sent_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        columns = ("to_user", "message", "status", "time")
        self.sent_tree = ttk.Treeview(
            sent_frame,
            columns=columns,
            show="headings",
            height=8
        )
        self.sent_tree.heading("to_user", text="用户ID")
        self.sent_tree.heading("message", text="消息")
        self.sent_tree.heading("status", text="状态")
        self.sent_tree.heading("time", text="时间")
        self.sent_tree.column("to_user", width=180)
        self.sent_tree.column("message", width=250)
        self.sent_tree.column("status", width=120, anchor=tk.CENTER)
        self.sent_tree.column("time", width=150)

        sent_scroll = ttk.Scrollbar(sent_frame, orient="vertical", command=self.sent_tree.yview)
        self.sent_tree.configure(yscrollcommand=sent_scroll.set)

        self.sent_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sent_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 刷新按钮
        refresh_btn_frame = tk.Frame(self.requests_frame, bg=ModernStyle.COLORS["dark"])
        refresh_btn_frame.pack(fill=tk.X, padx=20, pady=(0, 10))

        tk.Button(
            refresh_btn_frame,
            text="🔄 刷新请求列表",
            font=ModernStyle.FONTS["small"],
            bg=ModernStyle.COLORS["primary"],
            fg=ModernStyle.COLORS["light"],
            activebackground=ModernStyle.COLORS["primary_dark"],
            activeforeground=ModernStyle.COLORS["light"],
            relief="flat",
            bd=0,
            padx=15,
            pady=6,
            cursor="hand2",
            command=self._refresh_requests
        ).pack(side=tk.LEFT, padx=5)

    def _refresh_all_data(self) -> None:
        """刷新所有好友数据"""
        if not self.winfo_exists():
            return
        self.status_var.set("正在刷新好友数据...")
        self.app.runtime.submit(
            self.app.runtime.refresh_friends(),
            ("friend_refresh", None)
        )
        # 也刷新在线状态
        self.app.runtime.submit(
            self.app.runtime.refresh_presence(),
            ("presence_refresh", None)
        )
        # 延迟更新UI
        if self.winfo_exists():
            self.after(500, self._update_ui)

    def _update_ui(self) -> None:
        """更新UI显示"""
        if not self.winfo_exists():
            return
        self._refresh_friends_list()
        self._refresh_requests()
        self.status_var.set("数据已刷新")

    def _refresh_friends_list(self) -> None:
        """刷新好友列表"""
        if not self.winfo_exists():
            return
        # 清空现有列表
        for item in self.friends_tree.get_children():
            self.friends_tree.delete(item)

        # 获取好友列表
        friends = self.app.runtime.friends.get_friends()

        # 获取在线用户列表
        online_users = set(self.app.presence_list.get(0, tk.END))

        # 插入好友数据
        for friend_id in sorted(friends):
            status = "🟢 在线" if friend_id in online_users else "⚪ 离线"
            self.friends_tree.insert("", "end", values=(friend_id, status))

        self.status_var.set(f"共有 {len(friends)} 位好友")

    def _refresh_requests(self) -> None:
        """刷新好友请求列表"""
        if not self.winfo_exists():
            return
        # 清空收到的请求
        for item in self.received_tree.get_children():
            self.received_tree.delete(item)

        # 清空发送的请求
        for item in self.sent_tree.get_children():
            self.sent_tree.delete(item)

        # 获取请求数据
        pending_requests = self.app.runtime.friends.get_pending_requests()
        sent_requests = self.app.runtime.friends.get_sent_requests()

        # 插入收到的请求
        for req in pending_requests:
            from_user = req.get("from_user", "")
            message = req.get("message", "")
            created_at = req.get("created_at", 0)
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at)) if created_at else ""

            # 存储 request_id 到 item 的 tags 中
            request_id = req.get("id")
            item = self.received_tree.insert("", "end", values=(from_user, message, time_str))
            self.received_tree.item(item, tags=(str(request_id),))

        # 插入发送的请求
        for req in sent_requests:
            to_user = req.get("to_user", "")
            message = req.get("message", "")
            status = req.get("status", "pending")
            created_at = req.get("created_at", 0)
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at)) if created_at else ""

            status_text = {
                "pending": "⏳ 等待中",
                "accepted": "✅ 已接受",
                "rejected": "❌ 已拒绝"
            }.get(status, status)

            self.sent_tree.insert("", "end", values=(to_user, message, status_text, time_str))

    def _send_friend_request(self) -> None:
        """发送好友请求"""
        target_id = self.target_id_var.get().strip()
        message = self.message_var.get().strip()

        if not target_id:
            messagebox.showwarning("提示", "请输入用户ID")
            return

        if target_id == self.app.current_user:
            messagebox.showwarning("提示", "不能添加自己为好友")
            return

        # 检查是否已经是好友
        if self.app.runtime.friends.is_friend(target_id):
            messagebox.showinfo("提示", f"{target_id} 已经是你的好友了")
            return

        self.status_var.set(f"正在向 {target_id} 发送好友请求...")
        self.app.runtime.submit(
            self.app.runtime.send_friend_request(target_id, message),
            ("friend_request_send", target_id)
        )

        # 清空输入框
        self.target_id_var.set("")
        self.message_var.set("")

        # 显示成功提示
        messagebox.showinfo("成功", f"已向 {target_id} 发送好友请求")
        self.status_var.set("好友请求已发送")

        # 刷新请求列表
        self.after(500, self._refresh_requests)

    def _accept_request(self) -> None:
        """接受好友请求"""
        selection = self.received_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要接受的请求")
            return

        item = selection[0]
        tags = self.received_tree.item(item, "tags")
        if not tags:
            messagebox.showerror("错误", "无法获取请求ID")
            return

        request_id = int(tags[0])
        values = self.received_tree.item(item, "values")
        from_user = values[0] if values else "未知用户"

        self.status_var.set(f"正在接受 {from_user} 的好友请求...")
        self.app.runtime.submit(
            self.app.runtime.accept_friend_request(request_id),
            ("friend_accept", str(request_id))
        )

        messagebox.showinfo("成功", f"已接受 {from_user} 的好友请求")

        # 刷新所有数据
        self.after(500, self._refresh_all_data)

    def _reject_request(self) -> None:
        """拒绝好友请求"""
        selection = self.received_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要拒绝的请求")
            return

        item = selection[0]
        tags = self.received_tree.item(item, "tags")
        if not tags:
            messagebox.showerror("错误", "无法获取请求ID")
            return

        request_id = int(tags[0])
        values = self.received_tree.item(item, "values")
        from_user = values[0] if values else "未知用户"

        # 确认对话框
        if not messagebox.askyesno("确认", f"确定要拒绝 {from_user} 的好友请求吗？"):
            return

        self.status_var.set(f"正在拒绝 {from_user} 的好友请求...")
        self.app.runtime.submit(
            self.app.runtime.reject_friend_request(request_id),
            ("friend_reject", str(request_id))
        )

        messagebox.showinfo("成功", f"已拒绝 {from_user} 的好友请求")

        # 刷新请求列表
        self.after(500, self._refresh_requests)

    def _delete_friend(self) -> None:
        """删除好友"""
        selection = self.friends_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的好友")
            return

        item = selection[0]
        values = self.friends_tree.item(item, "values")
        friend_id = values[0] if values else None

        if not friend_id:
            return

        # 确认对话框
        if not messagebox.askyesno(
            "确认删除",
            f"确定要删除好友 {friend_id} 吗？\n\n删除后将清除所有聊天记录，此操作不可撤销！",
            icon="warning"
        ):
            return

        self.status_var.set(f"正在删除好友 {friend_id}...")
        self.app.runtime.submit(
            self.app.runtime.delete_friend(friend_id),
            ("friend_delete", friend_id)
        )

        messagebox.showinfo("成功", f"已删除好友 {friend_id}")

        # 刷新好友列表
        self.after(500, self._refresh_friends_list)

    def _start_chat_with_friend(self) -> None:
        """开始与选中的好友聊天"""
        selection = self.friends_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要聊天的好友")
            return

        item = selection[0]
        values = self.friends_tree.item(item, "values")
        friend_id = values[0] if values else None

        if not friend_id:
            return

        # 调用主窗口的开始私聊方法
        self.app._start_private_chat(friend_id)

        # 切换到主窗口
        self.app.lift()
        self.app.focus_set()

        self.status_var.set(f"已打开与 {friend_id} 的聊天窗口")

    def _on_close(self) -> None:
        """关闭窗口"""
        self.app.friend_window = None
        self.destroy()


def run_tk_app() -> None:
    login = LoginWindow()
    login.mainloop()
    runtime = getattr(login, "runtime", None)
    auth_payload = getattr(login, "auth_payload", None)
    if runtime and auth_payload and auth_payload.get("success"):
        app = TkChatApp(runtime, auth_payload)
        app.mainloop()
    elif runtime:
        runtime.shutdown()
