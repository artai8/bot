import logging
from pyrogram import Client
from pyrogram.types import (
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent
)

from bot import Bot
from helper_func import encode
from database.database import search_shares

logger = logging.getLogger(__name__)


@Bot.on_inline_query()
async def inline_search(client: Client, query: InlineQuery):
    search_text = query.query.strip()

    if not search_text:
        await query.answer(
            results=[],
            switch_pm_text="发送分享码或关键词搜索",
            switch_pm_parameter="help",
            cache_time=5
        )
        return

    # 搜索分享内容
    results = await search_shares(search_text, limit=20)

    if not results:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    title="未找到结果",
                    description=f"没有匹配「{search_text}」的分享",
                    input_message_content=InputTextMessageContent(
                        f"未找到匹配的分享：{search_text}"
                    )
                )
            ],
            cache_time=5
        )
        return

    inline_results = []
    for idx, share in enumerate(results):
        code = share['_id']
        title = share.get('title', '未命名')
        files = len(share.get('message_ids', []))
        views = share.get('access_count', 0)

        link = f"https://t.me/{client.username}?start={code}"

        inline_results.append(
            InlineQueryResultArticle(
                title=title,
                description=f"📁 {files} 个文件 | 👁 {views} 次查看 | 分享码: {code}",
                input_message_content=InputTextMessageContent(
                    f"📦 <b>{title}</b>\n\n"
                    f"📁 文件数：{files}\n"
                    f"📌 分享码：<code>{code}</code>\n\n"
                    f"👉 <a href='{link}'>点击获取文件</a>"
                ),
                thumb_url="https://img.icons8.com/color/48/000000/folder-invoices.png"
            )
        )

    await query.answer(results=inline_results, cache_time=10)
