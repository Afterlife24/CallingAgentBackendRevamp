# Call Tools Comparison

## Three Ways to Make Calls

### 1. call_handler.py (Recommended) ⭐

**Best for:** Production use, testing, bulk calls

```bash
# Single call
python call_handler.py +1234567890

# Bulk calls
python call_handler.py +1234567890 +0987654321 +1122334455
```

**Features:**

- ✅ CLI interface - no code editing needed
- ✅ Phone number validation
- ✅ Single or bulk calls
- ✅ Nice formatted output
- ✅ Error handling and logging
- ✅ Uses dispatcher.py for clean code

**Output:**

```
✅ Call initiated successfully!
   Room: outbound-1234567890
   Phone: +1234567890
   Dispatch ID: abc123

💡 The agent worker will pick up this dispatch and place the call.
```

---

### 2. dispatcher.py (Library)

**Best for:** Integration with FastAPI, custom applications

```python
from dispatcher import OutboundCallDispatcher

dispatcher = OutboundCallDispatcher()

# Single call
result = await dispatcher.make_call("+1234567890")

# Bulk calls
results = await dispatcher.make_bulk_calls([
    "+1234567890",
    "+0987654321"
], delay_between_calls=2.0)
```

**Features:**

- ✅ Reusable class
- ✅ Proper error handling
- ✅ Session management (no memory leaks)
- ✅ Structured response format
- ✅ Bulk calling with delays
- ✅ Ready for FastAPI integration

**Response Format:**

```python
{
    "success": True,
    "room_name": "outbound-1234567890",
    "dispatch_id": "abc123",
    "phone_number": "+1234567890"
}
```

---

### 3. make_call.py (Simple)

**Best for:** Quick testing, learning

```bash
# Edit the file to change phone number
python make_call.py
```

**Features:**

- ✅ Simple and easy to understand
- ✅ Good for learning the basics
- ⚠️ Need to edit code to change number
- ⚠️ No bulk calling
- ⚠️ Basic error handling

---

## Recommended Workflow

### For Development/Testing

```bash
# Terminal 1: Start agent
python agent.py dev

# Terminal 2: Make calls
python call_handler.py +1234567890
```

### For Production (with FastAPI)

1. Keep `dispatcher.py` as your call management library
2. Create FastAPI endpoints that use `OutboundCallDispatcher`
3. Use `call_handler.py` for manual testing/admin tasks

Example FastAPI integration:

```python
from fastapi import FastAPI
from dispatcher import OutboundCallDispatcher

app = FastAPI()
dispatcher = OutboundCallDispatcher()

@app.post("/call")
async def make_call(phone_number: str):
    result = await dispatcher.make_call(phone_number)
    return result
```

---

## Migration Path

If you're currently using `make_call.py`:

1. **Start using call_handler.py** for manual calls
   - No code editing needed
   - Better error messages
   - Supports bulk calls

2. **Use dispatcher.py** when building APIs
   - Import it in your FastAPI app
   - Reusable across endpoints
   - Clean separation of concerns

3. **Keep make_call.py** as a simple example
   - Good for documentation
   - Easy to understand for beginners

---

## Summary

| Tool                | Use Case                  | Complexity | Features   |
| ------------------- | ------------------------- | ---------- | ---------- |
| **call_handler.py** | CLI testing, manual calls | Low        | ⭐⭐⭐⭐⭐ |
| **dispatcher.py**   | API integration, library  | Medium     | ⭐⭐⭐⭐⭐ |
| **make_call.py**    | Learning, quick tests     | Very Low   | ⭐⭐⭐     |

**Recommendation:** Use `call_handler.py` for day-to-day work, and `dispatcher.py` when building your FastAPI server.
