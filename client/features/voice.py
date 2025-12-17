

"""Voice call management for client."""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except (ImportError, Exception):
    PYAUDIO_AVAILABLE = False

try:
    import opuslib
    OPUS_AVAILABLE = True
except (ImportError, Exception):
    # opuslib might be installed but the native Opus library might not be available
    OPUS_AVAILABLE = False

from shared.protocol import DEFAULT_VERSION
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode

logger = logging.getLogger(__name__)


class VoiceCallError(ProtocolError):
    """Voice call specific error."""
    pass


class AudioHandler:
    """Handles audio I/O using PyAudio with optional Opus codec."""

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_duration: int = 20,  # ms
        use_opus: bool = True,
    ):
        if not PYAUDIO_AVAILABLE:
            raise VoiceCallError(
                StatusCode.INTERNAL_ERROR,
                message="PyAudio not installed. Please install: pip install pyaudio"
            )

        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_duration = frame_duration
        self.frame_size = int(sample_rate * frame_duration / 1000)  # samples per frame
        self.chunk_size = self.frame_size * channels * 2  # bytes (16-bit PCM)

        self.use_opus = use_opus and OPUS_AVAILABLE
        if use_opus and not OPUS_AVAILABLE:
            logger.warning("Opus codec not available, falling back to PCM")
            self.use_opus = False

        self.pyaudio_instance = pyaudio.PyAudio()
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[pyaudio.Stream] = None
        self.encoder = None
        self.decoder = None

        if self.use_opus:
            try:
                # Opus application type: 2048 = VOIP
                self.encoder = opuslib.Encoder(sample_rate, channels, opuslib.APPLICATION_VOIP)
                self.decoder = opuslib.Decoder(sample_rate, channels)
                logger.info(f"Opus codec initialized: {sample_rate}Hz, {channels} channel(s)")
            except Exception as e:
                logger.error(f"Failed to initialize Opus codec: {e}, falling back to PCM")
                self.use_opus = False

    def start_input(self) -> None:
        """Start capturing audio from microphone."""
        if self.input_stream:
            return
        try:
            # Test microphone access first
            logger.info("Requesting microphone access...")
            self.input_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.frame_size,
            )
            logger.info("Audio input stream started (Microphone access granted)")
        except OSError as e:
            # OSError typically means permission denied or device not found
            raise VoiceCallError(
                StatusCode.INTERNAL_ERROR,
                message=f"无法访问麦克风。请检查：\n1. 麦克风设备是否连接\n2. 是否授予了麦克风权限\n3. 麦克风是否被其他程序占用\n\n详细错误: {e}"
            )
        except Exception as e:
            raise VoiceCallError(
                StatusCode.INTERNAL_ERROR,
                message=f"Failed to start audio input: {e}"
            )

    def start_output(self) -> None:
        """Start audio playback to speaker."""
        if self.output_stream:
            return
        try:
            self.output_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=self.frame_size,
            )
            logger.info("Audio output stream started")
        except Exception as e:
            raise VoiceCallError(
                StatusCode.INTERNAL_ERROR,
                message=f"Failed to start audio output: {e}"
            )

    def read_frame(self) -> Optional[bytes]:
        """Read one audio frame from mic and encode if opus is enabled."""
        if not self.input_stream:
            return None
        try:
            # 检查stream是否仍然活跃
            if not self.input_stream or not hasattr(self.input_stream, 'read'):
                return None

            pcm_data = self.input_stream.read(self.frame_size, exception_on_overflow=False)
            if self.use_opus and self.encoder:
                return self.encoder.encode(pcm_data, self.frame_size)
            return pcm_data
        except (OSError, IOError) as e:
            # Stream已关闭或设备不可用
            logger.debug(f"Audio stream closed or unavailable: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to read audio frame: {e}")
            return None

    def write_frame(self, data: bytes) -> None:
        """Decode (if opus) and write audio frame to speaker."""
        if not self.output_stream:
            return
        try:
            if self.use_opus and self.decoder:
                pcm_data = self.decoder.decode(data, self.frame_size)
            else:
                pcm_data = data
            self.output_stream.write(pcm_data)
        except Exception as e:
            logger.error(f"Failed to write audio frame: {e}")

    def stop_input(self) -> None:
        """Stop audio capture."""
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None
            logger.info("Audio input stream stopped")

    def stop_output(self) -> None:
        """Stop audio playback."""
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None
            logger.info("Audio output stream stopped")

    def cleanup(self) -> None:
        """Release all audio resources."""
        self.stop_input()
        self.stop_output()
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None
        logger.info("Audio handler cleaned up")


class VoiceManager:
    """Manages voice calls: initiate, answer, reject, send/receive audio."""

    def __init__(self, network, session, ui_queue: Optional[queue.Queue] = None):
        self.network = network
        self.session = session
        self.ui_queue = ui_queue

        self.current_call: Optional[Dict[str, Any]] = None
        self.audio_handler: Optional[AudioHandler] = None
        self._send_task: Optional[asyncio.Task] = None
        self._receive_loop_running = False
        self._audio_send_queue: queue.Queue[bytes] = queue.Queue()
        self._audio_thread: Optional[threading.Thread] = None

        # Register event handlers
        self.network.register_handler(MsgType.VOICE_CALL_ACK, self._handle_call_ack)
        self.network.register_handler(MsgType.VOICE_EVENT, self._handle_voice_event)
        self.network.register_handler(MsgType.VOICE_DATA, self._handle_voice_data)

    async def initiate_call(self, target_type: str, target_id: str, call_type: str = "direct") -> Dict[str, Any]:
        """
        Initiate a voice call to user or room.

        Args:
            target_type: "user" or "room"
            target_id: user ID or room ID
            call_type: "direct" for 1-on-1, "group" for conference
        """
        # 如果有现有通话，先结束
        if self.current_call:
            logger.warning("[INITIATE CALL] Ending existing call before starting new one...")
            await self.end_call()
            await asyncio.sleep(0.1)

        call_id = uuid.uuid4().hex
        headers = self.session.build_headers()
        logger.info(f"[INITIATE CALL] Starting new call {call_id}, target={target_type}:{target_id}")

        msg = {
            "id": call_id,
            "type": "request",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_CALL.value,
            "headers": headers,
            "payload": {
                "call_type": call_type,
                "target": {
                    "type": target_type,
                    "id": target_id
                },
                "codec": "opus" if OPUS_AVAILABLE else "pcm",
                "sample_rate": 48000,
                "channels": 1
            }
        }

        await self.network.send(msg)
        self.current_call = {
            "call_id": call_id,
            "target_type": target_type,
            "target_id": target_id,
            "call_type": call_type,
            "status": "calling",
            "is_initiator": True,
            "start_time": time.time(),
            "connect_time": None,
            "participants": [self.session.user_id] if self.session.user_id else [],  # 添加参与者列表
        }
        self._notify_ui("status", "正在呼叫...")
        logger.info(f"[INITIATE CALL] Call {call_id} initiated successfully")
        return {"status": 200, "call_id": call_id}

    async def answer_call(self, call_id: str) -> Dict[str, Any]:
        """Answer an incoming call."""
        if not self.current_call or self.current_call.get("call_id") != call_id:
            raise VoiceCallError(StatusCode.NOT_FOUND, message="Call not found")

        logger.info(f"[ANSWER CALL] Answering call {call_id}...")

        msg = {
            "id": uuid.uuid4().hex,
            "type": "request",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_ANSWER.value,
            "headers": self.session.build_headers(),
            "payload": {
                "call_id": call_id,
                "codec": "opus" if OPUS_AVAILABLE else "pcm"
            }
        }

        await self.network.send(msg)
        self.current_call["status"] = "connected"
        logger.info(f"[ANSWER CALL] Starting audio streams for call {call_id}...")
        await self._start_audio_streams_async()  # 使用异步版本
        self._notify_ui("status", "通话已接通")
        logger.info(f"[ANSWER CALL] Call {call_id} answered successfully")
        return {"status": 200, "call_id": call_id}

    async def reject_call(self, call_id: str) -> Dict[str, Any]:
        """Reject an incoming call."""
        msg = {
            "id": uuid.uuid4().hex,
            "type": "request",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_REJECT.value,
            "headers": self.session.build_headers(),  # 修复：使用session构建headers
            "payload": {"call_id": call_id}
        }

        await self.network.send(msg)
        self.current_call = None
        self._notify_ui("status", "已拒绝通话")
        logger.info(f"Rejected call {call_id}")
        return {"status": 200, "call_id": call_id}

    async def end_call(self) -> Dict[str, Any]:
        """End the current call."""
        if not self.current_call:
            logger.info("[END CALL] No active call to end")
            return {"status": 200}

        call_id = self.current_call.get("call_id")
        call_type = self.current_call.get("call_type", "direct")
        logger.info(f"[END CALL] Ending call {call_id}, type={call_type}...")

        # 计算通话时长并准备通话结束信息
        call_end_info = self._prepare_call_end_info("local")

        # 先将状态设为结束，停止音频发送循环
        if self.current_call:
            logger.info("[END CALL] Setting call status to 'ending'")
            self.current_call["status"] = "ending"

        # 停止音频流
        logger.info("[END CALL] Stopping audio streams...")
        self._stop_audio_streams()

        # 短暂等待清理完成
        await asyncio.sleep(0.1)

        # 发送end消息到服务器
        msg = {
            "id": uuid.uuid4().hex,
            "type": "request",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_END.value,
            "headers": self.session.build_headers(),
            "payload": {"call_id": call_id}
        }

        try:
            logger.info("[END CALL] Sending VOICE_END message to server...")
            await self.network.send(msg)
            logger.info("[END CALL] VOICE_END message sent successfully")
        except Exception as e:
            logger.warning(f"[END CALL] Failed to send end_call message: {e}")

        # 最后清除通话状态
        self.current_call = None
        logger.info("[END CALL] Call state cleared")

        # 发送通话结束通知到UI
        # 对于群聊，只在系统日志显示，不添加到会话（通过add_to_conversation=False标记）
        # 对于私聊，添加到会话
        call_end_info["add_to_conversation"] = (call_type == "direct")
        self._notify_ui("call_ended", call_end_info)
        self._notify_ui("status", "通话已结束")
        logger.info(f"[END CALL] Call {call_id} ended successfully")
        return {"status": 200, "call_id": call_id}

    async def _start_audio_streams_async(self) -> None:
        """Start capturing and playing audio (async version)."""
        try:
            logger.info("[START AUDIO] Starting audio streams...")

            self.audio_handler = AudioHandler(
                sample_rate=48000,
                channels=1,
                frame_duration=20,
                use_opus=OPUS_AVAILABLE
            )

            self.audio_handler.start_input()
            self.audio_handler.start_output()

            # Start audio capture thread
            self._audio_thread = threading.Thread(
                target=self._audio_capture_loop,
                name="VoiceCapture",
                daemon=True
            )
            self._audio_thread.start()

            # Start audio send task
            self._send_task = asyncio.create_task(self._audio_send_loop(), name="VoiceSend")

            logger.info("[START AUDIO] Audio streams started successfully")
        except Exception as e:
            logger.error(f"[START AUDIO] Failed to start audio streams: {e}", exc_info=True)
            self._stop_audio_streams()
            self._notify_ui("error", f"音频启动失败: {e}")

    def _start_audio_streams(self) -> None:
        """Start capturing and playing audio (sync wrapper for backward compatibility)."""
        # 这个方法只是一个包装器，实际工作由异步版本完成
        # 如果从异步上下文调用，应该使用 _start_audio_streams_async
        try:
            logger.info("[START AUDIO] Starting audio streams (sync wrapper)...")

            logger.info("[START AUDIO] Creating audio handler...")
            self.audio_handler = AudioHandler(
                sample_rate=48000,
                channels=1,
                frame_duration=20,
                use_opus=OPUS_AVAILABLE
            )

            logger.info("[START AUDIO] Starting audio input (microphone)...")
            self.audio_handler.start_input()

            logger.info("[START AUDIO] Starting audio output (speaker)...")
            self.audio_handler.start_output()

            # Start audio capture thread
            logger.info("[START AUDIO] Starting audio capture thread...")
            self._audio_thread = threading.Thread(
                target=self._audio_capture_loop,
                name="VoiceCapture",
                daemon=True
            )
            self._audio_thread.start()

            # Start audio send task
            logger.info("[START AUDIO] Starting audio send task...")
            self._send_task = asyncio.create_task(self._audio_send_loop(), name="VoiceSend")

            logger.info("[START AUDIO] Audio streams started successfully")
        except Exception as e:
            logger.error(f"[START AUDIO] Failed to start audio streams: {e}", exc_info=True)
            # 启动失败时清理
            self._stop_audio_streams()
            self._notify_ui("error", f"音频启动失败: {e}")

    def _stop_audio_streams(self) -> None:
        """Stop audio capture and playback."""
        logger.info("[VOICE CLEANUP] Starting audio streams cleanup...")

        # 取消发送任务
        if self._send_task and not self._send_task.done():
            logger.info("[VOICE CLEANUP] Cancelling audio send task...")
            self._send_task.cancel()
            self._send_task = None

        # 注意：不要立即清理audio_handler，因为捕获线程可能还在使用
        # 先让线程检测到状态变化并退出（最多20ms一帧）
        # 给线程最多100ms时间退出
        if self._audio_thread and self._audio_thread.is_alive():
            logger.info("[VOICE CLEANUP] Waiting for audio capture thread to exit...")
            for i in range(10):  # 最多等待100ms
                if not self._audio_thread.is_alive():
                    logger.info(f"[VOICE CLEANUP] Audio capture thread exited after {i*10}ms")
                    break
                time.sleep(0.01)  # 等待10ms
            else:
                logger.warning("[VOICE CLEANUP] Audio capture thread still alive after 100ms, forcing cleanup")

        self._audio_thread = None

        # 现在安全地清理音频处理器
        if self.audio_handler:
            logger.info("[VOICE CLEANUP] Cleaning up audio handler...")
            try:
                self.audio_handler.cleanup()
            except Exception as e:
                logger.error(f"Error during audio handler cleanup: {e}")
            self.audio_handler = None

        # 清空并重新创建音频队列
        try:
            logger.info("[VOICE CLEANUP] Clearing audio queue...")
            cleared_count = 0
            while not self._audio_send_queue.empty():
                try:
                    self._audio_send_queue.get_nowait()
                    cleared_count += 1
                except:
                    break
            if cleared_count > 0:
                logger.info(f"[VOICE CLEANUP] Cleared {cleared_count} frames from queue")

            # 重新创建队列以确保完全清洁
            self._audio_send_queue = queue.Queue()
            logger.info("[VOICE CLEANUP] Audio queue recreated")
        except Exception as e:
            logger.error(f"[VOICE CLEANUP] Error clearing queue: {e}")
            # 强制重新创建
            self._audio_send_queue = queue.Queue()

        logger.info("[VOICE CLEANUP] Audio streams cleanup completed")


    def _audio_capture_loop(self) -> None:
        """Capture audio in background thread and queue for sending."""
        logger.info("[AUDIO CAPTURE] Audio capture thread started")
        frame_count = 0
        while self.audio_handler and self.current_call and self.current_call.get("status") == "connected":
            try:
                # 双重检查：确保audio_handler仍然存在
                handler = self.audio_handler
                if not handler:
                    break

                frame = handler.read_frame()
                if frame:
                    self._audio_send_queue.put(frame)
                    frame_count += 1
                    if frame_count % 50 == 0:  # 每50帧记录一次（约1秒）
                        logger.debug(f"[AUDIO CAPTURE] Captured {frame_count} frames, queue size: {self._audio_send_queue.qsize()}")
                else:
                    time.sleep(0.001)  # Small delay on error
            except Exception as e:
                logger.error(f"[AUDIO CAPTURE] Audio capture error: {e}")
                break
        logger.info(f"[AUDIO CAPTURE] Audio capture thread exiting (captured {frame_count} frames total)")

    async def _audio_send_loop(self) -> None:
        """Send audio frames from queue to network."""
        while True:
            try:
                # 检查通话状态，如果通话已结束则退出
                if not self.current_call or self.current_call.get("status") not in ("connected", "ringing", "calling"):
                    logger.info("Audio send loop: call ended, exiting")
                    break

                # Get frame from queue (non-blocking with timeout)
                try:
                    frame = self._audio_send_queue.get(timeout=0.1)
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                # 再次检查通话状态（双重检查，防止在获取frame期间状态改变）
                if self.current_call and self.current_call.get("status") == "connected":
                    call_id = self.current_call.get("call_id")
                    msg = {
                        "id": uuid.uuid4().hex[:8],  # Short ID for efficiency
                        "type": "event",
                        "timestamp": int(time.time()),
                        "command": MsgType.VOICE_DATA.value,
                        "headers": self.session.build_headers(),  # 修复：使用session构建headers
                        "payload": {
                            "call_id": call_id,
                            "data": frame.hex(),  # Encode bytes as hex string
                            "codec": "opus" if OPUS_AVAILABLE else "pcm",
                            "seq": int(time.time() * 1000)  # Sequence number
                        }
                    }
                    try:
                        await self.network.send(msg)
                    except Exception as send_error:
                        # 发送失败，但不立即退出，让状态检查来决定是否退出
                        logger.debug(f"Audio send failed (will retry if call still active): {send_error}")
                        await asyncio.sleep(0.05)  # 短暂延迟后重试
            except asyncio.CancelledError:
                logger.info("Audio send loop cancelled")
                break
            except Exception as e:
                # 其他未预期的错误，记录但不终止循环（除非通话状态已改变）
                logger.warning(f"Unexpected error in audio send loop: {e}")
                if not self.current_call or self.current_call.get("status") not in ("connected", "ringing", "calling"):
                    logger.info("Audio send loop: call no longer active after error, exiting")
                    break
                await asyncio.sleep(0.1)  # 短暂延迟后继续

    async def _handle_call_ack(self, message: Dict[str, Any]) -> None:
        """Handle acknowledgment of call initiation."""
        payload = message.get("payload", {})
        status = payload.get("status", 500)

        if status == 200:
            call_id = payload.get("call_id")
            if self.current_call and self.current_call.get("call_id") == call_id:
                self.current_call["status"] = "ringing"
                self._notify_ui("status", "对方响铃中...")
        else:
            error_msg = payload.get("error_message", "Call failed")
            self._notify_ui("error", f"呼叫失败: {error_msg}")
            self.current_call = None

    async def _handle_voice_event(self, message: Dict[str, Any]) -> None:
        """Handle voice call events from server."""
        try:
            payload = message.get("payload", {})
            event_type = payload.get("event_type")
            call_id = payload.get("call_id")

            logger.info(f"[VOICE CLIENT] Received voice event: {event_type}, call_id={call_id}, full_payload={payload}")
            logger.info(f"[VOICE CLIENT] Current call state: {self.current_call}")

            if event_type == "incoming":
                # Incoming call
                from_user = payload.get("from_user")
                call_type = payload.get("call_type", "direct")
                target = payload.get("target", {})

                self.current_call = {
                    "call_id": call_id,
                    "from_user": from_user,
                    "call_type": call_type,
                    "target_type": target.get("type"),
                    "target_id": target.get("id"),
                    "status": "incoming",
                    "is_initiator": False,
                    "start_time": time.time(),
                    "connect_time": None,
                    "participants": [from_user],  # 添加参与者列表（发起者）
                }
                self._notify_ui("incoming_call", {
                    "call_id": call_id,
                    "from_user": from_user,
                    "call_type": call_type
                })

            elif event_type == "connected":
                # Call connected
                if self.current_call and self.current_call.get("call_id") == call_id:
                    self.current_call["status"] = "connected"
                    self.current_call["connect_time"] = time.time()
                    # 更新参与者列表
                    members = payload.get("members", [])
                    if members:
                        self.current_call["participants"] = members
                        logger.info(f"[VOICE CLIENT] Call connected with {len(members)} participants: {members}")
                    await self._start_audio_streams_async()  # 使用异步版本
                    self._notify_ui("status", "通话已接通")

            elif event_type in ("ended", "rejected"):
                # Call ended or rejected
                logger.info(f"[VOICE CLIENT] Processing {event_type} event, current_call exists: {self.current_call is not None}")
                if self.current_call:
                    logger.info(f"[VOICE CLIENT] Current call_id: {self.current_call.get('call_id')}, event call_id: {call_id}")

                # 检查是否是当前通话或者是之前退出的通话的最终结束通知
                is_current_call = self.current_call and self.current_call.get("call_id") == call_id
                has_call_info_in_payload = all(k in payload for k in ["call_type", "target_type", "target_id", "participants"])

                if is_current_call or has_call_info_in_payload:
                    logger.info(f"[VOICE CLIENT] Ending call {call_id} due to {event_type}")

                    try:
                        # 准备通话结束信息
                        logger.info(f"[VOICE CLIENT] Preparing call end info...")

                        if is_current_call:
                            # 当前通话，使用本地信息
                            call_end_info = self._prepare_call_end_info("remote")
                        else:
                            # 已退出的通话，使用服务器提供的信息
                            duration = payload.get("duration", 0)
                            minutes = duration // 60
                            seconds = duration % 60
                            duration_str = f"{minutes:02d}:{seconds:02d}"

                            participants = payload.get("participants", [])
                            call_type = payload.get("call_type", "direct")
                            target_type = payload.get("target_type", "user")
                            target_id = payload.get("target_id", "未知")

                            # 确定对方是谁
                            other_party = target_id
                            if call_type == "direct":
                                # 私聊：对方是参与者中不是自己的那个
                                for p in participants:
                                    if p != self.session.user_id:
                                        other_party = p
                                        break

                            call_end_info = {
                                "duration": duration,
                                "duration_str": duration_str,
                                "end_source": "remote",
                                "other_party": other_party,
                                "target_type": target_type,
                                "target_id": target_id,
                                "call_type": call_type,
                                "participants": participants,
                                "was_connected": duration > 0
                            }

                        # 对于服务器发送的ended事件，总是添加到会话（因为这表示通话真正结束）
                        call_end_info["add_to_conversation"] = True
                        logger.info(f"[VOICE CLIENT] Call end info prepared: {call_end_info}")
                    except Exception as e:
                        logger.error(f"[VOICE CLIENT] Error preparing call end info: {e}", exc_info=True)
                        call_end_info = {
                            "duration": 0,
                            "duration_str": "00:00",
                            "end_source": "remote",
                            "other_party": "未知",
                            "target_type": "user",
                            "target_id": "未知",
                            "was_connected": False,
                            "add_to_conversation": True
                        }

                    # 如果是当前通话，停止音频流
                    if is_current_call:
                        try:
                            # 先设置状态为ending，让音频发送循环退出
                            logger.info(f"[VOICE CLIENT] Setting call status to ending...")
                            self.current_call["status"] = "ending"

                            # 停止音频流
                            logger.info(f"[VOICE CLIENT] Stopping audio streams...")
                            self._stop_audio_streams()
                            logger.info(f"[VOICE CLIENT] Audio streams stopped")
                        except Exception as e:
                            logger.error(f"[VOICE CLIENT] Error stopping audio streams: {e}", exc_info=True)

                        # 清除通话状态
                        logger.info(f"[VOICE CLIENT] Clearing current_call...")
                        self.current_call = None

                    try:
                        # 发送通话结束事件到UI
                        logger.info(f"[VOICE CLIENT] Notifying UI of call_ended...")
                        self._notify_ui("call_ended", call_end_info)
                        logger.info(f"[VOICE CLIENT] UI notified successfully")
                    except Exception as e:
                        logger.error(f"[VOICE CLIENT] Error notifying UI: {e}", exc_info=True)

                    msg = "对方已拒绝" if event_type == "rejected" else "通话已结束"
                    self._notify_ui("status", msg)
                    logger.info(f"[VOICE CLIENT] Call {call_id} ended successfully")
                else:
                    logger.warning(f"[VOICE CLIENT] Received {event_type} for call {call_id} but current_call mismatch or None and no call info in payload")

            elif event_type == "error":
                # Call error
                error_msg = payload.get("message", "Unknown error")
                self._notify_ui("error", f"通话错误: {error_msg}")
                # 先设置状态，然后停止音频流
                if self.current_call:
                    self.current_call["status"] = "error"
                self._stop_audio_streams()
                self.current_call = None

            elif event_type in ("member_joined", "member_left"):
                # Group call member events
                members = payload.get("members", [])
                if self.current_call:
                    self.current_call["participants"] = members
                    logger.info(f"[VOICE CLIENT] Members updated: {members}")

                    # 如果是自己刚加入（member_joined），需要启动音频
                    if event_type == "member_joined":
                        user_id = payload.get("user_id")
                        if user_id == self.session.user_id:
                            logger.info(f"[VOICE CLIENT] I just joined the call, starting audio streams")
                            self.current_call["status"] = "connected"
                            self.current_call["connect_time"] = time.time()
                            await self._start_audio_streams_async()

                self._notify_ui("members_changed", members)

        except Exception as e:
            logger.error(f"[VOICE CLIENT] Fatal error in _handle_voice_event: {e}", exc_info=True)
            # 确保即使出错也清理资源
            try:
                self._stop_audio_streams()
                self.current_call = None
                self._notify_ui("error", f"处理语音事件时出错: {e}")
            except:
                pass

    async def _handle_voice_data(self, message: Dict[str, Any]) -> None:
        """Handle incoming voice data."""
        if not self.current_call or self.current_call.get("status") != "connected":
            return

        payload = message.get("payload", {})
        call_id = payload.get("call_id")

        if call_id != self.current_call.get("call_id"):
            return

        data_hex = payload.get("data")
        if data_hex and self.audio_handler:
            try:
                audio_data = bytes.fromhex(data_hex)
                self.audio_handler.write_frame(audio_data)
            except Exception as e:
                logger.error(f"Failed to play audio frame: {e}")

    def _notify_ui(self, event_type: str, data: Any) -> None:
        """Notify UI about voice events."""
        if self.ui_queue:
            self.ui_queue.put(("voice", {"type": event_type, "data": data}))

    def _prepare_call_end_info(self, end_source: str) -> Dict[str, Any]:
        """
        Prepare call end information including duration and participants.

        Args:
            end_source: "local" if current user hung up, "remote" if other party hung up
        """
        if not self.current_call:
            return {
                "duration": 0,
                "duration_str": "00:00",
                "end_source": end_source,
                "other_party": "未知",
                "target_type": "user",
                "target_id": "未知"
            }

        # 计算通话时长
        connect_time = self.current_call.get("connect_time")
        if connect_time:
            # 通话已接通，计算实际通话时长
            duration = int(time.time() - connect_time)
        else:
            # 通话未接通（呼叫中就挂断了）
            duration = 0

        # 格式化时长为 MM:SS
        minutes = duration // 60
        seconds = duration % 60
        duration_str = f"{minutes:02d}:{seconds:02d}"

        # 确定对方是谁
        is_initiator = self.current_call.get("is_initiator", True)
        target_type = self.current_call.get("target_type", "user")
        target_id = self.current_call.get("target_id", "未知")
        call_type = self.current_call.get("call_type", "direct")
        participants = self.current_call.get("participants", [])

        if is_initiator:
            # 我是呼叫方，对方是目标
            other_party = target_id
        else:
            # 我是接听方，对方是呼叫者
            other_party = self.current_call.get("from_user", "未知")

        return {
            "duration": duration,
            "duration_str": duration_str,
            "end_source": end_source,
            "other_party": other_party,
            "target_type": target_type,
            "target_id": target_id,
            "call_type": call_type,
            "participants": participants,  # 添加参与者列表
            "was_connected": connect_time is not None
        }

    def get_current_call(self) -> Optional[Dict[str, Any]]:
        """Get current call information."""
        return self.current_call

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.current_call:
            await self.end_call()
        self._stop_audio_streams()
