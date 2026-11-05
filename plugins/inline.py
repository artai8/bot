from pyrogram import Client
from pyrogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent

from bot import Bot
from database.database import store


@Bot.on_inline_query()
async def inline_search(client: Client, query: InlineQuery):
    search = query.query.strip()
    if not search:
        return await query.answer(
            results=[],
            switch_pm_text="发送分享码或关键词搜索",
            switch_pm_parameter="help",
            cache_time=5,
        )
    shares = await store.search_shares(client.tenant.tenant_id, search, 20)
    results = []
    for share in shares:
        code = share["code"]
        title = share.get("title", "未命名")
        link = f"https://t.me/{client.username}?start={code}"
        results.append(
            InlineQueryResultArticle(
                title=title,
                description=f"📁 {len(share.get('message_ids', []))} 个文件 | 👁 {share.get('access_count', 0)} 次查看 | {code}",
                input_message_content=InputTextMessageContent(
                    f"📦 <b>{title}</b>\n\n📌 分享码：<code>{code}</code>\n\n👉 <a href='{link}'>点击获取文件</a>"
                ),
            )
        )
    await query.answer(results=results, cache_time=10)
