"""
Simple CLI tool to make outbound calls

Usage:
    Single call:  python call_handler.py +1234567890
    Bulk calls:   python call_handler.py +1234567890 +0987654321 +1122334455

Note: Phone numbers must be in E.164 format (e.g., +1234567890)
"""
import asyncio
import logging
import sys
from dispatcher import OutboundCallDispatcher

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("call-handler")


async def make_single_call(phone_number: str, caller_id: str = "VisionIT Sales"):
    """Make a single outbound call"""
    dispatcher = OutboundCallDispatcher()
    result = await dispatcher.make_call(phone_number, caller_id=caller_id)

    if result["success"]:
        print("\n✅ Call initiated successfully!")
        print(f"   Room: {result['room_name']}")
        print(f"   Phone: {result['phone_number']}")
        print(f"   Dispatch ID: {result.get('dispatch_id', 'N/A')}")
        print("\n💡 The agent worker will pick up this dispatch and place the call.")
    else:
        print(f"\n❌ Call failed: {result.get('error', 'Unknown error')}")
        sys.exit(1)


async def make_bulk_calls(phone_numbers: list[str], caller_id: str = "VisionIT Sales"):
    """Make multiple outbound calls"""
    dispatcher = OutboundCallDispatcher()

    print(f"\n📞 Initiating {len(phone_numbers)} calls...")
    results = await dispatcher.make_bulk_calls(phone_numbers, caller_id=caller_id)

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful

    print("\n📊 Call Summary:")
    print(f"   Total: {len(results)}")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")

    if failed > 0:
        print("\n❌ Failed calls:")
        for r in results:
            if not r["success"]:
                print(
                    f"   - {r['phone_number']}: {r.get('error', 'Unknown error')}")

    if successful > 0:
        print("\n✅ Successful calls:")
        for r in results:
            if r["success"]:
                print(f"   - {r['phone_number']} → Room: {r['room_name']}")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("VisionIT Outbound Call Handler")
        print("=" * 50)
        print("\nUsage:")
        print("  Single call:  python call_handler.py +1234567890")
        print("  Bulk calls:   python call_handler.py +1234567890 +0987654321 +1122334455")
        print("\nNote: Phone numbers must be in E.164 format (e.g., +1234567890)")
        print("\n⚠️  Make sure the agent worker is running:")
        print("     python agent.py dev")
        sys.exit(1)

    phone_numbers = sys.argv[1:]

    # Validate phone numbers
    for phone in phone_numbers:
        if not phone.startswith("+"):
            print(f"❌ Invalid phone number format: {phone}")
            print("   Phone numbers must start with + and include country code")
            print("   Example: +1234567890")
            sys.exit(1)

    if len(phone_numbers) == 1:
        asyncio.run(make_single_call(phone_numbers[0]))
    else:
        asyncio.run(make_bulk_calls(phone_numbers))


if __name__ == "__main__":
    main()
