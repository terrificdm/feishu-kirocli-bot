"""Feishu (Lark) bot: WebSocket message receiving and sending."""

import base64
import json
import logging
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    P2ImMessageReceiveV1,
)
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

log = logging.getLogger(__name__)


class FeishuBot:
    def __init__(self, app_id: str, app_secret: str, bot_name: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._bot_name = bot_name
        self._on_message: Callable | None = None
        # Lark API client for sending messages
        self._client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

    def on_message(self, handler: Callable):
        """Register message handler: handler(chat_id, chat_type, text, mentions_bot)"""
        self._on_message = handler

    def start(self):
        """Start WebSocket connection (blocking)."""
        event_handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_event)
            .build()
        )
        ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        log.info("[Feishu] Starting WebSocket connection...")
        ws_client.start()

    def send_text(self, chat_id: str, text: str):
        """Send a message to a chat using interactive card for better formatting."""
        # Use card format for markdown support
        self.send_card(chat_id, text)

    def send_plain_text(self, chat_id: str, text: str):
        """Send a plain text message (no formatting)."""
        max_len = 28000
        chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] if len(text) > max_len else [text]

        for chunk in chunks:
            body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type("text") \
                .content(json.dumps({"text": chunk})) \
                .build()
            req = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(body) \
                .build()
            resp = self._client.im.v1.message.create(req)
            if not resp.success():
                log.error("[Feishu] Send failed: %s %s", resp.code, resp.msg)

    def _build_card(self, markdown_text: str, title: str = None) -> dict:
        """Build a Feishu interactive card from markdown text."""
        elements = []
        parts = markdown_text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    elements.append({"tag": "markdown", "content": part.strip()})
            else:
                lines = part.split("\n", 1)
                lang = lines[0].strip() if lines else ""
                code = lines[1] if len(lines) > 1 else part
                elements.append({"tag": "markdown", "content": f"```{lang}\n{code.strip()}\n```"})
        if not elements:
            elements.append({"tag": "markdown", "content": markdown_text})
        card = {"config": {"wide_screen_mode": True}, "elements": elements}
        if title:
            card["header"] = {"title": {"tag": "plain_text", "content": title}}
        return card

    def send_card(self, chat_id: str, markdown_text: str, title: str = None):
        """Send an interactive card message with markdown support."""
        card = self._build_card(markdown_text, title)
        body = CreateMessageRequestBody.builder() \
            .receive_id(chat_id) \
            .msg_type("interactive") \
            .content(json.dumps(card, ensure_ascii=False)) \
            .build()
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            log.error("[Feishu] Send card failed: code=%s msg=%s", resp.code, resp.msg)
            self.send_plain_text(chat_id, markdown_text)
            return None
        else:
            log.info("[Feishu] Card sent successfully to %s", chat_id)
            return resp.data.message_id if resp.data else None

    def update_card(self, message_id: str, markdown_text: str):
        """Update an existing card message."""
        if not message_id:
            log.warning("[Feishu] Cannot update card: no message_id")
            return
        card = self._build_card(markdown_text)
        body = PatchMessageRequestBody.builder() \
            .content(json.dumps(card, ensure_ascii=False)) \
            .build()
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(body) \
            .build()
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            log.error("[Feishu] Update card failed: code=%s msg=%s", resp.code, resp.msg)
        else:
            log.info("[Feishu] Card updated successfully: %s", message_id)

    def _download_image(self, message_id: str, image_key: str) -> tuple[bytes, str] | None:
        """Download image from Feishu. Returns (data, mime_type) or None."""
        try:
            req = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(image_key) \
                .type("image") \
                .build()
            resp = self._client.im.v1.message_resource.get(req)
            if not resp.success():
                log.error("[Feishu] Download image failed: %s %s", resp.code, resp.msg)
                return None
            
            # Read file content
            data = resp.file.read()
            # Detect mime type from first bytes
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                mime = "image/png"
            elif data[:2] == b'\xff\xd8':
                mime = "image/jpeg"
            elif data[:6] in (b'GIF87a', b'GIF89a'):
                mime = "image/gif"
            elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                mime = "image/webp"
            else:
                mime = "image/png"  # default
            
            log.info("[Feishu] Downloaded image: %d bytes, %s", len(data), mime)
            return (data, mime)
        except Exception as e:
            log.exception("[Feishu] Download image error: %s", e)
            return None

    def _handle_event(self, data: P2ImMessageReceiveV1):
        if not self._on_message:
            return

        try:
            msg = data.event.message
            sender = data.event.sender

            # Ignore bot messages
            if sender and sender.sender_type == "app":
                return

            chat_id = msg.chat_id
            chat_type = msg.chat_type  # "p2p" or "group"
            msg_type = msg.message_type
            message_id = msg.message_id

            # Check if bot is mentioned (for group chats)
            mentions_bot = False
            mention_map = {}
            if msg.mentions:
                for m in msg.mentions:
                    if m.name == self._bot_name:
                        mentions_bot = True
                    if m.key:
                        mention_map[m.key] = f"@{m.name}" if m.name else ""

            # Group chat: only process if bot is mentioned
            if chat_type == "group" and not mentions_bot:
                return

            text = ""
            images = []  # list of (base64_data, mime_type)

            # Handle text messages
            if msg_type == "text":
                content = json.loads(msg.content)
                text = content.get("text", "").strip()
                
                # Replace mention placeholders with names, remove bot mention
                for key, name in mention_map.items():
                    if name == f"@{self._bot_name}":
                        text = text.replace(key, "").strip()
                    else:
                        text = text.replace(key, name)

            # Handle image messages
            elif msg_type == "image":
                content = json.loads(msg.content)
                image_key = content.get("image_key", "")
                if image_key:
                    img_data = self._download_image(message_id, image_key)
                    if img_data:
                        data, mime = img_data
                        b64 = base64.b64encode(data).decode("ascii")
                        images.append((b64, mime))
                text = ""  # Image-only message

            # Handle post (rich text) - may contain images
            elif msg_type == "post":
                content = json.loads(msg.content)
                # Extract text from post content
                parts = []
                for lang_content in content.values():
                    if isinstance(lang_content, dict):
                        for item in lang_content.get("content", []):
                            if isinstance(item, list):
                                for elem in item:
                                    if isinstance(elem, dict):
                                        if elem.get("tag") == "text":
                                            parts.append(elem.get("text", ""))
                                        elif elem.get("tag") == "img":
                                            image_key = elem.get("image_key", "")
                                            if image_key:
                                                img_data = self._download_image(message_id, image_key)
                                                if img_data:
                                                    data, mime = img_data
                                                    b64 = base64.b64encode(data).decode("ascii")
                                                    images.append((b64, mime))
                text = " ".join(parts).strip()
                
                # Remove bot mention from text
                for key, name in mention_map.items():
                    if name == f"@{self._bot_name}":
                        text = text.replace(key, "").strip()
                    else:
                        text = text.replace(key, name)

            else:
                log.debug("[Feishu] Ignoring message type: %s", msg_type)
                return

            # Need at least text or images
            if not text and not images:
                return

            log.info("[Feishu] Message from %s (%s): text=%s, images=%d", 
                     chat_id, chat_type, text[:50] if text else "(none)", len(images))
            
            # Call handler with text and images
            self._on_message(chat_id, chat_type, text, mentions_bot, images)

        except Exception as e:
            log.exception("[Feishu] Handle event error: %s", e)
