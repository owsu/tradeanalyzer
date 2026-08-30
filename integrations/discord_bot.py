from __future__ import annotations

import asyncio
import json
import mimetypes
import os

import discord
from discord import app_commands

from config import (
    DATABASE_PATH,
    DISCORD_SUBMITTER_USER_ID,
    PROOF_BACKFILL_LIMIT,
    PROOF_CHANNEL_ID,
    PROOF_MAX_IMAGE_BYTES,
    PROOF_MAX_IMAGES,
    PROOF_MAX_TOTAL_IMAGE_BYTES,
    PROOF_PARSER_COOLDOWN,
    PROOF_QUEUE_SIZE,
)
from database import Database
from models import ProofImage, RawProof
from proofs.ingestion import ProofIngestionService
from proofs.parser import ProofParseError


class ProofBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.proof_queue: asyncio.Queue[RawProof] = asyncio.Queue(
            maxsize=PROOF_QUEUE_SIZE
        )
        self.ingestion = ProofIngestionService(database=Database(DATABASE_PATH))
        self._worker_task: asyncio.Task | None = None
        self._did_backfill = False
        self.tree.add_command(
            app_commands.ContextMenu(
                name="Submit Trade Proof",
                callback=self._submit_trade_proof,
                allowed_installs=app_commands.AppInstallationType(
                    guild=False, user=True
                ),
                allowed_contexts=app_commands.AppCommandContext(
                    guild=True, dm_channel=True, private_channel=True
                ),
            )
        )

    async def setup_hook(self) -> None:
        self._worker_task = asyncio.create_task(self._proof_worker())
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} global application command(s)")

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

    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if after.author == self.user or after.channel.id != PROOF_CHANNEL_ID:
            return
        await self._enqueue_message(after)

    async def _enqueue_message(self, message: discord.Message) -> bool:
        content = (message.content or "").strip()
        images: list[ProofImage] = []
        image_urls: list[str] = []
        total_image_bytes = 0
        supported_types = {"image/jpeg", "image/png", "image/webp"}

        for attachment in message.attachments:
            reported_type = attachment.content_type or mimetypes.guess_type(
                attachment.filename
            )[0]
            mime_type = (reported_type or "").split(";", 1)[0].lower()
            if mime_type not in supported_types or len(images) >= PROOF_MAX_IMAGES:
                continue
            if attachment.size > PROOF_MAX_IMAGE_BYTES:
                print(
                    f"Skipping oversized proof image {attachment.filename}: "
                    f"{attachment.size} bytes"
                )
                continue
            if total_image_bytes + attachment.size > PROOF_MAX_TOTAL_IMAGE_BYTES:
                print(f"Skipping {attachment.filename}: proof image budget exceeded")
                continue
            try:
                data = await attachment.read(use_cached=True)
            except discord.DiscordException as exc:
                print(f"Could not download proof image {attachment.filename}: {exc}")
                continue
            if len(data) > PROOF_MAX_IMAGE_BYTES:
                print(f"Skipping oversized downloaded image {attachment.filename}")
                continue
            total_image_bytes += len(data)
            images.append(
                ProofImage(
                    data=data,
                    mime_type=mime_type,
                    filename=attachment.filename,
                )
            )
            image_urls.append(attachment.url)

        if not content and not image_urls:
            return False

        await self.proof_queue.put(
            RawProof(
                source="discord_bot",
                message_id=str(message.id),
                channel_id=str(message.channel.id),
                author=str(message.author),
                text=content or None,
                images=tuple(images),
                image_urls=tuple(image_urls),
                timestamp=message.created_at,
            )
        )
        return True

    async def _submit_trade_proof(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if (
            DISCORD_SUBMITTER_USER_ID is not None
            and interaction.user.id != DISCORD_SUBMITTER_USER_ID
        ):
            await interaction.response.send_message(
                "You are not allowed to submit proofs to this analyzer.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        queued = await self._enqueue_message(message)
        if queued:
            await interaction.followup.send(
                "Proof queued for parsing. Duplicate messages are skipped automatically.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "That message has no readable text or supported image attachment.",
                ephemeral=True,
            )

    async def _proof_worker(self) -> None:
        while True:
            raw = await self.proof_queue.get()
            try:
                proof = await self.ingestion.process(raw)
                if proof is None:
                    print(f"Skipping already processed proof {raw.message_id}")
                    continue
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
