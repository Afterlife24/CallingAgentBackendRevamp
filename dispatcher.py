import asyncio
import json
import logging
import os
import random
from typing import Optional
from dotenv import load_dotenv
from livekit import api

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("outbound-dispatcher")


class OutboundCallDispatcher:
    """Dispatcher for managing outbound calls via LiveKit"""

    def __init__(self):
        # Store credentials instead of creating the API client here
        # to allow per-call creation/cleanup and avoid leaked aiohttp sessions
        self._url = os.getenv("LIVEKIT_URL")
        self._api_key = os.getenv("LIVEKIT_API_KEY")
        self._api_secret = os.getenv("LIVEKIT_API_SECRET")
        self.agent_name = os.getenv("LIVEKIT_AGENT_NAME", "outbound-caller")

    async def _safe_close_api(self, lk_api_instance):
        """Try to close underlying client/session if present (handles sync/async close)."""
        if lk_api_instance is None:
            return
        # Try common close methods on the API object
        for name in ("close", "close_session", "shutdown", "disconnect"):
            meth = getattr(lk_api_instance, name, None)
            if callable(meth):
                try:
                    if asyncio.iscoroutinefunction(meth):
                        await meth()
                    else:
                        meth()
                except Exception:
                    # best-effort close; ignore errors
                    pass
        # Also try to close any nested aiohttp session attributes
        sess = (
            getattr(lk_api_instance, "session", None)
            or getattr(lk_api_instance, "_session", None)
            or getattr(lk_api_instance, "client_session", None)
            or getattr(lk_api_instance, "aiohttp_session", None)
        )
        if sess is not None:
            close = getattr(sess, "close", None)
            if callable(close):
                try:
                    if asyncio.iscoroutinefunction(close):
                        await close()
                    else:
                        close()
                except Exception:
                    pass

    async def make_call(
        self,
        phone_number: str,
        caller_id: Optional[str] = None,  # noqa: ARG002
        room_name: Optional[str] = None,
    ) -> dict:
        """
        Make an outbound call to a phone number using agent dispatch

        Args:
            phone_number: Phone number to call (E.164 format, e.g., +1234567890)
            caller_id: Optional caller ID (not used currently)
            room_name: Optional room name (auto-generated if not provided)

        Returns:
            dict with success status, room_name, dispatch_id, and phone_number
        """
        lk_api = None
        try:
            if not room_name:
                room_name = f"outbound-{''.join(str(random.randint(0, 9)) for _ in range(10))}"

            metadata = json.dumps({"phone_number": phone_number})

            logger.info("Initiating outbound call to %s in room %s",
                        phone_number, room_name)

            # Create a LiveKit API client for this call (ensures we can close its session)
            lk_api = api.LiveKitAPI(
                url=self._url, api_key=self._api_key, api_secret=self._api_secret
            )

            # Create dispatch for the agent
            dispatch = await lk_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self.agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )

            # Robust dispatch_id extraction
            dispatch_id = None
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
                "Call dispatch created successfully. Room: %s, Dispatch ID: %s",
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
            # Ensure underlying http session is closed to avoid "Unclosed client session" warnings
            try:
                await self._safe_close_api(lk_api)
            except Exception:
                pass

    async def make_bulk_calls(
        self,
        phone_numbers: list[str],
        caller_id: Optional[str] = None,
        delay_between_calls: float = 2.0,
    ) -> list[dict]:
        """
        Make multiple outbound calls with optional delay between calls

        Args:
            phone_numbers: List of phone numbers to call
            caller_id: Optional caller ID
            delay_between_calls: Seconds to wait between calls (default: 2.0)

        Returns:
            List of result dicts from make_call()
        """
        results = []

        for phone_number in phone_numbers:
            result = await self.make_call(phone_number, caller_id)
            results.append(result)

            if delay_between_calls > 0:
                await asyncio.sleep(delay_between_calls)

        return results


async def main():
    """Example usage"""
    dispatcher = OutboundCallDispatcher()
    # Example: await dispatcher.make_call("+1234567890")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
