from __future__ import annotations

import asyncio
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from src.db import Database
from src.util import load_config


BASE_DIR = Path(__file__).resolve().parent


class DsBot(commands.Bot):
    db: Database
    config: dict


async def main() -> None:
    load_dotenv()
    token = os.getenv("TOKEN")
    if not token:
        raise RuntimeError("TOKEN not found in .env")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.voice_states = True

    bot: DsBot = DsBot(command_prefix="!", intents=intents)
    bot.config = load_config(BASE_DIR / "config.json")
    bot.db = Database(str(BASE_DIR / "storage.sqlite3"))
    await bot.db.init()

    extensions = [
        "src.cogs.tickets",
        "src.cogs.fleet",
        "src.cogs.afk",
        "src.cogs.admin",
        "src.cogs.welcome",
    ]
    for ext in extensions:
        await bot.load_extension(ext)
        print(f"[INIT] {ext}")

    ready_fired = False

    @bot.event
    async def on_ready() -> None:
        nonlocal ready_fired
        assert bot.user is not None

        if not ready_fired:
            ready_fired = True

            from src.cogs.tickets import TicketPanelView, TicketControlView
            from src.cogs.fleet import FleetPanelView
            from src.cogs.afk import AfkPanelView

            bot.add_view(TicketPanelView(bot))
            bot.add_view(TicketControlView(bot))
            bot.add_view(FleetPanelView(bot))
            bot.add_view(AfkPanelView(bot))

            synced = await bot.tree.sync()
            print(f"Logged in as {bot.user} | Synced {len(synced)} slash commands")
        else:
            print(f"Reconnected as {bot.user}")

        await bot.change_presence(activity=discord.Game(name="/ commands"))

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())

