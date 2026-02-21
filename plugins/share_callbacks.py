import logging
from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot import Bot
from config import ADMINS
from helper_func import generate_share_code
from database.database import (
    create_share, get_share, update_share, delete_share,
    get_user_shares, increment_stat
)
from plugins.share import user_share_sessions

logger = logging.getLogger(__name__)


@Bot.on_callback_query(group=2)
async def share_callback_handler(client: Client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    # ========== 分享流程回调 ==========
    if data == "share_toggle_protect":
        if user_id not in user_share_sessions:
            return await query.answer("❌ 没有进行中的会话", show_alert=True)

        session = user_share_sessions[user_id]
        session['protect'] = not session['protect']
        status = "开启" if session['protect'] else "关闭"

        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🚫 禁止转发：{status}",
                                  callback_data="share_toggle_protect")],
            [InlineKeyboardButton("✅ 完成分享", callback_data="share_complete")],
            [InlineKeyboardButton("❌ 取消", callback_data="share_cancel")]
        ])
        await query.message.edit_reply_markup(btn)
        await query.answer(f"转发保护：{status}")

    elif data == "share_complete":
        if user_id not in user_share_sessions:
            return await query.answer("❌ 没有进行中的会话", show_alert=True)

        session = user_share_sessions[user_id]
        if not session['messages']:
            return await query.answer("⚠️ 还未添加任何文件！请先发送文件。", show_alert=True)

        # 生成分享码
        share_code = generate_share_code()
        while await get_share(share_code):
            share_code = generate_share_code()

        await create_share(
            share_code=share_code,
            owner_id=user_id,
            message_ids=session['messages'],
            title=session['title'] or f"分享-{share_code}",
            protect_content=session['protect'],
            group_text=""
        )

        await increment_stat('links_generated')
        del user_share_sessions[user_id]

        share_link = f"https://t.me/{client.username}?start={share_code}"
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔁 分享给好友", url=f'https://telegram.me/share/url?url={share_link}')],
            [InlineKeyboardButton("📋 我的分享列表", callback_data="my_shares_1")]
        ])

        await query.message.edit_text(
            f"✅ <b>分享创建成功！</b>\n\n"
            f"📌 <b>分享码：</b><code>{share_code}</code>\n"
            f"📁 <b>文件数：</b>{len(session['messages'])}\n"
            f"🔒 <b>禁止转发：</b>{'是' if session['protect'] else '否'}\n"
            f"📝 <b>标题：</b>{session.get('title', '未命名')}\n\n"
            f"🔗 <b>链接：</b>{share_link}\n\n"
            f"其他用户发送 <code>{share_code}</code> 即可获取文件。",
            reply_markup=btn
        )

    elif data == "share_cancel":
        if user_id in user_share_sessions:
            del user_share_sessions[user_id]
        await query.message.edit_text("❌ 分享已取消。")

    # ========== 分享管理回调 ==========
    elif data.startswith("share_detail_"):
        code = data.replace("share_detail_", "")
        share = await get_share(code)
        if not share:
            return await query.answer("❌ 分享不存在！", show_alert=True)

        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🚫 转发保护：{'开启' if share.get('protect_content') else '关闭'}",
                callback_data=f"share_toggle_{code}"
            )],
            [InlineKeyboardButton("🗑 删除", callback_data=f"share_confirm_delete_{code}"),
             InlineKeyboardButton("⬅️ 返回", callback_data="my_shares_1")]
        ])

        await query.message.edit_text(
            f"📄 <b>分享详情</b>\n\n"
            f"📌 分享码：<code>{code}</code>\n"
            f"📝 标题：{share.get('title', '未命名')}\n"
            f"📁 文件数：{len(share.get('message_ids', []))}\n"
            f"👁 查看次数：{share.get('access_count', 0)}\n"
            f"🔒 禁止转发：{'是' if share.get('protect_content') else '否'}\n"
            f"📅 创建时间：{share.get('created_at', '未知')}",
            reply_markup=btn
        )

    elif data.startswith("share_toggle_"):
        code = data.replace("share_toggle_", "")
        share = await get_share(code)
        if not share or (share['owner_id'] != user_id and user_id not in ADMINS):
            return await query.answer("❌ 无权操作！", show_alert=True)

        new_protect = not share.get('protect_content', False)
        await update_share(code, {'protect_content': new_protect})
        await query.answer(f"转发保护：{'开启' if new_protect else '关闭'}")

        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🚫 转发保护：{'开启' if new_protect else '关闭'}",
                callback_data=f"share_toggle_{code}"
            )],
            [InlineKeyboardButton("🗑 删除", callback_data=f"share_confirm_delete_{code}"),
             InlineKeyboardButton("⬅️ 返回", callback_data="my_shares_1")]
        ])
        await query.message.edit_reply_markup(btn)

    elif data.startswith("share_confirm_delete_"):
        code = data.replace("share_confirm_delete_", "")
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ 确认删除", callback_data=f"share_do_delete_{code}"),
             InlineKeyboardButton("取消", callback_data=f"share_detail_{code}")]
        ])
        await query.message.edit_text(
            f"⚠️ 确定要删除分享 <code>{code}</code> 吗？\n"
            f"此操作不可撤销！",
            reply_markup=btn
        )

    elif data.startswith("share_do_delete_"):
        code = data.replace("share_do_delete_", "")
        share = await get_share(code)
        if share and (share['owner_id'] == user_id or user_id in ADMINS):
            await delete_share(code)
            await query.message.edit_text(f"✅ 分享 <code>{code}</code> 已删除。")
        else:
            await query.answer("❌ 无权操作！", show_alert=True)

    elif data.startswith("share_delete_"):
        code = data.replace("share_delete_", "")
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ 确认删除", callback_data=f"share_do_delete_{code}"),
             InlineKeyboardButton("取消", callback_data="my_shares_1")]
        ])
        await query.message.edit_text(
            f"⚠️ 删除分享 <code>{code}</code>？", reply_markup=btn
        )

    # ========== 分享列表分页 ==========
    elif data.startswith("my_shares_") or data.startswith("shares_page_"):
        page = int(data.split("_")[-1])
        shares, total = await get_user_shares(user_id, page=page, per_page=5)

        if not shares:
            return await query.message.edit_text("📭 没有找到任何分享。")

        text = f"📋 <b>我的分享</b>（第 {page} 页，共 {total} 个）\n\n"
        buttons = []
        for share in shares:
            code = share['_id']
            title = share.get('title', '未命名')
            access = share.get('access_count', 0)
            protect = "🔒" if share.get('protect_content') else "🔓"
            files = len(share.get('message_ids', []))
            text += f"{protect} <code>{code}</code> - {title}\n"
            text += f"   📁 {files} 个文件 | 👁 {access} 次查看\n\n"
            buttons.append([
                InlineKeyboardButton(f"📄 {code}", callback_data=f"share_detail_{code}"),
                InlineKeyboardButton("🗑", callback_data=f"share_delete_{code}")
            ])

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"shares_page_{page - 1}"))
        total_pages = (total + 4) // 5
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"shares_page_{page + 1}"))
        if nav_buttons:
            buttons.append(nav_buttons)

        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    # ========== 原有回调 ==========
    elif data == "about":
        from pyrogram import __version__
        from config import OWNER_ID, ABOUT_TEXT
        await query.message.edit_text(
            text=ABOUT_TEXT.format(owner_id=OWNER_ID, pyrogram_version=__version__),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回", callback_data="home"),
                 InlineKeyboardButton("❌ 关闭", callback_data="close")]
            ])
        )

    elif data == "help":
        from config import HELP_TEXT, ADMIN_HELP_TEXT
        if user_id in ADMINS:
            text = ADMIN_HELP_TEXT
        else:
            text = HELP_TEXT
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ 返回", callback_data="home"),
                 InlineKeyboardButton("❌ 关闭", callback_data="close")]
            ])
        )

    elif data == "home":
        from config import START_MSG
        await query.message.edit_text(
            text=START_MSG.format(
                first=query.from_user.first_name,
                last=query.from_user.last_name,
                username=None if not query.from_user.username else '@' + query.from_user.username,
                mention=query.from_user.mention,
                id=query.from_user.id
            ),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("ℹ️ 关于我", callback_data="about"),
                 InlineKeyboardButton("📖 使用帮助", callback_data="help")],
                [InlineKeyboardButton("❌ 关闭", callback_data="close")]
            ])
        )

    elif data == "close":
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except Exception:
            pass
