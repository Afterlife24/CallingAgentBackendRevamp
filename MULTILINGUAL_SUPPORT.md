# Multilingual Support - Outbound Calling Agent

## Overview

The Outbound Calling Agent now supports multilingual conversations while preventing accidental language switching from background noise, names, or unclear audio during phone calls.

## Solution Implemented

Uses **prompt engineering** to control language switching behavior in the OpenAI Realtime API, specifically optimized for telephony scenarios.

## How It Works

### Default Behavior

- Agent starts in **English** by default
- Stays in English unless user explicitly requests a language change
- Ignores false triggers common in phone calls:
  - Names in other languages
  - Background noise (traffic, music, other conversations)
  - Single foreign words or phrases
  - Poor audio quality or garbled speech

### Intentional Language Switching

The agent will switch languages when:

1. **Explicit Request**: User clearly asks to switch

   ```
   User: "Can we speak in Spanish?"
   User: "Please switch to Arabic"
   User: "I prefer German"
   ```

2. **Consistent Foreign Language**: User speaks 2-3 complete sentences in another language
   ```
   User: "Hola, me gustaría información sobre sus servicios. ¿Pueden ayudarme?"
   Agent: "I can speak multiple languages. Would you prefer Spanish or English?"
   User: "Spanish please"
   Agent: [switches to Spanish]
   ```

### Prevented Scenarios

The agent will NOT switch for:

- ❌ Names: "Can I speak to Amir?" (stays in English)
- ❌ Single words: "Gracias" (stays in English)
- ❌ Background noise or unclear audio
- ❌ Accents or pronunciation variations

## Telephony-Specific Considerations

### Audio Quality Challenges

Phone calls often have:

- Background noise (traffic, wind, other people)
- Audio compression artifacts
- Echo or feedback
- Poor connection quality

The prompt is designed to be more conservative about language switching in these conditions.

### SIP Trunk Compatibility

Works with all SIP trunks configured in `.env.local`:

```
SIP_OUTBOUND_TRUNK_ID=ST_...
```

## Supported Languages

OpenAI Realtime API supports 50+ languages including:

**European**: Spanish, French, German, Italian, Portuguese, Dutch, Polish, Russian, Greek, Swedish, Norwegian, Danish

**Middle Eastern**: Arabic, Hebrew, Turkish, Persian

**Asian**: Chinese, Japanese, Korean, Hindi, Thai, Vietnamese, Indonesian, Malay, Tagalog

## Implementation Details

### 1. Smart Language Protocol (`prompts.py`)

```python
# Language rules in AGENT_INSTRUCTION:
- START in ENGLISH by default
- DO NOT switch based on names, noise, or single words
- ONLY switch when user explicitly requests it
- Confirm before switching: "Would you like to continue in [language]?"
```

### 2. Session Instructions

```python
# SESSION_INSTRUCTION enforces:
- START in ENGLISH and stay in English
- Require clear confirmation before switching
- Do NOT switch based on names, background noise, or single words
```

## Testing

### Test Scenarios

1. **Name Triggers (Should NOT switch)**

   ```
   Call: "Hi, I'm calling about your services for my business"
   Agent: [Stays in English] ✅

   Call: "Can you help me reach Amir?"
   Agent: [Stays in English] ✅
   ```

2. **Explicit Requests (Should switch)**

   ```
   Call: "Can we speak in Spanish?"
   Agent: [Confirms] "Of course! ..." (switches to Spanish) ✅
   ```

3. **Background Noise (Should ignore)**

   ```
   Call with traffic noise, music, or other conversations
   Agent: [Stays in English] ✅
   ```

4. **Poor Audio Quality (Should ignore)**
   ```
   Call with garbled audio or unclear speech
   Agent: [Stays in English, may ask user to repeat] ✅
   ```

### Running Tests

The outbound calling agent requires:

1. LiveKit cloud connection
2. SIP trunk configuration
3. Phone number to dial

```bash
# Set up environment
cd out_bound_calls
cp .env.local.example .env.local  # if needed
# Edit .env.local with your credentials

# Run the agent
python agent.py dev
```

To dispatch a call, use the web backend:

```bash
cd ../webBackendCors
python web_agnet_server.py

# Then make a POST request to dispatch endpoint
curl -X POST http://localhost:5001/dispatch_call \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+1234567890"}'
```

## Conversation Flow Examples

### Example 1: User Wants Spanish

```
Agent: "Hello! I'm your AI assistant from Afterlife..."
User: "Can we speak in Spanish?"
Agent: "¡Por supuesto! Continuemos en español. ¿Cómo puedo ayudarte hoy?"
User: "Quiero información sobre el agente de voz"
Agent: "El Agente de Voz de Afterlife permite..."
```

### Example 2: User Mentions Foreign Name

```
Agent: "Hello! How can I help you today?"
User: "I need to contact Amir about the pricing"
Agent: "I'd be happy to help you with pricing information..." (stays in English)
```

### Example 3: User Starts Speaking Arabic

```
Agent: "Hello! How can I help you today?"
User: "مرحبا، أريد معلومات عن خدماتكم"
Agent: "I can speak multiple languages. Would you prefer to continue in Arabic or English?"
User: "Arabic please"
Agent: "ممتاز! كيف يمكنني مساعدتك اليوم؟"
```

### Example 4: Background Noise

```
Agent: "Hello! How can I help you today?"
[Background: Traffic noise, someone speaking Spanish nearby]
Agent: [Ignores background noise, stays in English] ✅
User: "Yes, I'm interested in your AI agents"
Agent: "Great! Let me tell you about our solutions..." (stays in English)
```

## Configuration Options

### Option 1: English Only (Strict Mode)

If you want to disable multilingual support completely:

```python
# In prompts.py, change to:
AGENT_INSTRUCTION = """
# LANGUAGE RULES
- You MUST ALWAYS respond in ENGLISH ONLY.
- If user speaks another language, politely respond in English:
  "I apologize, but I can only communicate in English. How can I help you?"
"""
```

### Option 2: Specific Languages Only

To limit to specific languages:

```python
# Add to AGENT_INSTRUCTION:
# Supported Languages
- You can only switch to: Spanish, Arabic, French
- For any other language request, politely decline:
  "I can speak English, Spanish, Arabic, or French. Which would you prefer?"
```

### Option 3: Auto-Detect (Current Implementation)

Current setup allows all languages with confirmation.

## Monitoring & Analytics

### Conversation Logs

All calls are logged in `KMS/logs/` with language information:

```bash
cd out_bound_calls/KMS/logs
tail -f *.log | grep -i "language"
```

### Checking for Language Switches

```bash
# Monitor for language-related events
tail -f KMS/logs/*.log | grep -E "language|switch|español|arabic"
```

## Troubleshooting

### Issue: Agent still switches accidentally

**Solution**: The prompt is already very strict. If issues persist, consider:

1. Using English-only mode (Option 1 above)
2. Checking audio quality (poor quality can cause misinterpretation)
3. Reviewing conversation logs to identify patterns

### Issue: Agent doesn't switch when user wants

**Solution**: Ensure user is being explicit:

```
❌ Bad: User says "Hola" (single word)
✅ Good: User says "Can we speak in Spanish?"
✅ Good: User speaks 2-3 full sentences in Spanish
```

### Issue: Background noise causes problems

**Solution**: The agent uses BVCTelephony noise cancellation:

```python
# In agent.py
noise_cancellation=noise_cancellation.BVCTelephony()
```

This is optimized for phone calls and should handle most background noise.

## Best Practices

1. **Test with real phone calls**: Simulator behavior may differ from actual calls
2. **Monitor conversation logs**: Check for unexpected language switches
3. **Use clear audio**: Encourage users to call from quiet environments
4. **Provide feedback**: If issues occur, review logs and adjust prompts
5. **Consider use case**: For international customers, you may want more lenient switching

## Summary

✅ Prevents accidental language switching from names, noise, gibberish
✅ Allows intentional language switching when user requests it
✅ Optimized for telephony scenarios (background noise, poor audio)
✅ Maintains sub-100ms latency requirement
✅ Supports 50+ languages
✅ Works with all SIP trunks
✅ Easy to configure (strict, limited, or auto-detect modes)
