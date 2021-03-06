from __future__ import annotations

import asyncio
import pathlib
import re
import subprocess
import threading
from typing import TYPE_CHECKING, Iterator

import hikari

from ivycraft.config import CONFIG

from .whitelist import Whitelist

if TYPE_CHECKING:
    from ivycraft.bot.bot import Bot


def paginate(text: str) -> Iterator[str]:
    current = 0
    jump = 500
    while True:
        page = text[current : current + jump]
        current += jump
        yield page
        if current >= len(text):
            return


CHAT_MSG = re.compile(
    r"\[Async Chat Thread - #\d+\/INFO]: <(?P<name>.+)> (?P<message>.+)"
)
LEAVE_MSG = re.compile(r"\[Server thread\/INFO]: (?P<name>.+) left the game")
JOIN_MSG = re.compile(r"\[Server thread\/INFO]: (?P<name>.+)\[\/.+] logged in")


class MCServer:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._wh: hikari.ExecutableWebhook | None = None

        self.proc: subprocess.Popen[bytes] | None = None
        self.path = pathlib.Path(CONFIG.server_path)
        self.whitelist = Whitelist(self.path / "whitelist.json", bot)

        self.chat_message_queue: list[str] = []

        self.reader = threading.Thread(target=self._reader_thread)
        self.logger: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.proc = subprocess.Popen(
            f"cd {self.path.resolve()} && java -Xmx{CONFIG.server_memory} "
            f"-Xms{CONFIG.server_memory} -jar server.jar nogui",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            shell=True,
        )
        self.reader.start()
        await self.update_whitelist()
        self.logger = asyncio.create_task(self.sender_loop())

    async def sender_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            if not self.chat_message_queue:
                continue

            to_send = "\n".join(lin.strip() for lin in self.chat_message_queue)
            for page in paginate(to_send):
                await self.bot.rest.create_message(CONFIG.chat_channel, page)
            self.chat_message_queue.clear()

    def command(self, command: str) -> None:
        assert self.proc is not None
        assert self.proc.stdin is not None
        self.proc.stdin.write((command + "\n").encode("utf8"))
        self.proc.stdin.flush()

    async def update_whitelist(self) -> None:
        await self.whitelist.save()
        self.command("whitelist reload")

    def _reader_thread(self) -> None:
        assert self.proc is not None
        assert self.proc.stdout is not None
        for _line in iter(self.proc.stdout.readline, b""):
            line: str = _line.decode().strip()
            print(line)
            if match := CHAT_MSG.findall(line):
                name, message = match[0]
                self.chat_message_queue.append(f"<{name}> {message}")
            elif match := LEAVE_MSG.findall(line):
                name = match[0]
                self.chat_message_queue.append(f"{name} left the game")
            elif match := JOIN_MSG.findall(line):
                name = match[0]
                self.chat_message_queue.append(f"{name} joined the game")
