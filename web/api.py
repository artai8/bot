import os
import time
import json
import logging
import asyncio
import html
from aiohttp import web
from functools import wraps
from pyrogram.types import InputMediaPhoto, InputMediaVideo
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait

from web.auth import auth_manager
from database.database import (
    get_user_count, full_userbase, get_all_stats, get_total_shares,
    get_banned_count, get_banned_users, ban_user, unban_user,
    get_user_shares, get_share, update_share, delete_share,
    get_recent_users, ping_db, del_user, search_shares,
    get_all_config, set_config, delete_config
)
from helper_func import get_messages
import config as cfg

logger = logging.getLogger(__name__)

BOT_INSTANCE = None


# ============ 静态文件路径查找 ============
def _find_static_dir():
    """多路径查找 static 目录"""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
        os.path.join(os.getcwd(), 'web', 'static'),
        '/app/web/static',
    ]
    for p in candidates:
        idx = os.path.join(p, 'index.html')
        logger.info(f"Checking static path: {p} -> dir={os.path.isdir(p)}, index={os.path.isfile(idx)}")
        if os.path.isdir(p) and os.path.isfile(idx):
            logger.info(f"✅ Found valid static dir: {p}")
            return p
    logger.error(f"❌ Static dir not found! Tried: {candidates}")
    return candidates[0]


STATIC_DIR = _find_static_dir()
INDEX_FILE = os.path.join(STATIC_DIR, 'index.html')


# ============ Bot 实例 ============
def set_bot_instance(bot):
    global BOT_INSTANCE
    BOT_INSTANCE = bot
    logger.info(f"Bot instance set: @{getattr(bot, 'username', 'unknown')}")


def _get_bot_username():
    """安全获取 bot username"""
    if BOT_INSTANCE and hasattr(BOT_INSTANCE, 'username') and BOT_INSTANCE.username:
        return BOT_INSTANCE.username
    return ""


def _parse_keywords(raw):
    if isinstance(raw, str):
        items = [x.strip() for x in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        items = []
    items = [x for x in items if x]
    seen = set()
    keywords = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        if len(x) > 32:
            x = x[:32]
        keywords.append(x)
    return keywords


def _normalize_channel_list(raw):
    normalized = []
    if isinstance(raw, list):
        for v in raw:
            if isinstance(v, int):
                normalized.append(v)
            elif isinstance(v, str) and v.lstrip("-").isdigit():
                normalized.append(int(v))
    elif isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(",", " ").split()]
        for p in parts:
            if p.lstrip("-").isdigit():
                normalized.append(int(p))
    clean = []
    seen = set()
    for v in normalized:
        if v not in seen:
            seen.add(v)
            clean.append(v)
    return clean


# ============ 认证装饰器 ============
def require_auth(handler):
    async def wrapper(request):
        token = request.headers.get('Authorization', '')
        if not auth_manager.verify_token(token):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        return await handler(request)
    return wrapper


# ============ 登录/登出 ============
async def api_login(request):
    try:
        data = await request.json()
        password = data.get('password', '')
        if auth_manager.verify_password(password):
            token = auth_manager.generate_token()
            return web.json_response({
                'success': True,
                'token': token,
                'message': 'Login successful'
            })
        return web.json_response({
            'success': False,
            'message': 'Invalid password'
        }, status=401)
    except Exception as e:
        logger.error(f"Login error: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_logout(request):
    token = request.headers.get('Authorization', '')
    auth_manager.revoke_token(token)
    return web.json_response({'success': True})


# ============ 仪表盘 ============
@require_auth
async def api_dashboard(request):
    try:
        user_count = await get_user_count()
        total_shares = await get_total_shares()
        banned_count = await get_banned_count()
        all_stats = await get_all_stats()
        recent_7d = await get_recent_users(7)
        recent_1d = await get_recent_users(1)
        db_ok = await ping_db()

        uptime = 0
        bot_username = _get_bot_username()
        if BOT_INSTANCE and hasattr(BOT_INSTANCE, 'uptime'):
            from datetime import datetime
            delta = datetime.now() - BOT_INSTANCE.uptime
            uptime = int(delta.total_seconds())

        return web.json_response({
            'users': {
                'total': user_count,
                'today': recent_1d,
                'week': recent_7d,
                'banned': banned_count
            },
            'shares': {
                'total': total_shares,
                'files_shared': all_stats.get('files_shared', 0),
                'links_generated': all_stats.get('links_generated', 0),
                'share_accessed': all_stats.get('share_accessed', 0)
            },
            'activity': {
                'tokens_verified': all_stats.get('tokens_verified', 0),
                'broadcasts': all_stats.get('broadcasts', 0)
            },
            'system': {
                'uptime': uptime,
                'database': 'connected' if db_ok else 'disconnected',
                'bot_username': bot_username
            }
        })
    except Exception as e:
        logger.error(f"Dashboard API error: {e}")
        return web.json_response({'error': str(e)}, status=500)


# ============ 用户管理 ============
@require_auth
async def api_users(request):
    try:
        page = int(request.query.get('page', 1))
        per_page = int(request.query.get('per_page', 20))
        users = await full_userbase()
        total = len(users)
        start = (page - 1) * per_page
        end = start + per_page
        paginated = users[start:end]
        return web.json_response({
            'users': paginated,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_ban_user(request):
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        reason = data.get('reason', 'Banned from WebUI')
        await ban_user(user_id, reason)
        return web.json_response({'success': True, 'message': f'User {user_id} banned'})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_unban_user(request):
    try:
        data = await request.json()
        user_id = int(data.get('user_id'))
        await unban_user(user_id)
        return web.json_response({'success': True, 'message': f'User {user_id} unbanned'})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_banned_users(request):
    try:
        users = await get_banned_users()
        result = []
        for u in users:
            result.append({
                'user_id': u['_id'],
                'reason': u.get('reason', ''),
                'banned_at': u.get('banned_at', 0)
            })
        return web.json_response({'banned_users': result, 'total': len(result)})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


# ============ 分享管理 ============
@require_auth
async def api_shares(request):
    try:
        page = int(request.query.get('page', 1))
        per_page = int(request.query.get('per_page', 20))
        search = request.query.get('search', '')

        if search:
            shares_list = await search_shares(search, limit=50)
            total = len(shares_list)
        else:
            from database.database import shares_collection
            total = await get_total_shares()
            cursor = shares_collection.find().sort('created_at', -1).skip((page - 1) * per_page).limit(per_page)
            shares_list = [doc async for doc in cursor]

        result = []
        bot_username = _get_bot_username()
        base_link = f"https://t.me/{bot_username}?start=" if bot_username else ""

        for s in shares_list:
            code = s['_id']
            result.append({
                'code': code,
                'owner_id': s.get('owner_id', 0),
                'title': s.get('title', 'Untitled'),
                'files_count': len(s.get('message_ids', [])),
                'access_count': s.get('access_count', 0),
                'protect_content': s.get('protect_content', False),
                'link': f"{base_link}{code}" if base_link else code,
                'group_text': s.get('group_text', ''),
                'keywords': s.get('keywords', []),
                'created_at': s.get('created_at', 0)
            })

        return web.json_response({
            'shares': result,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })
    except Exception as e:
        logger.error(f"API shares error: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_share_detail(request):
    try:
        code = request.match_info['code']
        share = await get_share(code)
        if not share:
            return web.json_response({'error': 'Share not found'}, status=404)

        bot_username = _get_bot_username()
        link = f"https://t.me/{bot_username}?start={code}" if bot_username else code

        return web.json_response({
            'code': share['_id'],
            'owner_id': share.get('owner_id', 0),
            'title': share.get('title', ''),
            'message_ids': share.get('message_ids', []),
            'files_count': len(share.get('message_ids', [])),
            'access_count': share.get('access_count', 0),
            'protect_content': share.get('protect_content', False),
            'link': link,
            'group_text': share.get('group_text', ''),
            'keywords': share.get('keywords', []),
            'created_at': share.get('created_at', 0),
            'updated_at': share.get('updated_at', 0)
        })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_share_update(request):
    try:
        code = request.match_info['code']
        data = await request.json()
        updates = {}
        if 'title' in data:
            updates['title'] = data['title']
        if 'protect_content' in data:
            updates['protect_content'] = data['protect_content']
        if 'keywords' in data:
            updates['keywords'] = _parse_keywords(data.get('keywords'))
        if 'group_text' in data:
            updates['group_text'] = str(data.get('group_text') or '')
        if updates:
            await update_share(code, updates)
        return web.json_response({'success': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_share_forward(request):
    try:
        code = request.match_info['code']
        data = await request.json()
        share = await get_share(code)
        if not share:
            return web.json_response({'error': 'Share not found'}, status=404)
        if not BOT_INSTANCE:
            return web.json_response({'error': 'Bot not initialized'}, status=500)

        updates = {}
        if 'keywords' in data:
            updates['keywords'] = _parse_keywords(data.get('keywords'))
        if 'group_text' in data:
            updates['group_text'] = str(data.get('group_text') or '')
        if updates:
            await update_share(code, updates)
            share.update(updates)

        db_config = await get_all_config()
        bound_channels = _normalize_channel_list(db_config.get('bound_channels', cfg.BOUND_CHANNELS))
        if not bound_channels:
            return web.json_response({'error': 'No bound channels'}, status=400)

        message_ids = share.get('message_ids', [])
        if not message_ids:
            return web.json_response({'error': 'No messages to forward'}, status=400)

        forward_all = bool(data.get('forward_all'))
        forward_indices = data.get('forward_indices')
        indices = []
        if isinstance(forward_indices, list):
            for v in forward_indices:
                try:
                    iv = int(v)
                    if 1 <= iv <= len(message_ids):
                        indices.append(iv)
                except Exception:
                    continue
        indices = sorted(set(indices))

        if forward_all:
            selected_ids = message_ids[:]
        elif indices:
            selected_ids = [message_ids[i - 1] for i in indices]
        else:
            try:
                forward_count = int(data.get('forward_count') or 0)
            except Exception:
                forward_count = 0
            if forward_count <= 0:
                forward_count = 2
            forward_count = min(max(1, forward_count), len(message_ids))
            selected_ids = message_ids[:forward_count]

        msgs = await get_messages(BOT_INSTANCE, selected_ids)
        msgs = [m for m in msgs if m and not m.empty]
        if not msgs:
            return web.json_response({'error': 'Messages not found'}, status=404)

        keywords = share.get('keywords', [])
        suffix = ""
        if keywords:
            kw = html.escape(str(keywords[0]))
            # 使用 Markdown 反引号包裹整句以确保胶囊高亮
            suffix = f"\n`在评论区输入（{kw}）查看资源`"
        group_text = share.get('group_text', '') or ''
        caption = group_text
        if suffix:
            caption = f"{caption}\n{suffix}" if caption else suffix

        can_album = len(msgs) > 1 and all((m.photo or m.video) and not m.document for m in msgs)
        successful = 0
        failed = 0

        for channel_id in bound_channels:
            try:
                if can_album:
                    media = []
                    for i, m in enumerate(msgs):
                        cap = caption if i == 0 else ""
                        if m.photo:
                            media.append(InputMediaPhoto(m.photo.file_id, caption=cap, parse_mode=ParseMode.MARKDOWN))
                        elif m.video:
                            media.append(InputMediaVideo(m.video.file_id, caption=cap, parse_mode=ParseMode.MARKDOWN))
                    await BOT_INSTANCE.send_media_group(chat_id=channel_id, media=media)
                else:
                    for i, msg in enumerate(msgs):
                        if msg.text and not msg.media:
                            text = caption if i == 0 else msg.text
                            await BOT_INSTANCE.send_message(chat_id=channel_id, text=text, parse_mode=ParseMode.MARKDOWN)
                        else:
                            cap = caption if i == 0 else ""
                            await msg.copy(chat_id=channel_id, caption=cap, parse_mode=ParseMode.MARKDOWN)
                        await asyncio.sleep(0.4)
                successful += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
                failed += 1
            except Exception:
                failed += 1

        return web.json_response({'success': True, 'successful': successful, 'failed': failed})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_share_delete(request):
    try:
        code = request.match_info['code']
        await delete_share(code)
        return web.json_response({'success': True, 'message': f'Share {code} deleted'})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


# ============ 广播 ============
@require_auth
async def api_broadcast(request):
    try:
        data = await request.json()
        message_text = data.get('message', '')
        if not message_text:
            return web.json_response({'error': 'Message is required'}, status=400)

        if not BOT_INSTANCE:
            return web.json_response({'error': 'Bot not initialized'}, status=500)

        users = await full_userbase()
        successful = 0
        failed = 0

        for user_id in users:
            try:
                await BOT_INSTANCE.send_message(chat_id=user_id, text=message_text)
                successful += 1
            except Exception:
                failed += 1

        from database.database import increment_stat
        await increment_stat('broadcasts')

        return web.json_response({
            'success': True,
            'total': len(users),
            'successful': successful,
            'failed': failed
        })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


# ============ 设置 ============
@require_auth
async def api_settings(request):
    from config import (
        IS_VERIFY, VERIFY_EXPIRE, PROTECT_CONTENT, AUTO_DELETE_TIME,
        FORCE_SUB_CHANNELS, SHOW_PROMO, PROMO_TEXT, CUSTOM_BUTTONS,
        CUSTOM_CAPTION, DISABLE_CHANNEL_BUTTON, SHARE_CODE_LENGTH,
        RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, START_MSG, FORCE_MSG,
        USER_REPLY_TEXT, ABOUT_TEXT, HELP_TEXT, ADMIN_HELP_TEXT,
        KEYWORD_BUTTON_TEXT, BOUND_CHANNELS
    )

    db_config = await get_all_config()

    return web.json_response({
        'is_verify': db_config.get('is_verify', IS_VERIFY),
        'verify_expire': db_config.get('verify_expire', VERIFY_EXPIRE),
        'protect_content': db_config.get('protect_content', PROTECT_CONTENT),
        'auto_delete_time': db_config.get('auto_delete_time', AUTO_DELETE_TIME),
        'force_sub_channels': db_config.get('force_sub_channels', FORCE_SUB_CHANNELS),
        'bound_channels': db_config.get('bound_channels', BOUND_CHANNELS),
        'show_promo': db_config.get('show_promo', SHOW_PROMO),
        'promo_text': db_config.get('promo_text', PROMO_TEXT),
        'custom_buttons': db_config.get('custom_buttons', CUSTOM_BUTTONS),
        'custom_caption': db_config.get('custom_caption', CUSTOM_CAPTION or ''),
        'disable_channel_button': db_config.get('disable_channel_button', DISABLE_CHANNEL_BUTTON),
        'share_code_length': db_config.get('share_code_length', SHARE_CODE_LENGTH),
        'rate_limit_max': db_config.get('rate_limit_max', RATE_LIMIT_MAX),
        'rate_limit_window': db_config.get('rate_limit_window', RATE_LIMIT_WINDOW),
        'start_message': db_config.get('start_message', START_MSG),
        'force_sub_message': db_config.get('force_sub_message', FORCE_MSG),
        'user_reply_text': db_config.get('user_reply_text', USER_REPLY_TEXT),
        'about_text': db_config.get('about_text', ABOUT_TEXT),
        'help_text': db_config.get('help_text', HELP_TEXT),
        'admin_help_text': db_config.get('admin_help_text', ADMIN_HELP_TEXT),
        'keyword_button_text': db_config.get('keyword_button_text', KEYWORD_BUTTON_TEXT),
    })


@require_auth
async def api_settings_update(request):
    try:
        data = await request.json()

        allowed = {
            'is_verify': bool,
            'verify_expire': int,
            'protect_content': bool,
            'auto_delete_time': int,
            'show_promo': bool,
            'promo_text': str,
            'custom_buttons': str,
            'custom_caption': str,
            'disable_channel_button': bool,
            'share_code_length': int,
            'rate_limit_max': int,
            'rate_limit_window': int,
            'start_message': str,
            'force_sub_message': str,
            'user_reply_text': str,
            'about_text': str,
            'help_text': str,
            'admin_help_text': str,
            'keyword_button_text': str,
            'bound_channels': list,
            'force_sub_channels': list,
        }

        updated = []
        for key, value in data.items():
            if key in allowed:
                expected_type = allowed[key]
                if key in ('bound_channels', 'force_sub_channels'):
                    raw = []
                    if isinstance(value, list):
                        raw = value
                    elif isinstance(value, str):
                        raw = [p.strip() for p in value.replace(",", " ").split()]
                    normalized = []
                    for v in raw:
                        try:
                            cid = int(v)
                            normalized.append(cid)
                        except Exception:
                            continue
                    clean = []
                    seen = set()
                    for v in normalized:
                        if isinstance(v, int) and v not in seen:
                            seen.add(v)
                            clean.append(v)
                    await set_config(key, clean)
                    updated.append(key)
                    continue
                if expected_type == bool:
                    value = bool(value)
                elif expected_type == int:
                    value = int(value)
                else:
                    value = str(value)

                await set_config(key, value)
                updated.append(key)

        await _apply_runtime_config()

        return web.json_response({
            'success': True,
            'updated': updated,
            'message': f'{len(updated)} settings updated'
        })
    except Exception as e:
        logger.error(f"Settings update error: {e}")
        return web.json_response({'error': str(e)}, status=500)


@require_auth
async def api_settings_reset(request):
    try:
        data = await request.json()
        key = data.get('key', '')
        if key:
            await delete_config(key)
            await _apply_runtime_config()
            return web.json_response({'success': True, 'message': f'{key} reset to default'})
        return web.json_response({'error': 'Key is required'}, status=400)
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


# ============ 热更新运行时配置 ============
async def _apply_runtime_config():
    """热更新运行时配置到 config 模块"""
    import config as cfg
    db_config = await get_all_config()

    if 'force_sub_channels' in db_config:
        try:
            val = db_config['force_sub_channels']
            if isinstance(val, list):
                cfg.FORCE_SUB_CHANNELS = [int(x) for x in val if isinstance(x, (int,)) or str(x).lstrip("-").isdigit()]
            elif isinstance(val, str):
                parts = [p.strip() for p in val.replace(",", " ").split()]
                cfg.FORCE_SUB_CHANNELS = [int(p) for p in parts if p and p.lstrip("-").isdigit()]
            else:
                cfg.FORCE_SUB_CHANNELS = []
        except Exception:
            cfg.FORCE_SUB_CHANNELS = []

    if 'is_verify' in db_config:
        cfg.IS_VERIFY = db_config['is_verify']
    if 'verify_expire' in db_config:
        cfg.VERIFY_EXPIRE = db_config['verify_expire']
    if 'protect_content' in db_config:
        cfg.PROTECT_CONTENT = db_config['protect_content']
    if 'auto_delete_time' in db_config:
        cfg.AUTO_DELETE_TIME = db_config['auto_delete_time']
    if 'show_promo' in db_config:
        cfg.SHOW_PROMO = db_config['show_promo']
    if 'promo_text' in db_config:
        cfg.PROMO_TEXT = db_config['promo_text']
    if 'custom_buttons' in db_config:
        cfg.CUSTOM_BUTTONS = db_config['custom_buttons']
    if 'custom_caption' in db_config:
        cfg.CUSTOM_CAPTION = db_config['custom_caption'] or None
    if 'disable_channel_button' in db_config:
        cfg.DISABLE_CHANNEL_BUTTON = db_config['disable_channel_button']
    if 'share_code_length' in db_config:
        cfg.SHARE_CODE_LENGTH = db_config['share_code_length']
    if 'rate_limit_max' in db_config:
        cfg.RATE_LIMIT_MAX = db_config['rate_limit_max']
    if 'rate_limit_window' in db_config:
        cfg.RATE_LIMIT_WINDOW = db_config['rate_limit_window']
    if 'start_message' in db_config:
        cfg.START_MSG = db_config['start_message']
    if 'force_sub_message' in db_config:
        cfg.FORCE_MSG = db_config['force_sub_message']
    if 'user_reply_text' in db_config:
        cfg.USER_REPLY_TEXT = db_config['user_reply_text']
    if 'about_text' in db_config:
        cfg.ABOUT_TEXT = db_config['about_text']
    if 'help_text' in db_config:
        cfg.HELP_TEXT = db_config['help_text']
    if 'admin_help_text' in db_config:
        cfg.ADMIN_HELP_TEXT = db_config['admin_help_text']
    if 'keyword_button_text' in db_config:
        cfg.KEYWORD_BUTTON_TEXT = db_config['keyword_button_text']
    if 'bound_channels' in db_config:
        try:
            val = db_config['bound_channels']
            if isinstance(val, list):
                cfg.BOUND_CHANNELS = [int(x) for x in val if isinstance(x, (int,)) or str(x).lstrip("-").isdigit()]
            elif isinstance(val, str):
                parts = [p.strip() for p in val.replace(",", " ").split()]
                cfg.BOUND_CHANNELS = [int(p) for p in parts if p and p.lstrip("-").isdigit()]
            else:
                cfg.BOUND_CHANNELS = []
        except Exception:
            cfg.BOUND_CHANNELS = []

    try:
        if 'force_sub_channels' in db_config and BOT_INSTANCE:
            inv = {}
            channels = list(getattr(cfg, 'FORCE_SUB_CHANNELS', []) or [])
            for channel_id in channels:
                try:
                    chat = await BOT_INSTANCE.get_chat(channel_id)
                    link = chat.invite_link
                    if not link:
                        await BOT_INSTANCE.export_chat_invite_link(channel_id)
                        link = (await BOT_INSTANCE.get_chat(channel_id)).invite_link
                    if link:
                        inv[channel_id] = link
                except Exception:
                    continue
            BOT_INSTANCE.invitelinks = inv
            BOT_INSTANCE.invitelink = list(inv.values())[0] if inv else None
    except Exception as e:
        logger.warning(f"Failed to refresh invite links: {e}")

    logger.info(f"Runtime config applied: {list(db_config.keys())}")


# ============ 系统健康 ============
@require_auth
async def api_system_health(request):
    import psutil
    db_ok = await ping_db()
    process = psutil.Process()
    memory = process.memory_info()

    return web.json_response({
        'database': 'connected' if db_ok else 'disconnected',
        'memory_mb': round(memory.rss / 1024 / 1024, 2),
        'cpu_percent': process.cpu_percent(),
        'threads': process.num_threads(),
        'timestamp': time.time()
    })


# ============ 路由注册 ============
def setup_api_routes(app):
    """注册所有 API 路由和静态文件服务"""
    logger.info(f"=== Setting up API routes ===")
    logger.info(f"STATIC_DIR: {STATIC_DIR} (exists={os.path.isdir(STATIC_DIR)})")
    logger.info(f"INDEX_FILE: {INDEX_FILE} (exists={os.path.isfile(INDEX_FILE)})")
    logger.info(f"CWD: {os.getcwd()}")

    try:
        if os.path.isdir(STATIC_DIR):
            for root, dirs, files in os.walk(STATIC_DIR):
                for f in files:
                    logger.info(f"  Static file: {os.path.join(root, f)}")
        else:
            web_dir = os.path.dirname(os.path.abspath(__file__))
            logger.info(f"  web/ contents: {os.listdir(web_dir) if os.path.isdir(web_dir) else 'NOT FOUND'}")
            logger.info(f"  CWD contents: {os.listdir(os.getcwd())}")
    except Exception as e:
        logger.error(f"Error listing directories: {e}")

    async def serve_admin(request):
        if os.path.isfile(INDEX_FILE):
            return web.FileResponse(INDEX_FILE)
        debug = {
            'error': 'index.html not found',
            'expected': INDEX_FILE,
            'static_dir': STATIC_DIR,
            'static_exists': os.path.isdir(STATIC_DIR),
            'cwd': os.getcwd(),
        }
        try:
            debug['cwd_contents'] = os.listdir(os.getcwd())
        except Exception:
            pass
        try:
            web_dir = os.path.dirname(os.path.abspath(__file__))
            debug['web_dir_contents'] = os.listdir(web_dir) if os.path.isdir(web_dir) else 'NOT FOUND'
        except Exception:
            pass
        logger.error(f"index.html not found: {debug}")
        return web.json_response(debug, status=404)

    app.router.add_get('/admin', serve_admin)
    app.router.add_get('/admin/', serve_admin)
    logger.info("Admin routes registered: /admin, /admin/")

    app.router.add_post('/api/login', api_login)
    app.router.add_post('/api/logout', api_logout)
    app.router.add_get('/api/dashboard', api_dashboard)
    app.router.add_get('/api/users', api_users)
    app.router.add_post('/api/ban', api_ban_user)
    app.router.add_post('/api/unban', api_unban_user)
    app.router.add_get('/api/banned', api_banned_users)
    app.router.add_get('/api/shares', api_shares)
    app.router.add_get('/api/shares/{code}', api_share_detail)
    app.router.add_put('/api/shares/{code}', api_share_update)
    app.router.add_delete('/api/shares/{code}', api_share_delete)
    app.router.add_post('/api/shares/{code}/forward', api_share_forward)
    app.router.add_post('/api/broadcast', api_broadcast)
    app.router.add_get('/api/settings', api_settings)
    app.router.add_put('/api/settings', api_settings_update)
    app.router.add_post('/api/settings/reset', api_settings_reset)
    app.router.add_get('/api/health', api_system_health)
    logger.info("API routes registered")

    if os.path.isdir(STATIC_DIR):
        app.router.add_static('/static/', path=STATIC_DIR, name='static')
        logger.info(f"Static serving: /static/ -> {STATIC_DIR}")
    else:
        logger.error(f"Cannot serve static files: {STATIC_DIR} not found")

    logger.info("=== API routes setup complete ===")

