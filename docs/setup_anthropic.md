# Anthropic API Reference

Main steps are in `PHASE_0_CHECKLIST.md`. This file has context.

## Important: Pro subscription ≠ API

Your **Claude Pro** subscription (claude.ai) and the **Anthropic API** (console.anthropic.com) are separate products with separate billing. Pro doesn't give you API access. You need a small API credit balance.

## Expected monthly cost

At 20-25 emails/day:

| Task | Daily cost |
|---|---|
| Classification (Phase 3) | ~$0.20 |
| Owner discovery (Phase 4) | ~$0.30 |
| Email drafting (Phase 5) | ~$0.10 |

**Total: ~$18/month** at full volume. $10 in credits lasts ~2 weeks at startup phase.

## Auto-reload (optional but recommended)

- Console → **Plans & Billing → Auto-reload**
- Reload $10 when balance drops below $2
- Prevents mid-day stoppages

## Test your key works

After `pip install anthropic`:

```python
from anthropic import Anthropic
import os
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()
resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=50,
    messages=[{"role": "user", "content": "Say hi in 5 words."}]
)
print(resp.content[0].text)
```

If it prints a short greeting, key works.

## Gotchas

- **Model name `claude-haiku-4-5` may change.** If you get a model-not-found error, check the list at https://docs.anthropic.com/en/docs/about-claude/models and use the latest Haiku name.
- **Free tier is 5 RPM.** Once you top up any amount you're on paid tier with higher limits.
- **Key is secret.** Never commit. Never paste in chat. If you think it leaked, revoke and regenerate immediately.
