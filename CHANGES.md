# Agent Updates - Using Afterlife Instructions

## What Changed

Your `out_bound_calls/agent.py` has been updated to use the **Afterlife** business instructions from `prompts.py`.

### Before (Dental Appointment Scheduling)

- Agent was a "scheduling assistant for a dental practice"
- Confirmed appointments for patients
- Had appointment-specific tools (look_up_availability, confirm_appointment)
- Hardcoded patient name and appointment time

### After (Afterlife AI Agent Sales)

- Agent is an "AI Business Assistant representing Afterlife"
- Promotes AI agent solutions (Telecalling, Web, WhatsApp agents)
- Focuses on understanding business needs and recommending solutions
- Dynamic instructions from prompts.py

## Changes Made

### 1. Added Import

```python
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
```

### 2. Simplified Agent Class

```python
class OutboundCaller(Agent):
    def __init__(self, *, dial_info: dict[str, Any]):
        # Instructions now come from prompts.py
        super().__init__(
            instructions=AGENT_INSTRUCTION  # ← From prompts.py
        )
```

**Removed parameters:**

- `name` - No longer needed
- `appointment_time` - No longer needed

### 3. Removed Appointment Tools

**Removed:**

- `look_up_availability()` - Not relevant for Afterlife
- `confirm_appointment()` - Not relevant for Afterlife

**Kept:**

- `transfer_call()` - Still useful for escalation
- `end_call()` - Essential for call management
- `detected_answering_machine()` - Essential for call management

### 4. Updated Initial Greeting

```python
# Now uses SESSION_INSTRUCTION from prompts.py
await session.generate_reply(
    instructions=SESSION_INSTRUCTION,
    allow_interruptions=True,
)
```

**Greeting will now say:**

> "Hello! I'm your AI assistant from Afterlife. We help businesses automate customer interactions with intelligent AI agents. How can I help you today?"

## Current Agent Behavior

Your agent will now:

1. **Introduce Afterlife** - Explain what Afterlife does
2. **Understand Business Needs** - Ask about customer communication channels
3. **Recommend Solutions** - Suggest appropriate AI agents:
   - Telecalling Agent (for phone automation)
   - Web Agent (for website interaction)
   - WhatsApp Agent (for WhatsApp automation)
4. **Explain Benefits** - Focus on time savings, lead generation, cost reduction
5. **Be Consultative** - Not pushy, solution-oriented approach

## Testing

```bash
# Terminal 1: Start agent
python agent.py dev

# Terminal 2: Make a test call
python call_handler.py +1234567890
```

The agent will now follow the Afterlife script from prompts.py!

## Customizing Instructions

To change agent behavior, edit `prompts.py`:

- **AGENT_INSTRUCTION** - Main agent personality and behavior
- **SESSION_INSTRUCTION** - Initial greeting and session context

No need to touch `agent.py` anymore for instruction changes!

## Reverting to VisionIT

If you want to go back to VisionIT (selling refurbished computers), update `prompts.py` with the VisionIT instructions instead of Afterlife instructions.
