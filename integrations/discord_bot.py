from __future__ import annotations

import asyncio
import json
import os

import discord

from config import PROOF_BACKFILL_LIMIT, PROOF_CHANNEL_ID, PROOF_PARSER_COOLDOWN
from models import RawProof
from proofs.ingestion import ProofIngestionService
from proofs.parser import ProofParseError


class ProofBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.proof_queue: asyncio.Queue[RawProof] = asyncio.Queue()
        self.ingestion = ProofIngestionService()
        self._worker_task: asyncio.Task | None = None
        self._did_backfill = False

    async def setup_hook(self) -> None:
        self._worker_task = asyncio.create_task(self._proof_worker())

    async def close(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
        await super().close()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")

        if self._did_backfill:
            return
        self._did_backfill = True

        channel = self.get_channel(PROOF_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(PROOF_CHANNEL_ID)
            except discord.DiscordException as exc:
                print(f"Could not access proof channel {PROOF_CHANNEL_ID}: {exc}")
                return

        if not hasattr(channel, "history"):
            print(f"Channel {PROOF_CHANNEL_ID} does not support message history")
            return

        async for message in channel.history(
            limit=PROOF_BACKFILL_LIMIT,
            oldest_first=True,
        ):
            await self._enqueue_message(message)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        if message.channel.id != PROOF_CHANNEL_ID:
            return
        await self._enqueue_message(message)

    async def _enqueue_message(self, message: discord.Message) -> None:
        content = (message.content or "").strip()
        image_urls = tuple(
            attachment.url
            for attachment in message.attachments
            if (attachment.content_type or "").startswith("image/")
        )

        if not content and not image_urls:
            return

        await self.proof_queue.put(
            RawProof(
                source="discord_bot",
                message_id=str(message.id),
                author=str(message.author),
                text=content or None,
                image_urls=image_urls,
                timestamp=message.created_at,
            )
        )

    async def _proof_worker(self) -> None:
        while True:
            raw = await self.proof_queue.get()
            try:
                proof = await self.ingestion.process(raw)
                print(
                    json.dumps(
                        {
                            "source": raw.source,
                            "source_message_id": raw.message_id,
                            "author": raw.author,
                            "image_urls": list(raw.image_urls),
                            **proof.model_dump(mode="json"),
                        },
                        indent=2,
                    )
                )
            except ProofParseError as exc:
                print(f"Proof {raw.message_id} could not be parsed: {exc}")
            except Exception as exc:
                print(
                    f"Proof {raw.message_id} failed: {type(exc).__name__}: {exc}"
                )
            finally:
                self.proof_queue.task_done()

            await asyncio.sleep(PROOF_PARSER_COOLDOWN)


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is missing. Use a Discord bot token, "
            "not a normal user token."
        )

    ProofBot().run(token)


if __name__ == "__main__":
    main()
