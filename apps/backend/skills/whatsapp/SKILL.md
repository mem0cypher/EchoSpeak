WhatsApp messaging bridge for EchoSpeak.

## When to use

When the user asks to:
- "Send a WhatsApp message to John"
- "Read my recent WhatsApp messages"
- "What WhatsApp messages did I get?"
- "List my WhatsApp chats"
- "Message Mom on WhatsApp saying I'll be late"

## Tool reference

### whatsapp_send
Send a WhatsApp message to a contact by phone number or chat name. Requires the recipient's phone number (with country code) or chat name. This is an action tool — requires confirmation.

### whatsapp_read_recent
Read recent messages from a specific chat or across all chats. Returns sender, content, and timestamp.

### whatsapp_list_chats
List recent WhatsApp chats with last message preview, unread count, and chat name.

## Requirements

Set `ALLOW_WHATSAPP=true` and `WHATSAPP_API_URL` (URL of the whatsapp-web.js bridge server). The bridge must be authenticated via QR code separately.

## Architecture note

This skill communicates with a whatsapp-web.js bridge server (Node.js) running alongside EchoSpeak. The bridge handles WhatsApp Web authentication and provides a REST API. EchoSpeak tools call this REST API.

## Output style

Keep WhatsApp message summaries concise. Show sender name, time, and message content. Use 📱 emoji for WhatsApp context.
