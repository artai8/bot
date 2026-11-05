from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import Bot
from database.database import store
from delivery import DeliveryQueueFull, copy_share_to_user, delete_later
from helper_func import admin_only, get_exp_time, not_banned, subscribed

logger = logging.getLogger(__name__)


@Bot.on_message(filters.command("share") & filters.private & admin_only & not_banned, group=2)
async def start_share(client: Client, message: Message):
    client.share_sessions[message.from_user.id] = {
        "messages": [],
        "protect": False,
        "title": "",
    }
    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚫 禁止转发：关闭", callback_data="share_toggle_protect")],
            [InlineKeyboardButton("✅ 完成分享", callback_data="share_complete")],
            [InlineKeyboardButton("❌ 取消", callback_data="share_cancel")],
        ]
    )
    await message.reply(
        "📤 <b>分享模式已开启！</b>\n\n"
        "请逐一发送文件，完成后点击 <b>完成分享</b>。\n"
        "可发送 <code>/title 标题内容</code> 设置标题。",
        reply_markup=buttons,
        quote=True,
    )


@Bot.on_message(filters.command("title") & filters.private & admin_only, group=2)
async def set_share_title(client: Client, message: Message):
    session = client.share_sessions.get(message.from_user.id)
    if not session:
        return await message.reply("❌ 当前没有进行中的分享会话，请先使用 /share。", quote=True)
    title = " ".join(message.command[1:]).strip()
    if not title:
        return await message.reply("📝 用法：<code>/title 你的标题</code>", quote=True)
    session["title"] = title[:500]
    await message.reply(f"📝 标题已设置为：<b>{session['title']}</b>", quote=True)


@Bot.on_message(filters.command("myshares") & filters.private & subscribed & not_banned, group=2)
async def my_shares(client: Client, message: Message):
    try:
        page = max(1, int(message.command[1])) if len(message.command) > 1 else 1
    except ValueError:
        page = 1
    text, markup = await render_share_list(client, message.from_user.id, page)
    await message.reply(text, reply_markup=markup, quote=True)


async def render_share_list(client: Client, user_id: int, page: int):
    shares, total = await store.get_user_shares(
        client.tenant.tenant_id, user_id, page=page, per_page=5
    )
    if not shares:
        return "📭 您还没有任何分享。", None
    text = f"📋 <b>我的分享</b>（第 {page} 页，共 {total} 个）\n\n"
    buttons = []
    for share in shares:
        code = share["code"]
        protect = "🔒" if share.get("protect_content") else "🔓"
        text += (
            f"{protect} <code>{code}</code> - {share.get('title', '未命名')}\n"
            f"   📁 {len(share.get('message_ids', []))} 个文件 | "
            f"👁 {share.get('access_count', 0)} 次查看\n\n"
        )
        buttons.append(
            [
                InlineKeyboardButton(f"📄 {code}", callback_data=f"share_detail_{code}"),
                InlineKeyboardButton("🗑", callback_data=f"share_delete_{code}"),
            ]
        )
    navigation = []
    if page > 1:
        navigation.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"shares_page_{page - 1}"))
    if page < (total + 4) // 5:
        navigation.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"shares_page_{page + 1}"))
    if navigation:
        buttons.append(navigation)
    return text, InlineKeyboardMarkup(buttons)


async def handle_share_code(client: Client, message: Message, code: str) -> bool:
    tenant_id = client.tenant.tenant_id
    share = await store.get_share(tenant_id, code)
    if not share:
        return False
    await store.increment_share_access(tenant_id, code)
    await store.increment_stat(tenant_id, "share_accessed")
    message_ids = share.get("message_ids") or []
    if not message_ids:
        await message.reply("❌ 此分享没有包含任何文件。", quote=True)
        return True
    await message.reply(f"📦 此分享包含 <b>{len(message_ids)}</b> 个文件", quote=True)
    try:
        sent, page, total_pages = await send_share_page(client, message.from_user.id, share, 1, 10)
    except DeliveryQueueFull:
        await message.reply("⏳ 当前取件请求较多，请稍后重试。", quote=True)
        return True
    navigation = None
    if total_pages > 1:
        navigation = await message.reply(
            f"📄 第 {page}/{total_pages} 页",
            reply_markup=build_share_page_buttons(code, page, total_pages),
            quote=True,
        )
    auto_delete = int(client.tenant.setting("auto_delete_time", 0))
    if auto_delete > 0 and sent:
        notification = await message.reply(
            str(client.tenant.setting("auto_delete_message")).format(
                time=get_exp_time(auto_delete)
            ),
            quote=True,
        )
        targets = [*sent, *([navigation] if navigation else [])]
        client.create_background_task(delete_later(targets, notification, auto_delete))
    await store.increment_stat(tenant_id, "files_shared", len(sent))
    return True


def build_share_page_buttons(code: str, page: int, total_pages: int):
    buttons = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton("⬅️ 上一页", callback_data=f"share_page_{code}_{page - 1}")
        )
    if page < total_pages:
        buttons.append(
            InlineKeyboardButton("下一页 ➡️", callback_data=f"share_page_{code}_{page + 1}")
        )
    return InlineKeyboardMarkup([buttons]) if buttons else None


async def send_share_page(client: Client, chat_id: int, share: dict, page: int, per_page: int = 10):
    message_ids = share.get("message_ids") or []
    total_pages = (len(message_ids) + per_page - 1) // per_page
    if not total_pages:
        return [], 1, 0
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    selected = message_ids[start : start + per_page]
    sent = await copy_share_to_user(
        client,
        chat_id,
        selected,
        protect_content=bool(
            share.get("protect_content", client.tenant.setting("protect_content", False))
        ),
    )
    return sent, page, total_pages
