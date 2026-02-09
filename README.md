# VisionIT Outbound Voice Agent

AI-powered outbound calling system for VisionIT using LiveKit Agents framework.

## Features

- Automated outbound calling via SIP
- Voice AI assistant for appointment scheduling
- Call transfer to human agents
- Answering machine detection
- Real-time conversation with STT/LLM/TTS pipeline

## Prerequisites

1. **LiveKit Cloud Account** - Sign up at [livekit.io](https://livekit.io)
2. **SIP Trunk Configuration** - Configure outbound SIP trunk in LiveKit Cloud
3. **Google Gemini API Key** - Get free key from [Google AI Studio](https://aistudio.google.com/apikey)

## Setup

### 1. Install Dependencies

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On macOS/Linux

# Install packages
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Edit `.env.local` and add your actual API keys:

```bash
# LiveKit Configuration (already set)
LIVEKIT_API_KEY="your-key"
LIVEKIT_API_SECRET="your-secret"
LIVEKIT_URL="wss://your-project.livekit.cloud"

# Google Gemini API Key (handles STT/LLM/TTS)
GOOGLE_API_KEY="..."

# SIP Trunk ID (get from: lk sip outbound list)
SIP_OUTBOUND_TRUNK_ID="ST_..."
```

### 3. Get Your SIP Trunk ID

```bash
# Install LiveKit CLI
brew install livekit-cli  # macOS
# or download from: https://github.com/livekit/livekit-cli

# List your outbound trunks
lk sip outbound list
```

Copy the trunk ID (starts with `ST_`) to your `.env.local` file.

## Usage

### Run the Agent Worker

Start the LiveKit agent worker in development mode:

```bash
python agent.py dev
```

The agent will connect to LiveKit and wait for dispatch requests.

### Make an Outbound Call

In a separate terminal (with the agent running):

**Option 1: Using call_handler.py (Recommended)**

```bash
# Single call
python call_handler.py +1234567890

# Multiple calls
python call_handler.py +1234567890 +0987654321 +1122334455
```

**Option 2: Using make_call.py (Simple)**

```bash
# Edit make_call.py to set your target phone number
# Then run:
python make_call.py
```

## How It Works

1. **Dispatch**: `call_handler.py` or `make_call.py` creates an agent dispatch with phone number metadata
2. **Agent Pickup**: The running agent worker picks up the dispatch
3. **SIP Call**: Agent creates a SIP participant and dials the phone number
4. **Conversation**: When answered, the voice AI starts the conversation
5. **Completion**: Call ends when user hangs up or agent calls `end_call()`

## Architecture

```
call_handler.py → dispatcher.py → Agent Dispatch → agent.py entrypoint()
                                                        ↓
                                                create_sip_participant()
                                                        ↓
                                                Dial Phone Number
                                                        ↓
                                                Start AgentSession
                                                        ↓
                                                Voice Conversation
```

## Project Files

- **agent.py** - Voice AI agent with conversation logic
- **dispatcher.py** - Call management and dispatch handling
- **call_handler.py** - CLI tool for making calls
- **make_call.py** - Simple script for testing
- **prompts.py** - Agent instructions and prompts

## Customization

### Change Agent Instructions

Edit the `instructions` parameter in `agent.py`:

```python
class OutboundCaller(Agent):
    def __init__(self, ...):
        super().__init__(
            instructions="Your custom instructions here..."
        )
```

### Add Function Tools

Add custom tools using the `@function_tool()` decorator:

```python
@function_tool()
async def my_custom_tool(self, ctx: RunContext, param: str):
    """Tool description for the LLM"""
    # Your logic here
    return "result"
```

### Change Voice

Modify the voice in `entrypoint()`:

```python
session = AgentSession(
    llm=google.beta.realtime.RealtimeModel(
        voice="Aoede",  # Options: Puck, Charon, Kore, Fenrir, Aoede
    )
)
```

Available Google Realtime voices:

- **Aoede** - Female voice (default)
- **Puck** - Male voice
- **Charon** - Male voice
- **Kore** - Female voice
- **Fenrir** - Male voice

## Troubleshooting

### "SIP_OUTBOUND_TRUNK_ID is not set or invalid"

- Make sure your trunk ID starts with `ST_`
- Verify it's set in `.env.local`

### "error creating SIP participant"

- Check SIP trunk is configured in LiveKit Cloud
- Verify phone number format (include country code: +1234567890)
- Check LiveKit Cloud logs for SIP errors

### Agent not picking up dispatch

- Ensure agent worker is running (`python agent.py dev`)
- Check agent name matches in both files (default: "outbound-caller")
- Verify LiveKit credentials are correct

## Testing

Test in console mode without making actual calls:

```bash
python agent.py console
```

This lets you interact with the agent via text in your terminal.

## Next Steps

- Add MongoDB for call transcript storage
- Create FastAPI server for REST API endpoints
- Implement bulk calling functionality
- Add CRM integration (HubSpot, Salesforce, etc.)

## Resources

- [LiveKit Agents Documentation](https://docs.livekit.io/agents/)
- [LiveKit Telephony Guide](https://docs.livekit.io/agents/start/telephony)
- [SIP Configuration Guide](https://docs.livekit.io/sip/quickstarts/configuring-sip-trunk/)
