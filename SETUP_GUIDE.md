# Setup Guide - What Was Fixed

## Issues Found & Fixed

### 1. Simplified to Google Realtime API ✅

**Problem**: Original code used separate STT/LLM/TTS components

**Fixed**: Using Google Realtime API which handles everything:

```python
session = AgentSession(
    llm=google.beta.realtime.RealtimeModel(voice="Aoede")
)
```

### 2. Updated Dependencies ✅

**Fixed**: Simplified `requirements.txt`:

```
livekit-plugins-google~=1.0  # Only need Google plugin
```

### 3. Incorrect Metadata Format ✅

**Problem**: `make_call.py` sent phone number as plain string, but `agent.py` expected JSON object

**Fixed**: Changed `make_call.py` to send proper JSON:

```python
metadata = json.dumps({"phone_number": phone_number})
```

### 4. Wrong Call Pattern ✅

**Problem**: `make_call.py` was trying to create SIP participant directly, which is the agent's job

**Fixed**: Now follows official LiveKit pattern:

1. Create agent dispatch with metadata
2. Agent picks up dispatch
3. Agent creates SIP participant and dials

### 5. Simplified Environment Variables ✅

**Problem**: `.env.local` required multiple API keys

**Fixed**: Now only needs:

- `GOOGLE_API_KEY` - Single API for voice AI
- `SIP_OUTBOUND_TRUNK_ID` - For phone calls

## What You Need to Do

### Step 1: Install Dependencies

```bash
cd out_bound_calls
source .venv/bin/activate  # or create new venv
pip install -r requirements.txt
```

### Step 2: Get API Keys

1. **Google Gemini API** (Realtime Voice AI)
   - Get free API key at https://aistudio.google.com/apikey
   - Add to `.env.local`: `GOOGLE_API_KEY="..."`

   ✨ **That's it!** Google Realtime API handles STT, LLM, and TTS all in one.

### Step 3: Configure SIP Trunk

```bash
# Install LiveKit CLI
brew install livekit-cli  # macOS
# or: https://github.com/livekit/livekit-cli/releases

# List your SIP trunks
lk sip outbound list

# Copy the trunk ID (starts with ST_) to .env.local
```

### Step 4: Test the Agent

```bash
# Terminal 1: Start the agent worker
python agent.py start

# Terminal 2: Make a test call
python make_call.py
```

## Key Differences from Reference Implementation

Your main project (`out_bound_calls`) is now a **working minimal implementation**.

The reference project (`Livekit_outbound`) has additional features:

- FastAPI REST API server
- MongoDB transcript storage
- Dispatcher module for better call management
- Worker lifecycle management
- Bulk calling support

## Next Steps (Optional)

If you want the full-featured version, you can add:

1. **FastAPI Server** - For REST API endpoints
2. **MongoDB** - For storing call transcripts
3. **Dispatcher Module** - Better call management
4. **Custom Instructions** - Update agent for VisionIT sales (currently dental scheduling)

## Testing Without Making Calls

```bash
# Console mode - interact via text
python agent.py console
```

This lets you test the agent logic without using phone credits.

## Common Issues

### "Module not found: deepgram"

→ Run: `pip install livekit-plugins-deepgram`

### "SIP trunk not found"

→ Check your `SIP_OUTBOUND_TRUNK_ID` in `.env.local`

### "Agent not picking up dispatch"

→ Make sure agent worker is running in another terminal

### "Call fails immediately"

→ Check phone number format: must include country code (+1234567890)

## Architecture Overview

```
┌─────────────────┐
│  make_call.py   │  Creates dispatch with phone number
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ LiveKit Cloud   │  Routes dispatch to agent worker
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   agent.py      │  Picks up dispatch, creates SIP call
│  (entrypoint)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  SIP Trunk      │  Dials phone number
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Phone Call     │  Voice conversation with AI
└─────────────────┘
```

## Resources

- **LiveKit Docs**: https://docs.livekit.io/agents/
- **Telephony Guide**: https://docs.livekit.io/agents/start/telephony
- **SIP Setup**: https://docs.livekit.io/sip/quickstarts/configuring-sip-trunk/
- **Examples**: https://github.com/livekit/agents/tree/main/examples
