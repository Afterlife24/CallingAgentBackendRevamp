from __future__ import annotations

import asyncio
import logging
from dotenv import load_dotenv
import json
import os
from typing import Any

from livekit import rtc, api
from livekit.agents import (
    AgentSession,
    Agent,
    ChatContext,
    ChatMessage,
    JobContext,
    function_tool,
    RunContext,
    get_job_context,
    cli,
    WorkerOptions,
    room_io,
    TurnHandlingOptions,
    InterruptionOptions,
    UserStateChangedEvent,
    AgentStateChangedEvent,
    FunctionToolsExecutedEvent,
    ConversationItemAddedEvent,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import (
    cartesia,
    groq,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("outbound-caller")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")

# Sliding window: cap context to keep per-turn token usage low
MAX_HISTORY_ITEMS = 10  # ~5 user + 5 assistant turns


# Product catalog — served on-demand via tool call instead of
# bloating the system prompt every LLM turn.
PRODUCT_CATALOG: dict[str, dict[str, str | list[str]]] = {
    "telecalling": {
        "name": "Telecalling Agent",
        "description": "AI handles phone calls with natural voice — inbound and outbound.",
        "capabilities": [
            "Answers queries, collects leads, schedules appointments",
            "Product/service explanations, sales follow-ups",
            "24/7 availability, CRM integration",
        ],
        "best_for": [
            "Service businesses, call-heavy operations",
            "Customer support, lead qualification",
        ],
    },
    "web": {
        "name": "Web Agent",
        "description": "Interactive AI avatar on a company's website that guides visitors.",
        "capabilities": [
            "Guides visitors, opens pages automatically",
            "Answers questions, improves engagement",
            "Reduces bounce rate, converts visitors to leads",
        ],
        "best_for": [
            "E-commerce, SaaS platforms",
            "Information-heavy websites",
        ],
    },
    "whatsapp": {
        "name": "WhatsApp Agent",
        "description": "AI-driven conversations on WhatsApp for instant support.",
        "capabilities": [
            "Instant support, FAQs, orders, notifications",
            "Lead generation, multilingual",
        ],
        "best_for": [
            "Local businesses, e-commerce",
            "Customer retention, WhatsApp-heavy businesses",
        ],
    },
}


def _register_session_events(session: AgentSession) -> None:
    """Register observability event listeners on an AgentSession."""

    @session.on("close")
    def on_session_close():
        usage = session.usage
        if usage and usage.model_usage:
            for mu in usage.model_usage:
                logger.info(f"[USAGE] Session totals: {mu}")

    @session.on("conversation_item_added")
    def on_conversation_item(ev: ConversationItemAddedEvent):
        item = ev.item
        if hasattr(item, "role") and hasattr(item, "text_content"):
            logger.info(f"[CONVERSATION] {item.role}: {item.text_content}")
        else:
            logger.info(f"[CONVERSATION] item added: {type(item).__name__}")

    @session.on("agent_state_changed")
    def on_agent_state(ev: AgentStateChangedEvent):
        logger.info(f"[STATE] Agent: {ev.old_state} → {ev.new_state}")

    @session.on("user_state_changed")
    def on_user_state(ev: UserStateChangedEvent):
        logger.info(f"[STATE] User: {ev.old_state} → {ev.new_state}")

    @session.on("function_tools_executed")
    def on_tools_executed(ev: FunctionToolsExecutedEvent):
        for call, output in ev.zipped():
            logger.info(
                f"[TOOL] {call.name}({call.arguments}) → {output.output if output else 'None'}")

    @session.on("user_input_transcribed")
    def on_transcription(ev):
        if ev.is_final:
            logger.info(f"[STT] Final: {ev.transcript}")


class OutboundCaller(Agent):
    def __init__(self, *, dial_info: dict[str, Any]):
        end_call_tool = EndCallTool(
            extra_description="End the call when the user says goodbye, thanks you and is done, or explicitly asks to hang up.",
            delete_room=True,
            end_instructions="Thank the user briefly and say goodbye.",
        )
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            tools=end_call_tool.tools,
        )
        self.participant: rtc.RemoteParticipant | None = None
        self.dial_info = dial_info

    def set_participant(self, participant: rtc.RemoteParticipant):
        self.participant = participant

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        """Prune old conversation messages to keep context window bounded."""
        turn_ctx.truncate(max_items=MAX_HISTORY_ITEMS)

    @function_tool()
    async def get_product_info(self, ctx: RunContext, product: str) -> str:
        """
        Returns detailed information about an Autonomic AI agent product.

        Use this tool whenever the user asks about a specific product's features,
        capabilities, or use cases. Also use it when you need to compare products
        or recommend the best fit for a user's business.

        Args:
            product: One of "telecalling", "web", "whatsapp", or "all" for a summary.
        """
        product = product.lower().strip()
        logger.info(f"[TOOL] get_product_info called with product: {product}")

        if product == "all":
            lines: list[str] = []
            for info in PRODUCT_CATALOG.values():
                lines.append(f"{info['name']}: {info['description']}")
            return " | ".join(lines)

        info = PRODUCT_CATALOG.get(product)
        if not info:
            available = ", ".join(PRODUCT_CATALOG.keys())
            return f"Unknown product '{product}'. Available: {available}, or 'all'."

        caps = ", ".join(str(c) for c in info["capabilities"])
        fits = ", ".join(str(f) for f in info["best_for"])
        return f"{info['name']}: {info['description']} Capabilities: {caps}. Best for: {fits}."

    @function_tool()
    async def transfer_call(self, ctx: RunContext):
        """Transfer the call to a human agent, called after confirming with the user"""
        transfer_to = self.dial_info.get("transfer_to")
        if not transfer_to:
            return "cannot transfer call"
        logger.info(f"transferring call to {transfer_to}")
        await ctx.session.generate_reply(
            instructions="let the user know you'll be transferring them"
        )
        job_ctx = get_job_context()
        try:
            await job_ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=job_ctx.room.name,
                    participant_identity=self.participant.identity,
                    transfer_to=f"tel:{transfer_to}",
                )
            )
            logger.info(f"transferred call to {transfer_to}")
        except Exception as e:
            logger.error(f"error transferring call: {e}")
            await ctx.session.generate_reply(
                instructions="there was an error transferring the call."
            )
            await job_ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=job_ctx.room.name)
            )

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
        logger.info(
            f"detected answering machine for {self.participant.identity}")
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


def _create_session() -> AgentSession:
    """Create an AgentSession with the standard Groq + Cartesia pipeline."""
    return AgentSession(
        stt=cartesia.STT(model="ink-whisper", language="en"),
        llm=groq.LLM(
            model="llama-3.1-8b-instant",
            temperature=0.6,
        ),
        tts=cartesia.TTS(
            model="sonic-3",
            voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
            language="en",
        ),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            interruption=InterruptionOptions(
                enabled=True,
                mode="adaptive",
                min_duration=0.5,
                min_words=1,
                resume_false_interruption=True,
                false_interruption_timeout=2.0,
            ),
        ),
    )


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    if ctx.job.metadata:
        dial_info = json.loads(ctx.job.metadata)
        participant_identity = phone_number = dial_info["phone_number"]
    else:
        logger.info("Running in console mode (no actual call)")
        dial_info = {"phone_number": "console-test", "transfer_to": None}
        participant_identity = phone_number = None

    agent = OutboundCaller(dial_info=dial_info)

    if phone_number:
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=outbound_trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=participant_identity,
                    wait_until_answered=True,
                )
            )

            participant = await ctx.wait_for_participant(identity=participant_identity)
            logger.info(f"participant joined: {participant.identity}")
            agent.set_participant(participant)

            session = _create_session()
            _register_session_events(session)

            # Auto-hangup when the phone user disconnects (hangs up)
            @ctx.room.on("participant_disconnected")
            def on_participant_disconnected(
                disconnected_participant: rtc.RemoteParticipant,
            ) -> None:
                if disconnected_participant.identity == participant_identity:
                    logger.info(
                        f"Phone user {participant_identity} hung up — deleting room"
                    )

                    async def _cleanup() -> None:
                        try:
                            await ctx.api.room.delete_room(
                                api.DeleteRoomRequest(room=ctx.room.name)
                            )
                        except Exception as e:
                            logger.warning(
                                f"Error deleting room after hangup: {e}")
                        ctx.shutdown()

                    asyncio.create_task(_cleanup())

            await session.start(
                agent=agent,
                room=ctx.room,
                room_options=room_io.RoomOptions(
                    audio_input=room_io.AudioInputOptions(
                        noise_cancellation=noise_cancellation.BVCTelephony(),
                    ),
                ),
            )

            await session.generate_reply(
                instructions=SESSION_INSTRUCTION,
                allow_interruptions=False,
            )
            logger.info("Greeting generated — agent is now listening")

        except api.TwirpError as e:
            logger.error(
                f"error creating SIP participant: {e.message}, "
                f"SIP status: {e.metadata.get('sip_status_code')} "
                f"{e.metadata.get('sip_status')}"
            )
            ctx.shutdown()
        except Exception as e:
            logger.error(f"unexpected error in entrypoint: {e}")
            import traceback
            traceback.print_exc()
            ctx.shutdown()
    else:
        # Console mode — same pipeline, no SIP
        logger.info("Starting session in console mode")
        session = _create_session()
        await session.start(agent=agent, room=ctx.room)
        _register_session_events(session)
        await session.generate_reply(
            instructions=SESSION_INSTRUCTION,
            allow_interruptions=False,
        )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller",
            port=8082,
        )
    )
