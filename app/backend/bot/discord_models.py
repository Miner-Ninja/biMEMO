"""Data models and UI views for the Discord bot.

Defines the PreparedDirectMessage dataclass and the discord.py View
subclasses (PrivacyView, StopView, ConfirmationView) used by the
Discord bot for DM payloads and interactive button flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord


if TYPE_CHECKING:
    from backend.bot.discord_bot import DiscordBot


@dataclass(frozen=True)
class PreparedDirectMessage:
    """Prepared Discord DM payload ready for the orchestrator."""

    text: str
    images: list[str]


async def _edit_interaction_message(
    interaction: discord.Interaction,
    *,
    content: str,
    view: discord.ui.View | None = None,
) -> None:
    """Edit one interaction message with the standard content/view pair."""
    await interaction.response.edit_message(content=content, view=view)


class PrivacyView(discord.ui.View):
    """View with 'I understand' button for privacy acknowledgment.

    Attributes:
        bot_ref: Reference to the DiscordBot instance.
    """

    def __init__(self, bot_ref: DiscordBot) -> None:
        """Initialize the privacy view.

        Args:
            bot_ref: Parent DiscordBot for callbacks.
        """
        super().__init__(timeout=None)
        self._bot_ref = bot_ref

    @discord.ui.button(label="I understand", style=discord.ButtonStyle.primary)
    async def acknowledge(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Handle privacy acknowledgment button click.

        Args:
            interaction: Discord interaction.
            button: The clicked button.
        """
        user_id = interaction.user.id
        self._bot_ref._adapter.acknowledge_privacy(user_id)
        await _edit_interaction_message(
            interaction,
            content="Privacy acknowledged. You may now send messages.",
            view=None,
        )
        pending = self._bot_ref._adapter.pop_pending_message(user_id)
        if pending is not None and interaction.channel is not None:
            await self._bot_ref._process_dm(
                interaction.channel,
                interaction.user,
                pending,
            )


class StopView(discord.ui.View):
    """View with 'Stop' button during message processing.

    Attributes:
        bot_ref: Reference to the DiscordBot instance.
    """

    def __init__(self, bot_ref: DiscordBot) -> None:
        """Initialize the stop view.

        Args:
            bot_ref: Parent DiscordBot for callbacks.
        """
        super().__init__(timeout=None)
        self._bot_ref = bot_ref

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_generation(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Handle stop button click.

        Args:
            interaction: Discord interaction.
            button: The clicked button.
        """
        loop = getattr(self._bot_ref.orchestrator, "_active_loop", None)
        if loop is not None and hasattr(loop, "stop"):
            loop.stop()
        await _edit_interaction_message(interaction, content="Stopped", view=None)


class ConfirmationView(discord.ui.View):
    """View with Approve/Deny buttons for RED zone confirmations.

    Attributes:
        bot_ref: Reference to the DiscordBot instance.
        action_id: Confirmation action identifier.
    """

    def __init__(self, bot_ref: DiscordBot, action_id: str) -> None:
        """Initialize the confirmation view.

        Args:
            bot_ref: Parent DiscordBot for callbacks.
            action_id: Confirmation action identifier.
        """
        super().__init__(timeout=None)
        self._bot_ref = bot_ref
        self._action_id = action_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Handle approval button click.

        Args:
            interaction: Discord interaction.
            button: The clicked button.
        """
        action = self._bot_ref.confirmation_mgr.approve(
            self._action_id,
            "discord",
        )
        if action is None:
            await _edit_interaction_message(
                interaction,
                content="Confirmation expired or invalid",
                view=None,
            )
            return
        await _edit_interaction_message(
            interaction,
            content=f"Approved: {action.tool_name}",
            view=None,
        )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Handle denial button click.

        Args:
            interaction: Discord interaction.
            button: The clicked button.
        """
        removed = self._bot_ref.confirmation_mgr.deny(self._action_id)
        if not removed:
            await _edit_interaction_message(
                interaction,
                content="Confirmation not found",
                view=None,
            )
            return
        await _edit_interaction_message(
            interaction,
            content="Denied",
            view=None,
        )
