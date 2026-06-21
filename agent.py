from __future__ import annotations

import asyncio
import logging
import re
import os
import json
from datetime import datetime
from typing import Any

import aiohttp
from dotenv import load_dotenv

from livekit import rtc, api
from livekit.agents import (
    AgentServer,
    AgentSession,
    Agent,
    JobContext,
    JobProcess,
    function_tool,
    RunContext,
    get_job_context,
    cli,
    inference,
    room_io,
    TurnHandlingOptions,
    InterruptionOptions,
    UserStateChangedEvent,
    AgentStateChangedEvent,
    FunctionToolsExecutedEvent,
    ConversationItemAddedEvent,
)
from livekit.plugins import (
    cartesia,
    groq,
    noise_cancellation,
)

from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION

# Load .env.local first, fall back to .env
load_dotenv(dotenv_path=".env.local")
load_dotenv(dotenv_path=".env")

logger = logging.getLogger("outbound-caller")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")

# ── 1. TTS sanitation — strip Llama-style leaked function-call syntax ─────────
_FUNC_CALL_RE = re.compile(
    r"<function=\w+.*?</function>|<\|.*?\|>",
    re.DOTALL,
)

# ── Supported languages for mid-call switching ────────────────────────────────
SUPPORTED_LANGUAGES = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
}


# ── Session-level observability ───────────────────────────────────────────────

def _register_session_events(session: AgentSession, agent: OutboundCaller) -> None:
    """Register observability event listeners on an AgentSession."""

    @session.on("close")
    def on_session_close():
        # ── 6. Backend call log on session close ──
        asyncio.create_task(agent.log_call_to_backend(
            status="completed", end_time=datetime.now()
        ))

        # ── Lead score summary ──
        ls = agent.lead_score
        logger.info("=" * 60)
        logger.info("[LEAD SCORE SUMMARY]")
        logger.info(f"  Score:       {ls['totalScore']}/100")
        logger.info(f"  Priority:    {ls['priority']}")
        logger.info(f"  Business:    {ls['businessType'] or 'N/A'}")
        logger.info(
            f"  Channels:    {', '.join(ls['customerChannels']) if ls['customerChannels'] else 'N/A'}"
        )
        logger.info(
            f"  Pain Points: {', '.join(ls['painPoints']) if ls['painPoints'] else 'N/A'}"
        )
        logger.info(f"  Timeline:    {ls['timeline'] or 'N/A'}")
        logger.info(
            f"  Confidence:  {', '.join(ls['confidenceSignals']) if ls['confidenceSignals'] else 'N/A'}"
        )
        logger.info(f"  Reasoning:   {ls['recommendedSolution'] or 'N/A'}")
        logger.info("=" * 60)

        usage = session.usage
        if usage and usage.model_usage:
            for mu in usage.model_usage:
                logger.info(f"[USAGE] Session totals: {mu}")

    @session.on("conversation_item_added")
    def on_conversation_item(ev: ConversationItemAddedEvent):
        item = ev.item
        if hasattr(item, "role") and hasattr(item, "text_content"):
            logger.info(f"[CONVERSATION] {item.role}: {item.text_content}")
            # ── 6. Transcript buffer ──
            agent.transcript_buffer.append({
                "role": item.role,
                "text": item.text_content,
                "timestamp": datetime.now().isoformat(),
            })
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
                f"[TOOL] {call.name}({call.arguments}) → {output.output if output else 'None'}"
            )

    @session.on("user_input_transcribed")
    def on_transcription(ev):
        if ev.is_final:
            logger.info(f"[STT] Final: {ev.transcript}")


# ── Agent ─────────────────────────────────────────────────────────────────────

class OutboundCaller(Agent):
    def __init__(self, *, dial_info: dict[str, Any]) -> None:
        super().__init__(instructions=AGENT_INSTRUCTION)
        self.participant: rtc.RemoteParticipant | None = None
        self.dial_info = dial_info

        # Call metadata
        self.call_start_time: datetime | None = None
        self.call_id: str | None = None
        self.room_name: str | None = None
        self.current_language: str = "en"

        # ── 6. Transcript buffer ──
        self.transcript_buffer: list = []

        # ── 4. Tool sequencing guards ──
        self._form_done = asyncio.Event()
        self._form_done.set()   # starts "not in flight"
        self._form_sent = False
        self._ending = False    # prevents end_call running twice
        self._scored = False    # set after score_and_route_lead is called

        # ── 3. Lead score state ──
        self.lead_score: dict = {
            "totalScore": 0,
            "priority": "LOW",
            "businessType": "",
            "customerChannels": [],
            "painPoints": [],
            "timeline": "",
            "confidenceSignals": [],
            "recommendedSolution": "",
            "breakdown": {
                "businessType": 0,
                "channels": 0,
                "painPoints": 0,
                "timeline": 0,
                "confidenceSignals": 0,
            },
        }

    def set_participant(self, participant: rtc.RemoteParticipant) -> None:
        self.participant = participant

    # ── 1. TTS sanitation node ────────────────────────────────────────────────
    async def tts_node(self, text, model_settings):
        """Filter leaked Llama function-call syntax before it reaches TTS."""
        async def _filtered():
            async for chunk in text:
                cleaned = _FUNC_CALL_RE.sub("", chunk)
                if cleaned:
                    yield cleaned
        return Agent.default.tts_node(self, _filtered(), model_settings)

    # ── Console mode greeting ─────────────────────────────────────────────────
    async def on_enter(self) -> None:
        """Greet immediately in console mode (no SIP participant)."""
        if self.dial_info.get("phone_number") is None:
            await self.session.generate_reply(
                instructions=SESSION_INSTRUCTION,
                allow_interruptions=False,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def extract_phone_number(self, identity: str) -> str:
        """Extract phone number from SIP identity like 'sip_+917780313547'."""
        if identity.startswith("sip_"):
            return identity[4:]
        return identity

    async def _hangup(self) -> None:
        """Delete the room (hangs up the call)."""
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )

    # ── 6. Backend logging ────────────────────────────────────────────────────
    async def log_call_to_backend(
        self, status: str = "ongoing", end_time: datetime | None = None
    ) -> None:
        """POST call log + transcript + lead score to the backend API."""
        if not self.call_id:
            return

        backend_url = os.getenv("BACKEND_API_URL", "http://localhost:5000")
        phone_number = (
            self.extract_phone_number(self.participant.identity)
            if self.participant
            else self.dial_info.get("phone_number", "unknown")
        )

        payload = {
            "callId": self.call_id,
            "phoneNumber": phone_number,
            "roomName": self.room_name,
            "startTime": self.call_start_time.isoformat() if self.call_start_time else None,
            "endTime": end_time.isoformat() if end_time else None,
            "status": status,
            "language": self.current_language,
            "transcript": self.transcript_buffer,
            "leadScoring": self.lead_score,
            "metadata": {
                "participantIdentity": self.participant.identity if self.participant else None,
                "callDirection": "outbound",
            },
        }

        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    f"{backend_url}/api/call-logs/log",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status == 200:
                        logger.info(
                            f"[BACKEND] Call log sent for {phone_number} "
                            f"(score {self.lead_score['totalScore']})"
                        )
                    else:
                        logger.error(
                            f"[BACKEND] Failed: HTTP {response.status}")
        except Exception as e:
            logger.error(f"[BACKEND] Error sending call log: {e}")

    # ── Tools ─────────────────────────────────────────────────────────────────

    @function_tool
    async def get_product_info(self, ctx: RunContext, product: str) -> str:
        """
        Returns detailed information about an Autonomiq AI agent product.

        Use this whenever the user asks about a specific product's features,
        capabilities, or use cases, or when comparing products.

        Args:
            product: One of "telecalling", "web", "whatsapp", or "all" for a summary.
        """
        PRODUCT_CATALOG: dict[str, dict] = {
            "telecalling": {
                "name": "Telecalling Agent",
                "description": "AI handles phone calls with natural voice — inbound and outbound.",
                "capabilities": [
                    "Answers queries, collects leads, schedules appointments",
                    "Product/service explanations, sales follow-ups",
                    "24/7 availability, CRM integration",
                ],
                "best_for": ["Service businesses, call-heavy operations", "Customer support, lead qualification"],
            },
            "web": {
                "name": "Web Agent",
                "description": "Interactive AI on a company's website that guides visitors.",
                "capabilities": [
                    "Guides visitors, opens pages automatically",
                    "Answers questions, improves engagement",
                    "Reduces bounce rate, converts visitors to leads",
                ],
                "best_for": ["E-commerce, SaaS platforms", "Information-heavy websites"],
            },
            "whatsapp": {
                "name": "WhatsApp Agent",
                "description": "AI-driven conversations on WhatsApp for instant support.",
                "capabilities": [
                    "Instant support, FAQs, orders, notifications",
                    "Lead generation, multilingual",
                ],
                "best_for": ["Local businesses, e-commerce", "Customer retention, WhatsApp-heavy businesses"],
            },
        }

        product = product.lower().strip()
        logger.info(f"[TOOL] get_product_info: {product}")

        if product == "all":
            return " | ".join(
                f"{v['name']}: {v['description']}" for v in PRODUCT_CATALOG.values()
            )

        info = PRODUCT_CATALOG.get(product)
        if not info:
            return f"Unknown product '{product}'. Available: {', '.join(PRODUCT_CATALOG)}, or 'all'."

        caps = ", ".join(str(c) for c in info["capabilities"])
        fits = ", ".join(str(f) for f in info["best_for"])
        return f"{info['name']}: {info['description']} Capabilities: {caps}. Best for: {fits}."

    # ── 3. Lead scoring ───────────────────────────────────────────────────────
    @function_tool
    async def score_and_route_lead(
        self,
        ctx: RunContext,
        total_score: str,
        priority: str,
        business_type: str = "",
        channels: str = "",
        pain_points: str = "",
        timeline: str = "",
        confidence_signals: str = "",
        reasoning: str = "",
    ) -> str:
        """
        Score the lead after 2-3 qualifying questions. Do NOT call on the first message.
        Understand their business and main problem first.

        Scoring guide:
        - Business Stage (0-20): Established=20, Growing=15, Startup=10
        - Channels needed (0-20): 3+=20, 2=15, 1=10
        - Pain Points (0-25): Quantified=25, Clear pain=20, General=10
        - Timeline (0-20): ASAP=20, This month=15, Next quarter=10, Exploring=5
        - Confidence Signals (0-15): Pricing ask=15, Budget=12, Decision maker=10

        Args:
            total_score: Score 0-100 as a string (e.g. "80").
            priority: One of HOT (75-100), WARM (50-74), COOL (25-49), LOW (0-24).
            business_type: What kind of business they run.
            channels: Communication channels they need.
            pain_points: Their pain points as understood from the conversation.
            timeline: Their urgency/timeline.
            confidence_signals: Buying signals detected (pricing questions, decision maker, etc.).
            reasoning: Brief explanation for this score.
        """
        try:
            score_int = int(str(total_score).strip())
        except (ValueError, TypeError):
            score_int = 0
        score_int = max(0, min(100, score_int))

        self._scored = True

        priority = (priority or "").strip().upper()
        if priority not in ("HOT", "WARM", "COOL", "LOW"):
            if score_int >= 75:
                priority = "HOT"
            elif score_int >= 50:
                priority = "WARM"
            elif score_int >= 25:
                priority = "COOL"
            else:
                priority = "LOW"

        self.lead_score["totalScore"] = score_int
        self.lead_score["priority"] = priority
        self.lead_score["businessType"] = business_type
        self.lead_score["customerChannels"] = (
            [c.strip() for c in channels.split(",")
             if c.strip()] if channels else []
        )
        self.lead_score["painPoints"] = [pain_points] if pain_points else []
        self.lead_score["timeline"] = timeline
        self.lead_score["confidenceSignals"] = (
            [c.strip() for c in confidence_signals.split(",") if c.strip()]
            if confidence_signals
            else []
        )
        self.lead_score["recommendedSolution"] = reasoning

        logger.info(
            f"[LEAD SCORE] {score_int}/100 | {priority} | {reasoning}"
        )

        if priority in ("HOT", "WARM"):
            return (
                f"Lead scored {score_int}/100 — {priority}. "
                f"FIRST: Reassure them briefly — you can definitely help and your team has done this for similar businesses. 1 sentence. "
                f"THEN: Offer a booking link — your team walks them through exact setup and pricing on WhatsApp, no commitment. "
                f"ASK if they'd like that. Do NOT call send_form yet — wait for yes."
            )
        elif priority == "COOL":
            return (
                f"Lead scored {score_int}/100 — {priority}. "
                f"FIRST: Acknowledge their situation positively — you can definitely help when they're ready. 1 sentence. "
                f"THEN: Offer a short requirements form — takes a minute, team puts together tailored options, no commitment. "
                f"ASK if they'd like it sent. Do NOT call send_form yet — wait for yes."
            )
        else:
            return (
                f"Lead scored {score_int}/100 — {priority}. "
                f"FIRST: Acknowledge warmly — 'that makes sense' or 'no rush at all'. Brief. "
                f"THEN: Offer a quick info form — team shares tailored info when they're ready, no commitment. "
                f"ASK if they'd like it. Do NOT call send_form unless they say yes."
            )

    # ── 4 & 5. End call with goodbye + form-wait guard ────────────────────────
    @function_tool
    async def end_call(self, ctx: RunContext) -> str:
        """
        End the call. Only call this AFTER you have already asked the user:
        "Are you happy to wrap up, or is there anything else I can help you with?"
        and they confirmed YES. The goodbye is spoken automatically.
        """
        if self._ending:
            return "already ending"
        self._ending = True

        identity = self.participant.identity if self.participant else "console"
        logger.info(f"[END] ending call for {identity}")

        # Wait for any in-flight send_form to finish
        try:
            await asyncio.wait_for(self._form_done.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[END] send_form still in flight after 12s, proceeding anyway")

        # ── Goodbye ───────────────────────────────────────────────────
        if self._form_sent:
            goodbye = (
                "You'll get it on WhatsApp in just a moment. "
                "Our team will reach out from there. "
                "Thanks so much for your time today — have a great day!"
            )
        else:
            goodbye = (
                "No problem at all. Feel free to reach out whenever you're ready. "
                "Thanks for your time — have a great day!"
            )

        try:
            await ctx.session.say(goodbye, allow_interruptions=False)
        except Exception as e:
            logger.warning(f"[END] failed to speak goodbye: {e}")

        await self.log_call_to_backend(status="completed", end_time=datetime.now())
        if self.participant:
            await self._hangup()

    # ── 4. Form sending ───────────────────────────────────────────────────────
    @function_tool
    async def send_form(self, ctx: RunContext) -> str:
        """
        Send the booking/requirements form via WhatsApp.
        Call ONLY after asking 'Want me to send it?' AND the user said yes.
        Never call before getting explicit consent.
        After this, call end_call.
        """
        if not self._scored:
            logger.warning(
                "[FORM] send_form called before score_and_route_lead")
            return "ERROR: Call score_and_route_lead first, then offer, then send."

        if not self.participant:
            logger.error("[FORM] No participant — cannot send form")
            return "Error: no participant available"

        phone_number = self.extract_phone_number(self.participant.identity)
        whatsapp_api_url = os.getenv("WHATSAPP_API_URL")
        if not whatsapp_api_url:
            logger.error("[FORM] WHATSAPP_API_URL not set")
            return "Error: WhatsApp API URL not configured"

        logger.info(f"[FORM] Sending to {phone_number} via {whatsapp_api_url}")

        self._form_done.clear()
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    f"{whatsapp_api_url}/sendFormTemplate",
                    json={"phone_number": phone_number,
                          "call_id": self.call_id},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    result = await response.json()
                    if result.get("success"):
                        channel = result.get("channel", "unknown")
                        self._form_sent = True
                        logger.info(
                            f"[FORM] Sent via {channel} to {phone_number}")
                        return f"Form sent successfully via {channel.upper()}"
                    else:
                        error = result.get("error", "Unknown error")
                        logger.error(f"[FORM] Failed: {error}")
                        return f"Failed to send form: {error}"
        except Exception as e:
            logger.error(f"[FORM] Error: {e}")
            return f"Error sending form: {e}"
        finally:
            self._form_done.set()

    # ── 7. Language switching ─────────────────────────────────────────────────
    @function_tool
    async def switch_language(self, ctx: RunContext, language: str) -> str:
        """
        Switch the conversation language mid-call.
        Call this when the caller explicitly asks to speak in Arabic, French, or English.

        Args:
            language: Target language code — 'en', 'ar', or 'fr'.
        """
        lang = language.strip().lower()
        if lang not in SUPPORTED_LANGUAGES:
            return (
                f"Unsupported language '{language}'. "
                f"Supported: English (en), Arabic (ar), French (fr)."
            )
        if lang == self.current_language:
            return f"Already speaking in {SUPPORTED_LANGUAGES[lang]}."

        session: AgentSession = ctx.session
        session.stt.update_options(language=lang)
        session.tts.update_options(language=lang)
        self.current_language = lang
        logger.info(
            f"[LANGUAGE] Switched to {SUPPORTED_LANGUAGES[lang]} ({lang})")
        return (
            f"Switched to {SUPPORTED_LANGUAGES[lang]}. "
            f"Continue the conversation in {SUPPORTED_LANGUAGES[lang]} now."
        )

    # ── Transfer (outbound-specific) ──────────────────────────────────────────
    @function_tool
    async def transfer_call(self, ctx: RunContext) -> str:
        """Transfer the call to a human agent. Only call after confirming with the user."""
        transfer_to = self.dial_info.get("transfer_to")
        if not transfer_to:
            return "cannot transfer call"
        logger.info(f"transferring call to {transfer_to}")
        await ctx.session.generate_reply(
            instructions="Let the user know you'll be transferring them now."
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
            return "call transferred"
        except Exception as e:
            logger.error(f"error transferring call: {e}")
            await ctx.session.generate_reply(
                instructions="There was an error transferring the call, apologise briefly."
            )
            await job_ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=job_ctx.room.name)
            )
            return f"transfer failed: {e}"

    # ── Answering machine (outbound-specific) ─────────────────────────────────
    # ── Answering machine ─────────────────────────────────────────────────────
    # Intentionally NOT a @function_tool — the LLM fires this on any confused
    # human response ("whatever", "I don't know") causing false positives.
    # Kept as an internal method for future SIP-signal-based voicemail detection.
    async def _detected_answering_machine(self) -> None:
        """Internal: hang up silently on confirmed voicemail."""
        if self.participant:
            logger.info(
                f"detected answering machine for {self.participant.identity}")
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )


# ── Server setup ──────────────────────────────────────────────────────────────

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Prewarm VAD once per worker process so sessions reuse the weights."""
    proc.userdata["vad"] = inference.VAD(model="silero")


server.setup_fnc = prewarm


@server.rtc_session(agent_name="outbound-caller")
async def entrypoint(ctx: JobContext) -> None:
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    # Parse dial info from job metadata
    if ctx.job.metadata:
        dial_info: dict[str, Any] = json.loads(ctx.job.metadata)
        participant_identity = phone_number = dial_info["phone_number"]
    else:
        logger.info("Running in console mode (no actual call)")
        dial_info = {"phone_number": None, "transfer_to": None}
        participant_identity = phone_number = None

    agent = OutboundCaller(dial_info=dial_info)
    agent.call_start_time = datetime.now()
    agent.call_id = ctx.job.id
    agent.room_name = ctx.room.name

    session = AgentSession(
        stt=groq.STT(
            model="whisper-large-v3",
            language="en",
        ),
        llm=groq.LLM(
            model="llama-3.3-70b-versatile",
            temperature=0.6,
        ),
        tts=cartesia.TTS(
            model="sonic-3",
            voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
            language="en",
        ),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
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

    _register_session_events(session, agent)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        ),
    )

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

            # Log call start to backend
            await agent.log_call_to_backend(status="ongoing")

            # Auto-hangup when the phone user disconnects
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

            # Agent speaks first
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
    # Console mode: on_enter() handles the greeting


if __name__ == "__main__":
    cli.run_app(server)
