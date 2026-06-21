from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Optional

from dotenv import load_dotenv
from livekit import api

load_dotenv(dotenv_path=".env.local")
load_dotenv(dotenv_path=".env")
logger = logging.getLogger("outbound-dispatcher")


class OutboundCallDispatcher:
    """Dispatcher for managing outbound calls via LiveKit."""

    def __init__(self) -> None:
        self._url = os.getenv("LIVEKIT_URL")
        self._api_key = os.getenv("LIVEKIT_API_KEY")
        self._api_secret = os.getenv("LIVEKIT_API_SECRET")
        self.agent_name = os.getenv("LIVEKIT_AGENT_NAME", "outbound-caller")

    async def make_call(
        self,
        phone_number: str,
        caller_id: Optional[str] = None,  # noqa: ARG002 — reserved for future use
        room_name: Optional[str] = None,
    ) -> dict:
        """
        Make an outbound call to a phone number using agent dispatch.

        Args:
            phone_number: Phone number to call in E.164 format (e.g. +1234567890).
            caller_id: Reserved — not used by the current agent.
            room_name: Optional room name; auto-generated when omitted.

        Returns:
            dict with success, room_name, dispatch_id, and phone_number.
        """
        if not room_name:
            room_name = (
                "outbound-" + "".join(str(random.randint(0, 9))
                                      for _ in range(10))
            )

        metadata = json.dumps({"phone_number": phone_number})
        logger.info("Initiating outbound call to %s in room %s",
                    phone_number, room_name)

        lkapi = api.LiveKitAPI(
            url=self._url,
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
        try:
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self.agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )

            # Robust dispatch_id extraction — handles both dict and object responses
            dispatch_id: str | None = None
            if isinstance(dispatch, dict):
                dispatch_id = (
                    dispatch.get("dispatch_id")
                    or dispatch.get("id")
                    or dispatch.get("job_id")
                )
            else:
                dispatch_id = (
                    getattr(dispatch, "dispatch_id", None)
                    or getattr(dispatch, "id", None)
                    or getattr(dispatch, "job_id", None)
                )

            if not dispatch_id:
                logger.error(
                    "Dispatch response missing dispatch_id; full response: %r", dispatch
                )
                return {
                    "success": False,
                    "error": "missing dispatch_id in dispatch response",
                    "phone_number": phone_number,
                }

            logger.info(
                "Call dispatch created. Room: %s, Dispatch ID: %s",
                room_name,
                dispatch_id,
            )
            return {
                "success": True,
                "room_name": room_name,
                "dispatch_id": dispatch_id,
                "phone_number": phone_number,
            }

        except Exception as e:
            logger.error("Failed to make outbound call: %s", str(e))
            return {
                "success": False,
                "error": str(e),
                "phone_number": phone_number,
            }
        finally:
            await lkapi.aclose()

    async def make_bulk_calls(
        self,
        phone_numbers: list[str],
        caller_id: Optional[str] = None,
        delay_between_calls: float = 2.0,
    ) -> list[dict]:
        """
        Make multiple outbound calls with an optional delay between each.

        Args:
            phone_numbers: List of E.164 phone numbers.
            caller_id: Reserved — not used by the current agent.
            delay_between_calls: Seconds to wait between dispatches (default 2.0).

        Returns:
            List of result dicts from make_call().
        """
        results: list[dict] = []
        for phone_number in phone_numbers:
            result = await self.make_call(phone_number, caller_id)
            results.append(result)
            if delay_between_calls > 0:
                await asyncio.sleep(delay_between_calls)
        return results


async def main() -> None:
    """Example usage — replace with a real number to test."""
    dispatcher = OutboundCallDispatcher()
    # result = await dispatcher.make_call("+1234567890")
    # print(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
