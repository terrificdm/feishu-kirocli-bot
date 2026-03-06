"""Bridge: connects Feishu bot to Kiro CLI via ACP protocol."""

import json
import logging
import os
import signal
import sys
import threading
import time

from acp_client import ACPClient, PromptResult, PermissionRequest
from config import Config
from feishu_bot import FeishuBot

log = logging.getLogger(__name__)

# Max feishu message length
_MAX_MSG_LEN = 28000

# Permission request timeout (seconds)
_PERMISSION_TIMEOUT = 60


def format_response(result: PromptResult) -> str:
    """Format Kiro's response with tool call info for Feishu."""
    parts = []

    # Show tool calls
    for tc in result.tool_calls:
        icon = {"fs": "📄", "edit": "📝", "terminal": "⚡", "other": "🔧"}.get(tc.kind, "🔧")
        # If stop_reason is refusal and tool is not completed, mark as denied
        if result.stop_reason == "refusal" and tc.status != "completed":
            status_icon = "🚫"  # Denied
        else:
            status_icon = {"completed": "✅", "failed": "❌"}.get(tc.status, "⏳")
        line = f"{icon} {tc.title} {status_icon}"
        parts.append(line)

    if parts:
        parts.append("")  # blank line separator

    # Add message based on stop_reason
    if result.stop_reason == "refusal":
        if result.text:
            parts.append(result.text)
        else:
            parts.append("🚫 Operation cancelled")
        parts.append("")
        parts.append("💬 You can continue the conversation")
    elif result.text:
        parts.append(result.text)

    return "\n".join(parts) if parts else "(No response)"


class Bridge:
    def __init__(self, config: Config):
        self._config = config
        self._acp: ACPClient | None = None
        self._acp_lock = threading.Lock()
        self._last_activity = 0.0
        self._bot = FeishuBot(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.BOT_NAME)
        # chat_id -> session_id
        self._sessions: dict[str, str] = {}
        self._sessions_lock = threading.Lock()
        # chat_id -> True if currently processing
        self._processing: dict[str, bool] = {}
        self._processing_lock = threading.Lock()
        # Pending messages for debounce + collect: chat_id -> [(text, images)]
        self._pending_messages: dict[str, list[tuple[str, list | None]]] = {}
        self._pending_lock = threading.Lock()
        self._debounce_timers: dict[str, threading.Timer] = {}
        # Pending permission requests: chat_id -> (event, result_holder)
        self._pending_permissions: dict[str, tuple[threading.Event, list]] = {}
        self._pending_permissions_lock = threading.Lock()
        # session_id -> chat_id mapping (for permission requests)
        self._session_to_chat: dict[str, str] = {}
        # chat_id -> saved mode_id (for restoring agent after session_load)
        self._session_modes: dict[str, str] = {}
        # chat_id -> active card message_id (for streaming and permission reuse)
        self._active_cards: dict[str, str] = {}
        # Idle checker thread
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

    def start(self):
        """Start the bridge."""
        # Don't start ACP client here - start on demand
        log.info("[Bridge] Starting in on-demand mode (kiro-cli will start when needed)")

        # Start idle checker thread
        self._idle_checker_stop.clear()
        self._idle_checker_thread = threading.Thread(target=self._idle_checker_loop, daemon=True)
        self._idle_checker_thread.start()

        # Register message handler
        self._bot.on_message(self._handle_message)

        # Setup graceful shutdown
        def shutdown(sig, frame):
            log.info("[Bridge] Shutting down...")
            self._idle_checker_stop.set()
            # Cancel all debounce timers
            with self._pending_lock:
                for timer in self._debounce_timers.values():
                    timer.cancel()
                self._debounce_timers.clear()
            self._stop_acp()
            sys.exit(0)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        log.info("[Bridge] Starting Feishu bot...")
        # This blocks
        self._bot.start()

    def _start_acp(self):
        """Start ACP client if not running."""
        with self._acp_lock:
            if self._acp is not None and self._acp.is_running():
                return
            
            log.info("[Bridge] Starting kiro-cli acp...")
            self._acp = ACPClient(
                cli_path=self._config.KIRO_CLI_PATH,
            )
            
            # Determine kiro-cli working directory
            # In fixed mode, use WORKING_DIR so kiro-cli reads .kiro/settings/mcp.json from there
            # In per_chat mode, use None (inherit current directory)
            kiro_cwd = self._config.WORKING_DIR if self._config.WORKSPACE_MODE == "fixed" else None
            self._acp.start(cwd=kiro_cwd)
            if kiro_cwd:
                log.info("[Bridge] kiro-cli started with cwd: %s", kiro_cwd)
            
            self._acp.on_permission_request(self._handle_permission)
            
            # Clear old sessions since we're starting fresh
            with self._sessions_lock:
                self._sessions.clear()
            self._session_to_chat.clear()
            self._session_modes.clear()
            
            self._last_activity = time.time()
            log.info("[Bridge] kiro-cli acp started")

    def _stop_acp(self):
        """Stop ACP client."""
        with self._acp_lock:
            if self._acp is not None:
                log.info("[Bridge] Stopping kiro-cli acp...")
                self._acp.stop()
                self._acp = None
                
                # Clear sessions
                with self._sessions_lock:
                    self._sessions.clear()
                self._session_to_chat.clear()
                self._session_modes.clear()
                
                log.info("[Bridge] kiro-cli acp stopped")

    def _ensure_acp(self) -> ACPClient:
        """Ensure ACP client is running, start if needed."""
        self._start_acp()
        self._last_activity = time.time()
        return self._acp

    def _idle_checker_loop(self):
        """Background thread that checks for idle timeout."""
        idle_timeout = self._config.IDLE_TIMEOUT
        if idle_timeout <= 0:
            log.info("[Bridge] Idle timeout disabled")
            return
        
        while not self._idle_checker_stop.wait(timeout=30):  # Check every 30 seconds
            idle_time = 0
            with self._acp_lock:
                if self._acp is None or not self._acp.is_running():
                    continue
                
                idle_time = time.time() - self._last_activity
                if idle_time > idle_timeout:
                    log.info("[Bridge] Idle timeout (%.0fs), stopping kiro-cli...", idle_time)
            
            # Stop outside the lock to avoid deadlock
            if idle_time > idle_timeout:
                self._stop_acp()

    def _handle_permission(self, request: PermissionRequest) -> str | None:
        """Handle permission request from Kiro - ask user via Feishu."""
        # Auto-approve if configured
        if config.AUTO_APPROVE:
            log.info("[Bridge] Auto-approving permission request: %s", request.description)
            return "allow"
        
        session_id = request.session_id
        
        # Find chat_id for this session
        chat_id = self._session_to_chat.get(session_id)
        if not chat_id:
            log.warning("[Bridge] No chat_id found for session %s, auto-denying", session_id)
            return "deny"

        # Format the permission request message
        icon = "🔐"
        msg = f"{icon} **Kiro requests permission:**\n\n"
        msg += f"📋 {request.title}\n\n"
        msg += "Reply: **y**(allow) / **n**(deny) / **t**(trust, always allow)\n"
        msg += f"⏱️ Auto-deny in {_PERMISSION_TIMEOUT}s if no response"

        # Update active card if available (keeps chronological order)
        active_card = self._active_cards.get(chat_id)
        if active_card:
            self._bot.update_card(active_card, msg)
        else:
            self._bot.send_text(chat_id, msg)
        log.info("[Bridge] Sent permission request to chat %s: %s", chat_id, request.title)

        # Wait for user response
        evt = threading.Event()
        result_holder: list = []  # Will hold the decision

        with self._pending_permissions_lock:
            self._pending_permissions[chat_id] = (evt, result_holder)

        try:
            # Wait for response or timeout
            if evt.wait(timeout=_PERMISSION_TIMEOUT):
                if result_holder:
                    decision = result_holder[0]
                    log.info("[Bridge] User decision for %s: %s", request.title, decision)
                    # Send new card below user's reply for the result
                    if active_card:
                        new_card = self._bot.send_card(chat_id, "🤔 Processing...")
                        if new_card:
                            self._active_cards[chat_id] = new_card
                    return decision
            
            # Timeout - update card in place (no user message between)
            if active_card:
                self._bot.update_card(active_card, "⏱️ Timeout, auto-denied")
            else:
                self._bot.send_text(chat_id, "⏱️ Timeout, auto-denied")
            log.warning("[Bridge] Permission request timed out for: %s", request.title)
            return "deny"
        finally:
            with self._pending_permissions_lock:
                self._pending_permissions.pop(chat_id, None)

    def _handle_message(self, chat_id: str, chat_type: str, text: str, mentions_bot: bool, images: list[tuple[str, str]] | None = None):
        """Handle incoming Feishu message (called from event loop, must not block long).
        
        Args:
            chat_id: Chat ID
            chat_type: "p2p" or "group"
            text: Text content
            mentions_bot: Whether bot was mentioned
            images: List of (base64_data, mime_type) tuples (WIP - currently ignored)
        """
        text_stripped = text.strip()
        text_lower = text_stripped.lower()

        # Image processing - log details for debugging
        if images:
            log.info("[Bridge] Received %d image(s)", len(images))
            for i, (b64_data, mime) in enumerate(images):
                log.info("[Bridge] Image %d: %s, base64 len=%d", i+1, mime, len(b64_data))

        # Check for permission response first
        with self._pending_permissions_lock:
            pending = self._pending_permissions.get(chat_id)
        
        if pending:
            evt, result_holder = pending
            # Check if this is a y/n/t response
            if text_lower in ('y', 'yes', 'ok'):
                result_holder.append("allow_once")
                evt.set()
                return
            elif text_lower in ('n', 'no'):
                result_holder.append("deny")
                evt.set()
                return
            elif text_lower in ('t', 'trust', 'always'):
                result_holder.append("allow_always")
                evt.set()
                return
            else:
                # Not a clear y/n/t, remind user
                self._bot.send_text(chat_id, "⚠️ Please reply y(allow) / n(deny) / t(trust)")
                return

        # Check for cancel command - allow even when processing
        if text_lower in ("cancel", "stop"):
            self._handle_cancel(chat_id)
            return

        # Check for slash commands (only for text-only messages)
        if text_stripped.startswith("/"):
            self._handle_command(chat_id, text_stripped)
            return

        # Store in pending buffer for debounce + collect
        with self._pending_lock:
            if chat_id not in self._pending_messages:
                self._pending_messages[chat_id] = []
            pending = self._pending_messages[chat_id]
            if len(pending) >= self._config.PENDING_CAP:
                self._bot.send_text(chat_id,
                                    f"⚠️ Too many pending messages (max {self._config.PENDING_CAP})")
                return
            pending.append((text, images))

        with self._processing_lock:
            is_busy = self._processing.get(chat_id, False)

        if not is_busy:
            self._reset_debounce(chat_id)

    def _handle_cancel(self, chat_id: str):
        """Handle cancel request - cancels current operation and clears pending."""
        # Cancel debounce timer and clear pending messages
        pending_cleared = 0
        with self._pending_lock:
            timer = self._debounce_timers.pop(chat_id, None)
            if timer:
                timer.cancel()
            pending_cleared = len(self._pending_messages.pop(chat_id, []))
        
        with self._sessions_lock:
            session_id = self._sessions.get(chat_id)

        if not session_id:
            if pending_cleared:
                self._bot.send_text(chat_id, f"🗑️ Cleared {pending_cleared} queued message(s)")
            else:
                self._bot.send_text(chat_id, "❌ No active session")
            return

        with self._acp_lock:
            if self._acp is None or not self._acp.is_running():
                if pending_cleared:
                    self._bot.send_text(chat_id, f"🗑️ Cleared {pending_cleared} queued message(s)")
                else:
                    self._bot.send_text(chat_id, "❌ Kiro is not running")
                return
            acp = self._acp

        try:
            acp.session_cancel(session_id)
            msg = "⏹️ Cancel request sent"
            if pending_cleared:
                msg += f"\n🗑️ Cleared {pending_cleared} queued message(s)"
            self._bot.send_text(chat_id, msg)
        except Exception as e:
            log.error("[Bridge] Cancel failed: %s", e)
            self._bot.send_text(chat_id, f"❌ Cancel failed: {e}")

    def _handle_command(self, chat_id: str, text: str):
        """Handle slash commands like /mode, /model, /help."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/agent":
            self._handle_agent_command(chat_id, arg)
        elif cmd == "/model":
            self._handle_model_command(chat_id, arg)
        elif cmd == "/help":
            self._handle_help_command(chat_id)
        else:
            self._bot.send_text(chat_id, f"❓ Unknown command: {cmd}\n💡 Send /help for available commands")

    def _handle_agent_command(self, chat_id: str, mode_arg: str):
        """Handle /mode or /agent command."""
        with self._sessions_lock:
            session_id = self._sessions.get(chat_id)

        if not session_id:
            self._bot.send_text(chat_id, "❌ No session yet. Send a message first.")
            return

        with self._acp_lock:
            if self._acp is None or not self._acp.is_running():
                self._bot.send_text(chat_id, "❌ Kiro is not running")
                return
            acp = self._acp

        # If no arg, list available modes
        if not mode_arg:
            modes_data = acp.get_session_modes(session_id)
            if not modes_data:
                self._bot.send_text(chat_id, "❓ No agent info available\n💡 Try sending a message first")
                return
            
            # Parse modes structure: {currentModeId: str, availableModes: [{id, name}, ...]}
            current_mode = modes_data.get("currentModeId", "")
            available_modes = modes_data.get("availableModes", [])
            
            if not available_modes:
                self._bot.send_text(chat_id, "❓ No agents available")
                return
            
            lines = ["📋 **Available Agents:**", ""]
            for mode in available_modes:
                mode_id = mode.get("id", "") if isinstance(mode, dict) else str(mode)
                mode_name = mode.get("name", mode_id) if isinstance(mode, dict) else str(mode)
                marker = " ✓" if mode_id == current_mode else ""
                # Avoid redundant display when id == name
                if mode_id == mode_name:
                    lines.append(f"• {mode_id}{marker}")
                else:
                    lines.append(f"• {mode_id} - {mode_name}{marker}")
            lines.append("")
            lines.append(f"Current: **{current_mode}**")
            lines.append("💡 Use /agent agent_name to switch")
            self._bot.send_text(chat_id, "\n".join(lines))
            return

        # Validate agent name before calling Kiro (invalid names crash Kiro!)
        modes_data = acp.get_session_modes(session_id)
        valid_mode_ids = set()
        if modes_data:
            for mode in modes_data.get("availableModes", []):
                mid = mode.get("id", "") if isinstance(mode, dict) else str(mode)
                if mid:
                    valid_mode_ids.add(mid)
        
        if valid_mode_ids and mode_arg not in valid_mode_ids:
            self._bot.send_text(chat_id, f"❌ Invalid agent: {mode_arg}\n\n💡 Use /agent to see available agents")
            return

        # Switch mode
        try:
            result = acp.session_set_mode(session_id, mode_arg)
            self._session_modes[chat_id] = mode_arg
            self._bot.send_text(chat_id, f"✅ Switched to agent: **{mode_arg}**")
        except Exception as e:
            log.error("[Bridge] Set mode failed: %s", e)
            self._bot.send_text(chat_id, f"❌ Switch failed: {e}")

    def _handle_model_command(self, chat_id: str, model_arg: str):
        """Handle /model command."""
        with self._sessions_lock:
            session_id = self._sessions.get(chat_id)

        if not session_id:
            self._bot.send_text(chat_id, "❌ No session yet. Send a message first.")
            return

        with self._acp_lock:
            if self._acp is None or not self._acp.is_running():
                self._bot.send_text(chat_id, "❌ Kiro is not running")
                return
            acp = self._acp

        if not model_arg:
            # Get model options from session/new response
            options = acp.get_model_options(session_id)
            current_model = acp.get_current_model(session_id)
            
            if options:
                lines = ["📋 **Available Models:**", ""]
                for opt in options:
                    if isinstance(opt, dict):
                        model_id = opt.get("modelId", "") or opt.get("id", "")
                        model_name = opt.get("name", model_id)
                    else:
                        model_id = str(opt)
                        model_name = model_id
                    
                    if model_id:
                        marker = " ✓" if model_id == current_model else ""
                        if model_id == model_name:
                            lines.append(f"• {model_id}{marker}")
                        else:
                            lines.append(f"• {model_id} - {model_name}{marker}")
                lines.append("")
                lines.append(f"Current: **{current_model}**")
                lines.append("💡 Use /model model_name to switch")
                self._bot.send_text(chat_id, "\n".join(lines))
            else:
                self._bot.send_text(chat_id, "❓ Cannot get model list\n\n💡 Use /model model_name to switch directly")
            return

        # Validate model name before calling Kiro (invalid names crash Kiro!)
        options = acp.get_model_options(session_id)
        valid_model_ids = set()
        if options:
            for opt in options:
                if isinstance(opt, dict):
                    mid = opt.get("modelId", "") or opt.get("id", "")
                    if mid:
                        valid_model_ids.add(mid)
                else:
                    valid_model_ids.add(str(opt))
        
        if valid_model_ids and model_arg not in valid_model_ids:
            self._bot.send_text(chat_id, f"❌ Invalid model: {model_arg}\n\n💡 Use /model to see available models")
            return

        try:
            result = acp.session_set_model(session_id, model_arg)
            self._bot.send_text(chat_id, f"✅ Switched to model: **{model_arg}**")
        except Exception as e:
            log.error("[Bridge] Set model failed: %s", e)
            self._bot.send_text(chat_id, f"❌ Switch failed: {e}")

    def _handle_help_command(self, chat_id: str):
        """Show available commands."""
        help_text = """📚 **Available Commands:**

**Agent:**
• /agent - List available agents
• /agent agent_name - Switch agent

**Model:**
• /model - List available models
• /model model_name - Switch model

**Other:**
• /help - Show this help"""
        self._bot.send_text(chat_id, help_text)

    def _reset_debounce(self, chat_id: str):
        """Start or reset the debounce timer for a chat."""
        with self._pending_lock:
            old_timer = self._debounce_timers.get(chat_id)
            if old_timer:
                old_timer.cancel()
            timer = threading.Timer(
                self._config.DEBOUNCE,
                self._debounce_fire,
                args=(chat_id,),
            )
            timer.daemon = True
            self._debounce_timers[chat_id] = timer
            timer.start()

    def _debounce_fire(self, chat_id: str):
        """Called when debounce timer expires. Starts processing in a new thread."""
        with self._pending_lock:
            self._debounce_timers.pop(chat_id, None)
        threading.Thread(
            target=self._process_message,
            args=(chat_id,),
            daemon=True,
        ).start()

    @staticmethod
    def _merge_messages(messages: list[tuple[str, list | None]]) -> tuple[str, list | None]:
        """Merge multiple pending messages into a single prompt."""
        if len(messages) == 1:
            return messages[0]

        texts = [text for text, _ in messages if text]
        all_images: list = []
        for _, images in messages:
            if images:
                all_images.extend(images)

        merged_text = "\n".join(texts)
        return merged_text, all_images or None

    def _process_message(self, chat_id: str):
        """Process pending messages with collect semantics."""
        with self._processing_lock:
            if self._processing.get(chat_id):
                return  # Another thread is already processing; it will drain pending
            self._processing[chat_id] = True

        try:
            self._process_message_loop(chat_id)
        finally:
            with self._processing_lock:
                self._processing[chat_id] = False
            # Race condition fix: if new messages arrived while we were finishing,
            # kick off another debounce so they don't get stuck in pending.
            with self._pending_lock:
                if self._pending_messages.get(chat_id):
                    self._reset_debounce(chat_id)

    def _process_message_loop(self, chat_id: str):
        """Drain and process pending messages in a loop."""
        while True:
            with self._pending_lock:
                messages = self._pending_messages.pop(chat_id, [])
            if not messages:
                break

            text, images = self._merge_messages(messages)
            if len(messages) > 1:
                log.info("[Bridge] Merged %d messages into one prompt for chat %s", len(messages), chat_id)
            self._process_single_message(chat_id, text, images)

    def _process_single_message(self, chat_id: str, text: str, images: list[tuple[str, str]] | None = None):
        """Process a single message."""
        thinking_msg_id = None
        
        # Streaming state
        _stream_lock = threading.Lock()
        _last_stream_update = [0.0]
        _STREAM_INTERVAL = 1.5  # seconds between card updates (Feishu rate limit safe)
        
        def _on_stream(chunk: str, accumulated: str):
            """Called from ACP read thread on each text chunk."""
            current_card = self._active_cards.get(chat_id)
            if not current_card:
                return
            now = time.time()
            with _stream_lock:
                elapsed = now - _last_stream_update[0]
                if elapsed >= _STREAM_INTERVAL:
                    _last_stream_update[0] = now
                else:
                    return
            # Update card outside lock
            try:
                self._bot.update_card(current_card, accumulated + " ▌")
            except Exception as e:
                log.debug("[Bridge] Stream update error: %s", e)
        
        try:
            # Send "Thinking..." and save message ID for later update
            thinking_msg_id = self._bot.send_card(chat_id, "🤔 Thinking...")
            
            # Store card handle for streaming and permission reuse
            if thinking_msg_id:
                self._active_cards[chat_id] = thinking_msg_id

            # Ensure ACP is running (start on demand)
            try:
                acp = self._ensure_acp()
            except Exception as e:
                log.error("[Bridge] Failed to start kiro-cli: %s", e)
                if thinking_msg_id:
                    self._bot.update_card(thinking_msg_id, f"❌ Failed to start Kiro: {e}")
                else:
                    self._bot.send_text(chat_id, f"❌ Failed to start Kiro: {e}")
                return

            # Get or create session
            session_id = self._get_or_create_session(chat_id, acp)

            # Track session -> chat mapping for permission requests
            self._session_to_chat[session_id] = chat_id

            # Send prompt to Kiro with optional images and streaming
            stream_cb = _on_stream if thinking_msg_id else None
            max_retries = 3
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    result = acp.session_prompt(session_id, text, images=images, on_stream=stream_cb)
                    break
                except RuntimeError as e:
                    last_error = e
                    error_str = str(e)
                    if "ValidationException" in error_str or "Internal error" in error_str:
                        if attempt < max_retries - 1:
                            log.warning("[Bridge] Transient error (attempt %d/%d): %s", 
                                       attempt + 1, max_retries, e)
                            time.sleep(1)
                            continue
                    raise
            else:
                raise last_error

            # Update activity time
            self._last_activity = time.time()

            # Format response and update the card (use _active_cards for latest reference)
            response = format_response(result)
            final_card = self._active_cards.get(chat_id) or thinking_msg_id
            if final_card:
                self._bot.update_card(final_card, response)
            else:
                self._bot.send_text(chat_id, response)

        except Exception as e:
            log.exception("[Bridge] Error processing message: %s", e)
            error_msg = str(e)
            if "cancelled" in error_msg.lower():
                error_text = "⏹️ Operation cancelled"
            else:
                error_text = f"❌ Error: {e}"
            
            # Update card (use _active_cards for latest reference)
            error_card = self._active_cards.get(chat_id) or thinking_msg_id
            if error_card:
                self._bot.update_card(error_card, error_text)
            else:
                self._bot.send_text(chat_id, error_text)
            
            # If session errored, remove it so next message creates a new one
            with self._sessions_lock:
                self._sessions.pop(chat_id, None)
            # Check if ACP process died
            with self._acp_lock:
                if self._acp is not None and not self._acp.is_running():
                    log.warning("[Bridge] kiro-cli died, will restart on next message")
                    self._acp = None
        
        finally:
            # Clean up active card reference
            self._active_cards.pop(chat_id, None)

    def _get_or_create_session(self, chat_id: str, acp: ACPClient) -> str:
        # Determine workspace directory based on mode
        if self._config.WORKSPACE_MODE == "fixed":
            # Fixed mode: all chats share the same directory
            # Useful for project-specific .kiro config (MCP servers, skills, etc.)
            work_dir = self._config.WORKING_DIR
        else:
            # Per-chat mode (default): each chat gets its own subdirectory
            work_dir = os.path.join(self._config.WORKING_DIR, chat_id)
        
        os.makedirs(work_dir, exist_ok=True)

        with self._sessions_lock:
            if chat_id in self._sessions:
                session_id = self._sessions[chat_id]
                # Try to load existing session
                try:
                    acp.session_load(session_id, work_dir)
                    # Restore agent selection (session_load resets mode to default)
                    saved_mode = self._session_modes.get(chat_id)
                    if saved_mode:
                        try:
                            acp.session_set_mode(session_id, saved_mode)
                        except Exception as e:
                            log.warning("[Bridge] Failed to restore mode '%s': %s", saved_mode, e)
                    log.info("[Bridge] Loaded existing session for chat %s", chat_id)
                    return session_id
                except Exception as e:
                    log.warning("[Bridge] Failed to load session %s: %s, creating new one", session_id, e)
                    # Fall through to create new session

        session_id, modes = acp.session_new(work_dir)
        log.info("[Bridge] Created new session %s for chat %s (cwd: %s)", session_id, chat_id, work_dir)

        with self._sessions_lock:
            self._sessions[chat_id] = session_id
        self._session_to_chat[session_id] = chat_id
        return session_id


def main():
    log_level = logging.DEBUG if Config.DEBUG else logging.INFO
    log_format = "%(asctime)s %(levelname)s %(message)s"
    handlers = [logging.StreamHandler()]
    if Config.LOG_FILE:
        handlers.append(logging.FileHandler(Config.LOG_FILE, encoding="utf-8"))
    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    config = Config()
    config.validate()

    bridge = Bridge(config)
    bridge.start()


if __name__ == "__main__":
    main()
