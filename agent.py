from __future__ import annotations

import asyncio
import logging
from dotenv import load_dotenv
import json
import os
from typing import Any
from pathlib import Path

from livekit import rtc, api
from livekit.agents import (
    AgentSession,
    Agent,
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
from livekit.agents.utils.audio import audio_frames_from_file
from livekit.plugins import (
    cartesia,
    openai,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("outbound-caller")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
GREETING_AUDIO_PATH = Path(__file__).parent / "assets" / "greeting.wav"


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
        logger.info(f"[CONVERSATION] {item.role}: {item.text_content}")

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
        super().__init__(instructions=AGENT_INSTRUCTION)
        self.participant: rtc.RemoteParticipant | None = None
        self.dial_info = dial_info

    def set_participant(self, participant: rtc.RemoteParticipant):
        self.participant = participant

    async def hangup(self):
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(
            api.DeleteRoomRequest(room=job_ctx.room.name)
        )

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
            await self.hangup()

    @function_tool()
    async def end_call(self, ctx: RunContext):
        """Called when the user wants to end the call"""
        logger.info(f"ending the call for {self.participant.identity}")
        await ctx.wait_for_playout()
        await self.hangup()

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Use this tool AFTER you hear the voicemail greeting"""
        logger.info(
            f"detected answering machine for {self.participant.identity}")
        await self.hangup()


# Pre-load greeting WAV frames at module level so they're ready instantly
_greeting_frames: list[rtc.AudioFrame] | None = None


async def _load_greeting_frames() -> list[rtc.AudioFrame]:
    """Load greeting WAV into memory once."""
    global _greeting_frames
    if _greeting_frames is not None:
        return _greeting_frames

    if not GREETING_AUDIO_PATH.exists():
        logger.warning(f"Greeting WAV not found at {GREETING_AUDIO_PATH}")
        return []

    frames: list[rtc.AudioFrame] = []
    async for frame in audio_frames_from_file(
        str(GREETING_AUDIO_PATH), sample_rate=24000, num_channels=1
    ):
        frames.append(frame)

    _greeting_frames = frames
    logger.info(f"Pre-loaded greeting WAV: {len(frames)} frames")
    return frames


async def _play_greeting_direct(room: rtc.Room, frames: list[rtc.AudioFrame]) -> None:
    """Publish greeting WAV directly to the room as a raw audio track.

    This bypasses the agent session entirely so the caller hears the
    greeting the instant they pick up — zero delay.
    """
    source = rtc.AudioSource(sample_rate=24000, num_channels=1)
    track = rtc.LocalAudioTrack.create_audio_track("greeting", source)
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)

    publication = await room.local_participant.publish_track(track, options)
    logger.info("Published greeting audio track to room")

    # Push all frames into the source
    for frame in frames:
        await source.capture_frame(frame)

    # Small buffer to let the last frames flush through WebRTC
    await asyncio.sleep(0.5)

    # Unpublish the greeting track so it doesn't interfere with the agent
    await room.local_participant.unpublish_track(publication.sid)
    logger.info("Greeting playback complete, track unpublished")


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    # Pre-load greeting frames
    greeting_frames = await _load_greeting_frames()

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

            # ── STEP 1: Play greeting WAV immediately via raw track ──
            # This happens BEFORE the agent session starts, so there's
            # zero delay — the caller hears the greeting instantly.
            if greeting_frames:
                logger.info(
                    f"Playing greeting WAV directly ({len(greeting_frames)} frames)")
                greeting_task = asyncio.create_task(
                    _play_greeting_direct(ctx.room, greeting_frames)
                )
            else:
                greeting_task = None
                logger.warning("No greeting frames available")

            # ── STEP 2: Start agent session in parallel ──
            # Audio input stays muted so the realtime model can't hear
            # the user saying "hello" during the greeting and auto-respond.
            session = AgentSession(
                stt=cartesia.STT(model="ink-whisper", language="en"),
                llm=openai.LLM(
                    model="llama-3.1-8b-instant",
                    base_url="https://api.groq.com/openai/v1",
                    api_key=os.getenv("GROQ_API_KEY"),
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

            # Flag to suppress any auto-greeting from the realtime model
            greeting_done = asyncio.Event()

            _register_session_events(session)

            @session.on("speech_created")
            def _on_speech_created(ev: Any) -> None:
                if not greeting_done.is_set():
                    logger.info("Suppressing realtime model auto-greeting")
                    asyncio.ensure_future(session.interrupt())

            await session.start(
                agent=agent,
                room=ctx.room,
                room_options=room_io.RoomOptions(
                    audio_input=room_io.AudioInputOptions(
                        noise_cancellation=noise_cancellation.BVCTelephony(),
                    ),
                ),
            )

            # Keep audio input muted until greeting finishes
            session.input.set_audio_enabled(False)
            logger.info("Session started, audio input muted during greeting")

            # ── STEP 3: Wait for greeting to finish ──
            if greeting_task:
                await greeting_task

            # Kill any auto-greeting the model may have queued
            await session.interrupt()

            # ── STEP 4: Unmute and hand off to the realtime model ──
            greeting_done.set()
            session.input.set_audio_enabled(True)
            logger.info("Greeting done — agent is now listening")

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
        # Console mode — just start normally
        logger.info("Starting session in console mode")
        session_console = AgentSession(
            stt=cartesia.STT(model="ink-whisper", language="en"),
            llm=openai.LLM(
                model="llama-3.3-70b-versatile",
                base_url="https://api.groq.com/openai/v1",
                api_key=os.getenv("GROQ_API_KEY"),
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
        await session_console.start(agent=agent, room=ctx.room)
        _register_session_events(session_console)
        await session_console.generate_reply(
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
