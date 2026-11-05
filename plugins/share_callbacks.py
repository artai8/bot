from __future__ import annotations

from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot import Bot
from database.database import store
from helper_func import generate_share_code
from plugins.share import (
    build_share_page_buttons,
    render_share_list,
    send_share_page,
)


def _back_buttons():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ 返回", callback_data="home"),
                InlineKeyboardButton("❌ 关闭", callback_data="close"),
            ]
        ]
    )


@Bot.on_callback_query(group=2)
async def share_callback_handler(client: Client, query: CallbackQuery):
    data = query.data or ""
    user_id = query.from_user.id
    tenant_id = client.tenant.tenant_id
    sessions = client.share_sessions

    if data == "share_toggle_protect":
        session = sessions.get(user_id)
        if not session:
            return await query.answer("❌ 没有进行中的会话", show_alert=True)
        session["protect"] = not session["protect"]
        status = "开启" if session["protect"] else "关闭"
        await query.message.edit_reply_markup(
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"🚫 禁止转发：{status}", callback_data="share_toggle_protect"
                        )
                    ],
                    [InlineKeyboardButton("✅ 完成分享", callback_data="share_complete")],
                    [InlineKeyboardButton("❌ 取消", callback_data="share_cancel")],
                ]
            )
        )
        return await query.answer(f"转发保护：{status}")

    if data == "share_complete":
        session = sessions.get(user_id)
        if not session:
            return await query.answer("❌ 没有进行中的会话", show_alert=True)
        if not session["messages"]:
            return await query.answer("⚠️ 还未添加任何文件！", show_alert=True)
        requested = generate_share_code(int(client.tenant.setting("share_code_length", 8)))
        share = await store.create_share(
            tenant_id,
            requested,
            user_id,
            session["messages"],
            title=session["title"] or f"分享-{requested}",
            protect_content=session["protect"],
            code_length=int(client.tenant.setting("share_code_length", 8)),
        )
        code = share["code"]
        await store.increment_stat(tenant_id, "links_generated")
        sessions.pop(user_id, None)
        link = f"https://t.me/{client.username}?start={code}"
        await query.message.edit_text(
            f"✅ <b>分享创建成功！</b>\n\n📌 <b>分享码：</b><code>{code}</code>\n"
            f"📁 <b>文件数：</b>{len(session['messages'])}\n🔗 <b>链接：</b>{link}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔁 分享给好友", url=f"https://telegram.me/share/url?url={link}"
                        )
                    ],
                    [InlineKeyboardButton("📋 我的分享列表", callback_data="my_shares_1")],
                ]
            ),
        )
        return

    if data == "share_cancel":
        sessions.pop(user_id, None)
        return await query.message.edit_text("❌ 分享已取消。")

    if data.startswith("share_page_"):
        _, _, code, page_text = data.split("_", 3)
        share = await store.get_share(tenant_id, code)
        if not share:
            return await query.answer("❌ 分享不存在！", show_alert=True)
        sent, page, total_pages = await send_share_page(client, user_id, share, int(page_text), 10)
        if not sent:
            return await query.answer("❌ 无可发送的文件。", show_alert=True)
        await query.message.edit_text(
            f"📄 第 {page}/{total_pages} 页",
            reply_markup=build_share_page_buttons(code, page, total_pages),
        )
        return await query.answer()

    if data.startswith("share_detail_"):
        code = data.removeprefix("share_detail_")
        share = await store.get_share(tenant_id, code)
        if not share:
            return await query.answer("❌ 分享不存在！", show_alert=True)
        await query.message.edit_text(
            f"📄 <b>分享详情</b>\n\n📌 分享码：<code>{code}</code>\n"
            f"📝 标题：{share.get('title', '未命名')}\n"
            f"📁 文件数：{len(share.get('message_ids', []))}\n"
            f"👁 查看次数：{share.get('access_count', 0)}",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🚫 切换转发保护", callback_data=f"share_toggle_{code}")],
                    [
                        InlineKeyboardButton(
                            "🗑 删除", callback_data=f"share_confirm_delete_{code}"
                        ),
                        InlineKeyboardButton("⬅️ 返回", callback_data="my_shares_1"),
                    ],
                ]
            ),
        )
        return

    if data.startswith("share_toggle_"):
        code = data.removeprefix("share_toggle_")
        share = await store.get_share(tenant_id, code)
        if not share or (share["owner_id"] != user_id and not client.tenant.is_admin(user_id)):
            return await query.answer("❌ 无权操作！", show_alert=True)
        value = not share.get("protect_content", False)
        await store.update_share(tenant_id, code, {"protect_content": value})
        await query.answer(f"转发保护：{'开启' if value else '关闭'}")
        return

    if data.startswith("share_confirm_delete_") or data.startswith("share_delete_"):
        code = data.split("delete_", 1)[1]
        return await query.message.edit_text(
            f"⚠️ 确定要删除分享 <code>{code}</code> 吗？",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("⚠️ 确认删除", callback_data=f"share_do_delete_{code}"),
                        InlineKeyboardButton("取消", callback_data="my_shares_1"),
                    ]
                ]
            ),
        )

    if data.startswith("share_do_delete_"):
        code = data.removeprefix("share_do_delete_")
        share = await store.get_share(tenant_id, code)
        if not share or (share["owner_id"] != user_id and not client.tenant.is_admin(user_id)):
            return await query.answer("❌ 无权操作！", show_alert=True)
        await store.delete_share(tenant_id, code)
        return await query.message.edit_text(f"✅ 分享 <code>{code}</code> 已删除。")

    if data.startswith("my_shares_") or data.startswith("shares_page_"):
        text, markup = await render_share_list(client, user_id, int(data.split("_")[-1]))
        return await query.message.edit_text(text, reply_markup=markup)

    if data == "about":
        return await query.message.edit_text(
            str(client.tenant.setting("about_text")).format(owner_id=client.tenant.owner_id),
            disable_web_page_preview=True,
            reply_markup=_back_buttons(),
        )
    if data == "help":
        key = "admin_help_text" if client.tenant.is_admin(user_id) else "help_text"
        return await query.message.edit_text(
            str(client.tenant.setting(key)), reply_markup=_back_buttons()
        )
    if data == "home":
        user = query.from_user
        return await query.message.edit_text(
            str(client.tenant.setting("start_message")).format(
                first=user.first_name,
                last=user.last_name,
                username=None if not user.username else "@" + user.username,
                mention=user.mention,
                id=user.id,
            ),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("ℹ️ 关于我", callback_data="about"),
                        InlineKeyboardButton("📖 使用帮助", callback_data="help"),
                    ],
                    [InlineKeyboardButton("❌ 关闭", callback_data="close")],
                ]
            ),
        )
    if data == "close":
        await query.message.delete()
