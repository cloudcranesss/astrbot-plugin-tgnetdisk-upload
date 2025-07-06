import asyncio
import os
import re
import tempfile
import aiofiles
import aiohttp
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.star.filter.event_message_type import EventMessageType


@register("TG_NETWORK_DISK Uploader", "cloudcranesss", "TG_NETWORK_DISK Uploader", "v1.0.0", "")
class AstrBot(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.session = aiohttp.ClientSession()  # 初始化时创建session
        self.file_name = "demo.zip"
        self.config = config
        self.url = self.config.get("TG_NETWORK_DISK", "")
        self.temp_dir = tempfile.gettempdir()
        self.waiting_for_file: dict[str, bool] = {}
        self.timeout_tasks: dict[str, asyncio.Task] = {}
        logger.info(f"TG_NETWORK_DISK: {self.url}")

    async def close(self):
        """关闭session"""
        if self.session:
            await self.session.close()

    @filter.regex(r"^tg(.+)")
    async def start_command(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        self.file_name = await self._get_keyword("tg", event.get_messages())
        if user_id in self.waiting_for_file:
            yield event.plain_result("请勿重复上传")
            return

        self.waiting_for_file[user_id] = True
        yield event.plain_result("请发送文件")

        async def timeout_task(user_id):
            await asyncio.sleep(60)
            if user_id in self.waiting_for_file:
                del self.waiting_for_file[user_id]
                if user_id in self.timeout_tasks:
                    del self.timeout_tasks[user_id]
                logger.info(f"用户 {user_id} 文件上传超时")
        task = asyncio.create_task(timeout_task(user_id))
        self.timeout_tasks[user_id] = task

    @filter.event_message_type(EventMessageType.ALL)
    async def upload(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()

        if user_id in self.waiting_for_file and event.get_message_outline().strip().lower() == "q":
            del self.waiting_for_file[user_id]
            if user_id in self.timeout_tasks:
                task = self.timeout_tasks[user_id]
                task.cancel()
                del self.timeout_tasks[user_id]
            yield event.plain_result("取消上传")
            return

        if user_id not in self.waiting_for_file:
            return

        has_file = False
        for message in event.get_messages():
            if message.type == "File":
                has_file = True
                break

        if not has_file:
            return

        del self.waiting_for_file[user_id]
        if user_id in self.timeout_tasks:
            task = self.timeout_tasks[user_id]
            task.cancel()
            del self.timeout_tasks[user_id]

        message_chain = event.get_messages()
        logger.info(f"用户 {user_id} 提交了文件上传")
        logger.info(message_chain)

        file_url = None
        file_name = None  # 存储原始文件名

        for msg in message_chain:
            if getattr(msg, 'type', '') == 'File':
                try:
                    if hasattr(msg, 'url') and msg.url:
                        file_url = msg.url
                    if hasattr(msg, 'name') and msg.name:
                        file_name = msg.name
                except Exception as e:
                    logger.error(e)
                    logger.error(f"文件处理失败: {str(e)}")

        if not file_url:
            yield event.plain_result("❌ 文件解析失败，请重试")
            return

        # 使用原始文件名（如果存在）
        if file_name:
            self.file_name = file_name

        try:
            yield event.plain_result("开始处理文件...")
            if file_url.startswith("http"):
                file_path = await self.download_file(file_url)
                if not file_path:
                    yield event.plain_result("文件下载失败")
                    return

                result = await self.upload_file(file_path)
                if result:
                    # 假设返回结果中有下载链接
                    download_url = result.get("url", "上传成功")
                    yield event.plain_result(f"✅ 上传成功\n🔗 {download_url}")
                else:
                    yield event.plain_result("❌ 上传失败")

                # 清理临时文件
                if os.path.exists(file_path):
                    os.remove(file_path)

        except Exception as e:
            logger.error(e)
            yield event.plain_result(f"❌ 文件处理失败: {str(e)}")

    async def _get_keyword(self, key, messages):
        r1 = str(messages[0])
        r2 = re.findall(r"text='(.*?)'", r1)[0]
        keyword = r2.split(key)[1]
        logger.info(f"搜索关键词: {keyword}")
        return keyword

    async def download_file(self, url):
        """下载文件"""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    logger.error(f"下载文件失败: HTTP {response.status}")
                    return None

                file_path = os.path.join(self.temp_dir, self.file_name)

                async with aiofiles.open(file_path, "wb") as f:
                    chunk_size = 1024 * 1024  # 1MB chunks
                    while True:
                        chunk = await response.content.read(chunk_size)
                        if not chunk:
                            break
                        await f.write(chunk)

                return file_path

        except Exception as e:
            logger.error(f"下载文件失败：{e}")
            return None

    async def upload_file(self, file_path):
        """上传文件 - 修复了files参数问题"""
        if not self.url:
            logger.error("未配置TG_NETWORK_DISK地址")
            return None

        upload_url = self.url + "/api"
        try:
            # 使用FormData正确上传文件
            data = aiohttp.FormData()
            data.add_field(
                'image',
                open(file_path, 'rb')
            )

            async with self.session.post(upload_url, data=data) as response:
                if response.status != 200:
                    error_msg = await response.text()
                    logger.error(f"上传文件错误: {error_msg}")
                    return None

                return await response.json()

        except Exception as e:
            logger.error(f"上传文件失败：{e}")
            return None