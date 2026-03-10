from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib import error, request

try:
    from config import (
        NAPCAT_ALLOW_GROUP,
        NAPCAT_ALLOW_PRIVATE,
        NAPCAT_API_BASE,
        NAPCAT_API_TOKEN,
        NAPCAT_GROUP_REQUIRE_AT,
        NAPCAT_REPLY_ENABLED,
    )
except Exception:
    NAPCAT_ALLOW_GROUP = False
    NAPCAT_ALLOW_PRIVATE = True
    NAPCAT_API_BASE = 'http://127.0.0.1:3000'
    NAPCAT_API_TOKEN = ''
    NAPCAT_GROUP_REQUIRE_AT = True
    NAPCAT_REPLY_ENABLED = True

try:
    from config import NAPCAT_OWNER_USER_IDS
except Exception:
    NAPCAT_OWNER_USER_IDS = []

try:
    from config import NAPCAT_OWNER_LABEL
except Exception:
    NAPCAT_OWNER_LABEL = '主人'

try:
    from config import NAPCAT_IMAGE_VISION_ENABLED
except Exception:
    NAPCAT_IMAGE_VISION_ENABLED = True

try:
    from config import NAPCAT_IMAGE_PROMPT
except Exception:
    NAPCAT_IMAGE_PROMPT = '请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。'

from .base import BaseChatAdapter, ChatMessageEvent


class NapCatOneBotAdapter(BaseChatAdapter):
    name = 'napcat_qq'

    def __init__(
        self,
        *,
        api_base: str = NAPCAT_API_BASE,
        api_token: str = NAPCAT_API_TOKEN,
        reply_enabled: bool = NAPCAT_REPLY_ENABLED,
        allow_group: bool = NAPCAT_ALLOW_GROUP,
        allow_private: bool = NAPCAT_ALLOW_PRIVATE,
        group_require_at: bool = NAPCAT_GROUP_REQUIRE_AT,
        owner_user_ids: Optional[List[str]] = None,
        owner_label: str = NAPCAT_OWNER_LABEL,
        image_vision_enabled: bool = NAPCAT_IMAGE_VISION_ENABLED,
        image_prompt: str = NAPCAT_IMAGE_PROMPT,
        filter_mode: str = 'off',
        user_whitelist: Optional[List[str]] = None,
        user_blacklist: Optional[List[str]] = None,
        group_whitelist: Optional[List[str]] = None,
        group_blacklist: Optional[List[str]] = None,
        ws_action_sender: Optional[Callable[[str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]] = None,
    ):
        self.api_base = str(api_base or '').rstrip('/')
        self.api_token = str(api_token or '').strip()
        self.reply_enabled = bool(reply_enabled)
        self.allow_group = bool(allow_group)
        self.allow_private = bool(allow_private)
        self.group_require_at = bool(group_require_at)
        raw_owner_ids = owner_user_ids if isinstance(owner_user_ids, list) else NAPCAT_OWNER_USER_IDS
        self.owner_user_ids = {str(item).strip() for item in (raw_owner_ids or []) if str(item).strip()}
        self.owner_label = str(owner_label or '主人').strip() or '主人'
        self.image_vision_enabled = bool(image_vision_enabled)
        self.image_prompt = str(image_prompt or '').strip() or '请客观详细描述这张QQ图片的内容，并提取其中可用于回复的关键信息。'
        self.filter_mode = str(filter_mode or 'off').strip().lower()
        if self.filter_mode not in {'off', 'whitelist', 'blacklist'}:
            self.filter_mode = 'off'
        self.user_whitelist = {str(item).strip() for item in (user_whitelist or []) if str(item).strip()}
        self.user_blacklist = {str(item).strip() for item in (user_blacklist or []) if str(item).strip()}
        self.group_whitelist = {str(item).strip() for item in (group_whitelist or []) if str(item).strip()}
        self.group_blacklist = {str(item).strip() for item in (group_blacklist or []) if str(item).strip()}
        self.ws_action_sender = ws_action_sender

    def set_ws_action_sender(self, sender: Optional[Callable[[str, Dict[str, Any], float], Awaitable[Dict[str, Any]]]]) -> None:
        self.ws_action_sender = sender

    def _passes_filter(self, message_type: str, user_id: str, group_id: Any) -> bool:
        mode = self.filter_mode
        if mode == 'off':
            return True

        user_key = str(user_id or '').strip()
        group_key = str(group_id or '').strip()

        if user_key and user_key in self.owner_user_ids:
            return True

        if mode == 'whitelist':
            if message_type == 'group':
                return (group_key and group_key in self.group_whitelist) or (user_key and user_key in self.user_whitelist)
            return bool(user_key and user_key in self.user_whitelist)

        if mode == 'blacklist':
            if message_type == 'group':
                if group_key and group_key in self.group_blacklist:
                    return False
                if user_key and user_key in self.user_blacklist:
                    return False
                return True
            return not bool(user_key and user_key in self.user_blacklist)

        return True

    def _message_targets_self(self, payload: Dict[str, Any], self_id: str) -> bool:
        if not self_id:
            return False
        message = payload.get('message')
        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                if str(seg.get('type') or '').lower() != 'at':
                    continue
                data = seg.get('data') or {}
                qq = str(data.get('qq') or '').strip()
                if qq == self_id or qq == 'all':
                    return True
        raw_text = str(payload.get('raw_message') or payload.get('message') or '')
        return f'[CQ:at,qq={self_id}]' in raw_text or '[CQ:at,qq=all]' in raw_text

    def _strip_self_mentions(self, text: str, self_id: str) -> str:
        cleaned = str(text or '')
        if self_id:
            cleaned = cleaned.replace(f'[CQ:at,qq={self_id}]', ' ')
        cleaned = cleaned.replace('[CQ:at,qq=all]', ' ')
        return ' '.join(cleaned.split())

    def _extract_image_segment(self, seg: Dict[str, Any]) -> Dict[str, Any]:
        data = seg.get('data') or {}
        image = {
            'url': str(data.get('url') or '').strip(),
            'file': str(data.get('file') or '').strip(),
            'summary': str(data.get('summary') or '').strip(),
            'name': str(data.get('name') or '').strip(),
        }
        return {key: value for key, value in image.items() if value}

    def _segment_to_text(self, seg: Dict[str, Any], self_id: str) -> str:
        seg_type = str(seg.get('type') or '').strip().lower()
        data = seg.get('data') or {}
        if seg_type == 'text':
            return str(data.get('text') or '')
        if seg_type == 'at':
            qq = str(data.get('qq') or '').strip()
            if qq == 'all':
                return '@全体成员'
            if self_id and qq == self_id:
                return ''
            return f'@{qq}' if qq else ''
        placeholders = {
            'image': '[图片]',
            'face': '[表情]',
            'file': '[文件]',
            'video': '[视频]',
            'record': '[语音]',
            'reply': '',
            'json': '[卡片消息]',
            'xml': '[卡片消息]',
        }
        return placeholders.get(seg_type, '')

    def _extract_message_payload(self, payload: Dict[str, Any], self_id: str) -> Tuple[str, List[Dict[str, Any]]]:
        message = payload.get('message')
        images: List[Dict[str, Any]] = []
        if isinstance(message, list):
            parts: List[str] = []
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                seg_type = str(seg.get('type') or '').strip().lower()
                if seg_type == 'image':
                    image_payload = self._extract_image_segment(seg)
                    if image_payload:
                        images.append(image_payload)
                text = self._segment_to_text(seg, self_id)
                if text:
                    parts.append(text)
            if parts or images:
                return ' '.join(' '.join(parts).split()), images
        raw_text = str(payload.get('raw_message') or payload.get('message') or '')
        return ' '.join(raw_text.split()), images

    def _parse_session(self, session_id: str) -> Tuple[str, str]:
        session_text = str(session_id or '').strip()
        if ':' not in session_text:
            raise ValueError(f'Invalid session id: {session_id}')
        chat_type, peer_id = session_text.split(':', 1)
        chat_type = chat_type.strip().lower()
        peer_id = peer_id.strip()
        if not peer_id:
            raise ValueError(f'Invalid session id: {session_id}')
        return chat_type, peer_id

    def _build_send_action(self, session_id: str, message: Any) -> Dict[str, Any]:
        chat_type, peer_id = self._parse_session(session_id)
        if chat_type == 'group':
            if not self.allow_group:
                return {'ok': False, 'reason': 'group_disabled', 'session_id': session_id}
            return {'ok': True, 'action': 'send_group_msg', 'payload': {'group_id': int(peer_id), 'message': message}}
        if chat_type == 'private':
            if not self.allow_private:
                return {'ok': False, 'reason': 'private_disabled', 'session_id': session_id}
            return {'ok': True, 'action': 'send_private_msg', 'payload': {'user_id': int(peer_id), 'message': message}}
        return {'ok': False, 'reason': f'unsupported_session_type:{chat_type}', 'session_id': session_id}

    async def _send_action(self, session_id: str, action: str, payload: Dict[str, Any], **kwargs: Any) -> Any:
        timeout = float(kwargs.get('timeout') or 8)
        ws_result = None

        if self.ws_action_sender is not None:
            try:
                ws_result = await self.ws_action_sender(action, payload, timeout)
            except Exception as exc:
                ws_result = {'ok': False, 'reason': str(exc), 'transport': 'websocket', 'session_id': session_id}
            if isinstance(ws_result, dict):
                ws_result.setdefault('session_id', session_id)
                ws_result.setdefault('transport', 'websocket')
            if isinstance(ws_result, dict) and ws_result.get('ok'):
                return ws_result

        if not self.api_base:
            if isinstance(ws_result, dict):
                return ws_result
            return {'ok': False, 'reason': 'api_base_missing', 'session_id': session_id}

        url = f'{self.api_base}/{action}'

        def _post() -> Dict[str, Any]:
            raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            req = request.Request(url, data=raw, method='POST')
            req.add_header('Content-Type', 'application/json; charset=utf-8')
            if self.api_token:
                req.add_header('Authorization', f'Bearer {self.api_token}')
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', errors='replace')
                status = getattr(resp, 'status', 200)
            try:
                parsed = json.loads(body) if body else {}
            except Exception:
                parsed = {'raw': body}
            return {'ok': True, 'status': status, 'session_id': session_id, 'response': parsed, 'transport': 'http'}

        try:
            http_result = await asyncio.to_thread(_post)
            if isinstance(ws_result, dict) and not ws_result.get('ok'):
                http_result['ws_fallback'] = ws_result
            return http_result
        except error.HTTPError as exc:
            body = exc.read().decode('utf-8', errors='replace') if hasattr(exc, 'read') else ''
            result = {'ok': False, 'reason': f'http_error:{exc.code}', 'session_id': session_id, 'body': body, 'transport': 'http'}
            if isinstance(ws_result, dict):
                result['ws_fallback'] = ws_result
            return result
        except Exception as exc:
            result = {'ok': False, 'reason': str(exc), 'session_id': session_id, 'transport': 'http'}
            if isinstance(ws_result, dict):
                result['ws_fallback'] = ws_result
            return result

    def normalize_event(self, payload: Dict[str, Any]) -> Optional[ChatMessageEvent]:
        post_type = str(payload.get('post_type') or '')
        message_type = str(payload.get('message_type') or '')
        self_id = str(payload.get('self_id') or '')
        raw_message, images = self._extract_message_payload(payload, self_id)
        if post_type != 'message' or not raw_message:
            return None

        user_id = str(payload.get('user_id') or '')
        if self_id and user_id and self_id == user_id:
            return None

        if not self._passes_filter(message_type, user_id, payload.get('group_id')):
            return None

        if message_type == 'group':
            if not self.allow_group:
                return None
            if self.group_require_at and not self._message_targets_self(payload, self_id):
                return None
            session_id = f"group:{payload.get('group_id')}"
            raw_message = self._strip_self_mentions(raw_message, self_id).strip()
            if not raw_message:
                return None
        else:
            if not self.allow_private:
                return None
            session_id = f'private:{user_id}'

        sender = payload.get('sender') or {}
        sender_name = str(sender.get('card') or sender.get('nickname') or user_id)
        is_owner = bool(user_id and user_id in self.owner_user_ids)

        return ChatMessageEvent(
            source='qq_gateway',
            channel='qq',
            user_id=user_id,
            session_id=session_id,
            text=raw_message,
            metadata={
                'adapter': 'napcat_qq',
                'message_type': message_type,
                'group_id': payload.get('group_id'),
                'self_id': self_id,
                'message_id': payload.get('message_id'),
                'sender_name': sender_name,
                'sender': sender,
                'is_owner': is_owner,
                'owner_label': self.owner_label,
                'sender_role': 'owner' if is_owner else 'contact',
                'images': images,
                'has_image': bool(images),
                'image_count': len(images),
                'image_vision_enabled': self.image_vision_enabled,
                'image_prompt': self.image_prompt,
                'filter_mode': self.filter_mode,
            },
        )

    async def send_text(self, session_id: str, text: str, **kwargs: Any) -> Any:
        text = str(text or '').strip()
        if not text:
            return {'ok': False, 'reason': 'empty_text', 'session_id': session_id}
        if not self.reply_enabled:
            return {'ok': False, 'reason': 'reply_disabled', 'session_id': session_id, 'text': text}
        action_info = self._build_send_action(session_id, text)
        if not action_info.get('ok'):
            action_info.setdefault('text', text)
            return action_info
        result = await self._send_action(session_id, action_info['action'], action_info['payload'], **kwargs)
        if isinstance(result, dict):
            result.setdefault('text', text)
        return result

    async def send_voice(self, session_id: str, voice_path: str, **kwargs: Any) -> Any:
        path_text = str(voice_path or '').strip()
        if not path_text:
            return {'ok': False, 'reason': 'empty_voice_path', 'session_id': session_id}
        if not self.reply_enabled:
            return {'ok': False, 'reason': 'reply_disabled', 'session_id': session_id, 'voice_path': path_text}

        voice_file = Path(path_text).expanduser()
        try:
            voice_file = voice_file.resolve()
        except Exception:
            voice_file = voice_file.absolute()
        if not voice_file.exists() or not voice_file.is_file():
            return {'ok': False, 'reason': 'voice_file_missing', 'session_id': session_id, 'voice_path': str(voice_file)}

        message = [{
            'type': 'record',
            'data': {
                'file': voice_file.as_uri(),
            },
        }]
        action_info = self._build_send_action(session_id, message)
        if not action_info.get('ok'):
            action_info.setdefault('voice_path', str(voice_file))
            return action_info
        result = await self._send_action(session_id, action_info['action'], action_info['payload'], **kwargs)
        if isinstance(result, dict):
            result.setdefault('voice_path', str(voice_file))
        return result

    async def send_image(self, session_id: str, image_path: str, **kwargs: Any) -> Any:
        path_text = str(image_path or '').strip()
        if not path_text:
            return {'ok': False, 'reason': 'empty_image_path', 'session_id': session_id}
        if not self.reply_enabled:
            return {'ok': False, 'reason': 'reply_disabled', 'session_id': session_id, 'image_path': path_text}

        image_file = Path(path_text).expanduser()
        try:
            image_file = image_file.resolve()
        except Exception:
            image_file = image_file.absolute()
        if not image_file.exists() or not image_file.is_file():
            return {'ok': False, 'reason': 'image_file_missing', 'session_id': session_id, 'image_path': str(image_file)}

        caption = str(kwargs.get('caption') or '').strip()
        message = [{
            'type': 'image',
            'data': {
                'file': image_file.as_uri(),
            },
        }]
        if caption:
            message.append({
                'type': 'text',
                'data': {
                    'text': caption,
                },
            })

        action_info = self._build_send_action(session_id, message)
        if not action_info.get('ok'):
            action_info.setdefault('image_path', str(image_file))
            action_info.setdefault('caption', caption)
            return action_info
        result = await self._send_action(session_id, action_info['action'], action_info['payload'], **kwargs)
        if isinstance(result, dict):
            result.setdefault('image_path', str(image_file))
            result.setdefault('caption', caption)
        return result
