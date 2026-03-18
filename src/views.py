from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord
from discord import ui

if TYPE_CHECKING:
    from bot import DsBot

from src.util import save_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

SettingDef = dict[str, str]


def _cfg_get(cfg: dict, key: str) -> Any:
    if key.startswith("log_"):
        return cfg.get("log_channels", {}).get(key[4:])
    return cfg.get(key)


def _cfg_set(cfg: dict, key: str, value: Any) -> None:
    if key.startswith("log_"):
        cfg.setdefault("log_channels", {})[key[4:]] = value
    else:
        cfg[key] = value


def _fmt(val: Any, kind: str) -> str:
    if val is None:
        return "❌ не задано"
    if kind == "role":
        return f"<@&{val}>"
    if kind in ("text_channel", "voice_channel", "category"):
        return f"<#{val}>"
    return str(val)


def build_settings_embed(title: str, items: list[SettingDef], cfg: dict) -> discord.Embed:
    embed = discord.Embed(title=title, colour=discord.Colour.gold())
    lines = []
    for it in items:
        val = _cfg_get(cfg, it["key"])
        lines.append(f"**{it['label']}:** {_fmt(val, it['kind'])}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Выберите параметр ниже для изменения")
    return embed


# ── Generic settings select ─────────────────────────────────

class _SettingsSelect(ui.Select):
    def __init__(self, bot: DsBot, items: list[SettingDef]) -> None:
        self.bot = bot
        self._items = {it["key"]: it for it in items}
        options = [
            discord.SelectOption(
                label=it["label"], value=it["key"], emoji=it.get("emoji"),
            )
            for it in items
        ]
        super().__init__(placeholder="Выберите параметр...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        it = self._items[self.values[0]]
        kind = it["kind"]
        key = it["key"]
        label = it["label"]

        if kind == "role":
            await interaction.response.send_message(
                f"**{label}** — выберите роль:",
                view=_RoleSelectorView(self.bot, key, label), ephemeral=True,
            )
        elif kind == "text_channel":
            await interaction.response.send_message(
                f"**{label}** — выберите канал:",
                view=_ChannelView(self.bot, key, label, [discord.ChannelType.text]),
                ephemeral=True,
            )
        elif kind == "voice_channel":
            await interaction.response.send_message(
                f"**{label}** — выберите войс:",
                view=_ChannelView(self.bot, key, label, [discord.ChannelType.voice]),
                ephemeral=True,
            )
        elif kind == "category":
            await interaction.response.send_message(
                f"**{label}** — выберите категорию:",
                view=_ChannelView(self.bot, key, label, [discord.ChannelType.category]),
                ephemeral=True,
            )
        elif kind == "text":
            await interaction.response.send_modal(_TextModal(self.bot, key, label))


class SettingsView(ui.View):
    def __init__(self, bot: DsBot, items: list[SettingDef]) -> None:
        super().__init__(timeout=300)
        self.add_item(_SettingsSelect(bot, items))


# ── Selectors ───────────────────────────────────────────────

class _RoleSelect(ui.RoleSelect):
    def __init__(self, bot: DsBot, key: str, label: str) -> None:
        super().__init__(placeholder="Выберите роль...")
        self.bot, self._key, self._label = bot, key, label

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        _cfg_set(self.bot.config, self._key, role.id)
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.edit_message(
            content=f"✅ **{self._label}** → {role.mention}", view=None,
        )


class _RoleSelectorView(ui.View):
    def __init__(self, bot: DsBot, key: str, label: str) -> None:
        super().__init__(timeout=60)
        self.add_item(_RoleSelect(bot, key, label))


class _ChannelSel(ui.ChannelSelect):
    def __init__(
        self, bot: DsBot, key: str, label: str, types: list[discord.ChannelType],
    ) -> None:
        super().__init__(placeholder="Выберите канал...", channel_types=types)
        self.bot, self._key, self._label = bot, key, label

    async def callback(self, interaction: discord.Interaction) -> None:
        ch = self.values[0]
        _cfg_set(self.bot.config, self._key, ch.id)
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.edit_message(
            content=f"✅ **{self._label}** → <#{ch.id}>", view=None,
        )


class _ChannelView(ui.View):
    def __init__(
        self, bot: DsBot, key: str, label: str, types: list[discord.ChannelType],
    ) -> None:
        super().__init__(timeout=60)
        self.add_item(_ChannelSel(bot, key, label, types))


class _TextModal(ui.Modal):
    value_field = ui.TextInput(label="Значение", max_length=512)

    def __init__(self, bot: DsBot, key: str, label: str) -> None:
        super().__init__(title=label[:45])
        self.bot, self._key, self._label = bot, key, label

    async def on_submit(self, interaction: discord.Interaction) -> None:
        val = self.value_field.value.strip()
        _cfg_set(self.bot.config, self._key, val)
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.send_message(
            f"✅ **{self._label}** → `{val}`", ephemeral=True,
        )
