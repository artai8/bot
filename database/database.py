import time
import logging
import certifi
import motor.motor_asyncio
from config import DB_URI, DB_NAME

logger = logging.getLogger(__name__)

dbclient = motor.motor_asyncio.AsyncIOMotorClient(
    DB_URI,
    maxPoolSize=50,
    minPoolSize=10,
    connectTimeoutMS=10000,
    retryWrites=True,
    retryReads=True,
    serverSelectionTimeoutMS=5000,
    tls=True,
    tlsCAFile=certifi.where()
)

database = dbclient[DB_NAME]

# ============ 用户集合 ============
user_data = database['users']

default_verify = {
    'is_verified': False,
    'verified_time': 0,
    'verify_token': "",
    'link': ""
}


def new_user(id):
    return {
        '_id': id,
        'verify_status': {
            'is_verified': False,
            'verified_time': "",
            'verify_token': "",
            'link': ""
        },
        'joined_at': time.time()
    }


async def present_user(user_id: int):
    found = await user_data.find_one({'_id': user_id})
    return bool(found)


async def add_user(user_id: int):
    user = new_user(user_id)
    await user_data.insert_one(user)


async def db_verify_status(user_id):
    user = await user_data.find_one({'_id': user_id})
    if user:
        return user.get('verify_status', default_verify)
    return default_verify


async def db_update_verify_status(user_id, verify):
    await user_data.update_one({'_id': user_id}, {'$set': {'verify_status': verify}})


async def full_userbase():
    user_docs = user_data.find()
    user_ids = [doc['_id'] async for doc in user_docs]
    return user_ids


async def del_user(user_id: int):
    await user_data.delete_one({'_id': user_id})


async def get_user_count():
    return await user_data.count_documents({})


async def get_recent_users(days=7):
    cutoff = time.time() - (days * 86400)
    count = await user_data.count_documents({'joined_at': {'$gte': cutoff}})
    return count


# ============ 分享集合 ============
shares_collection = database['shares']


async def create_share(share_code: str, owner_id: int, message_ids: list,
                       title: str = "", protect_content: bool = False, group_text: str = "", keywords=None):
    if keywords is None:
        keywords = []
    share = {
        '_id': share_code,
        'owner_id': owner_id,
        'message_ids': message_ids,
        'title': title,
        'group_text': group_text,
        'keywords': keywords,
        'protect_content': protect_content,
        'access_count': 0,
        'created_at': time.time(),
        'updated_at': time.time()
    }
    await shares_collection.insert_one(share)
    return share


async def get_share(share_code: str):
    return await shares_collection.find_one({'_id': share_code})


async def find_share_by_message_id(message_id: int):
    return await shares_collection.find_one({'message_ids': message_id})

async def find_share_by_group_text(group_text: str, owner_id: int = None):
    query = {'group_text': group_text}
    if owner_id is not None:
        query['owner_id'] = owner_id
    return await shares_collection.find_one(query)

async def find_share_by_keyword(keyword: str):
    cursor = shares_collection.find({'keywords': keyword}).sort('created_at', -1).limit(1)
    docs = [doc async for doc in cursor]
    return docs[0] if docs else None

async def find_shares_by_keyword(keyword: str, limit: int = 6):
    cursor = shares_collection.find({'keywords': keyword}).sort('created_at', -1).limit(limit)
    return [doc async for doc in cursor]


async def increment_share_access(share_code: str):
    await shares_collection.update_one(
        {'_id': share_code},
        {'$inc': {'access_count': 1}}
    )


async def get_user_shares(owner_id: int, page: int = 1, per_page: int = 10):
    skip = (page - 1) * per_page
    cursor = shares_collection.find({'owner_id': owner_id}).sort('created_at', -1).skip(skip).limit(per_page)
    shares = [doc async for doc in cursor]
    total = await shares_collection.count_documents({'owner_id': owner_id})
    return shares, total


async def update_share(share_code: str, updates: dict):
    updates['updated_at'] = time.time()
    await shares_collection.update_one(
        {'_id': share_code},
        {'$set': updates}
    )


async def delete_share(share_code: str):
    await shares_collection.delete_one({'_id': share_code})


async def get_total_shares():
    return await shares_collection.count_documents({})


async def search_shares(query: str, limit: int = 10):
    cursor = shares_collection.find(
        {'title': {'$regex': query, '$options': 'i'}}
    ).limit(limit)
    return [doc async for doc in cursor]


async def get_user_share_count(owner_id: int):
    return await shares_collection.count_documents({'owner_id': owner_id})


# ============ 封禁集合 ============
banned_users = database['banned_users']


async def ban_user(user_id: int, reason: str = ""):
    await banned_users.update_one(
        {'_id': user_id},
        {'$set': {'reason': reason, 'banned_at': time.time()}},
        upsert=True
    )


async def unban_user(user_id: int):
    await banned_users.delete_one({'_id': user_id})


async def is_banned(user_id: int):
    return await banned_users.find_one({'_id': user_id})


async def get_banned_users():
    docs = banned_users.find()
    return [doc async for doc in docs]


async def get_banned_count():
    return await banned_users.count_documents({})


# ============ 统计集合 ============
bot_stats = database['bot_stats']


async def increment_stat(key: str, value: int = 1):
    await bot_stats.update_one(
        {'_id': key},
        {'$inc': {'count': value}},
        upsert=True
    )


async def get_stat(key: str) -> int:
    doc = await bot_stats.find_one({'_id': key})
    return doc['count'] if doc else 0


async def get_all_stats():
    docs = bot_stats.find()
    stats = {}
    async for doc in docs:
        stats[doc['_id']] = doc['count']
    return stats


# ============ 数据库健康检查 ============
async def ping_db():
    try:
        await dbclient.admin.command('ping')
        return True
    except Exception as e:
        logger.error(f"Database ping failed: {e}")
        return False


# ============ 索引创建 ============
async def create_indexes():
    try:
        await shares_collection.create_index('owner_id')
        await shares_collection.create_index('created_at')
        await shares_collection.create_index('message_ids')
        await shares_collection.create_index([('title', 'text')])
        logger.info("Database indexes created successfully")
    except Exception as e:
        logger.error(f"Error creating indexes: {e}")


# ============ 动态配置集合 ============
config_collection = database['bot_config']


async def get_config(key: str, default=None):
    doc = await config_collection.find_one({'_id': key})
    if doc:
        return doc.get('value', default)
    return default


async def set_config(key: str, value):
    await config_collection.update_one(
        {'_id': key},
        {'$set': {'value': value, 'updated_at': time.time()}},
        upsert=True
    )


async def get_all_config():
    docs = config_collection.find()
    config = {}
    async for doc in docs:
        config[doc['_id']] = doc.get('value')
    return config


async def delete_config(key: str):
    await config_collection.delete_one({'_id': key})
