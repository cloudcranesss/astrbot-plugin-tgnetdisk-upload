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
        self.session = None
        self.file_name = "demo.zip"
        self.config = config
        self.url = self.config.get("TG_NETWORK_DISK", "")
        self.temp_dir = tempfile.gettempdir()
        self.waiting_for_file: dict[str, bool] = {}
        self.timeout_tasks: dict[str, asyncio.Task] = {}
        logger.info(f"TG_NETWORK_DISK: {self.url}")

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
                    yield event.plain_result("文件上传超时，请重新发送文件")
                    logger.info(f"用户 {user_id} 文件上传超时")
                yield event.plain_result("文件上传超时，请重新发送文件")

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

        for msg in message_chain:
            if getattr(msg, 'type', '') == 'File':
                try:
                    if hasattr(msg, 'url') and msg.url:
                        file_url = msg.url
                except Exception as e:
                    logger.error(e)
                    logger.error(f"文件处理失败: {str(e)}")

        if not file_url:
            yield event.plain_result("❌ 文件解析失败，请重试")
            return

        try:
            yield event.plain_result("开始处理文件...")
            if file_url.startswith("http"):
                file_path = await self.download_file(file_url)
                result = await self.upload_file(file_path)
                yield event.plain_result(result)

        except Exception as e:
            logger.error(e)
            yield event.plain_result(f"图片处理失败: {str(e)}")
            return

    async def _get_keyword(self, key, messages):
        r1 = str (messages[0])
        r2 = re.findall(r"text='(.*?)'", r1)[0]
        keyword = r2.split(key)[1]
        logger.info(f"搜索关键词: {keyword}")
        return keyword

    async def download_file(self, url):
        """
        下载文件
        """
        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"下载文件失败: HTTP {response.status}")

                file_path = os.path.join(self.temp_dir, self.file_name)

                async with aiofiles.open(file_path, "wb") as f:
                    chuck_size = 1024 * 1024
                    while True:
                        chunk = await response.content.read(chuck_size)
                        if not chunk:
                            break
                        await f.write(chunk)

                return file_path

        except Exception as e:
            logger.error(f"下载文件失败：{e}")
            return None

        finally:
            if response:
                await response.release()
                await self.session.close()

    async def upload_file(self, file_path):
        url = self.url + "/api"
        try:
            response = await self.session.post(url, files={"image": open(file_path, "rb")})
            if response.status != 200:
                error_msg = await response.text()
                logger.error(f"上传文件错误: {error_msg}")
                raise Exception(f"上传文件错误: HTTP {response.status}")
            return await response.json()
        except Exception as e:
            logger.error(f"上传文件失败：{e}")
            return None


