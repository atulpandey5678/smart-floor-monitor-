"""AI Chat Engine with Anthropic Tool Calling."""

import logging
import json
from datetime import datetime, timedelta
import anthropic

from config import CLAUDE_API_KEY

logger = logging.getLogger(__name__)

# Define the tools Claude can use
_TOOLS = [
    {
        "name": "get_recent_alerts",
        "description": "Retrieves recent factory alerts from the database. Use this to answer questions about what went wrong, downtime, or machine issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of past days to fetch alerts for (e.g., 1 for today/yesterday, 7 for past week)."
                }
            },
            "required": ["days"]
        }
    },
    {
        "name": "get_unresolved_alerts",
        "description": "Retrieves all current alerts that have NOT been resolved yet. Use this when the user asks about current problems or what needs attention right now.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "resolve_alert",
        "description": "Resolves a specific alert in the database and tags it with a root cause.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "integer",
                    "description": "The ID of the alert to resolve."
                },
                "root_cause": {
                    "type": "string",
                    "description": "The root cause of the alert. Must be one of: 'Tool Breakage', 'Material Shortage', 'Quality Issue', 'Operator Absence', 'Maintenance', 'Other'."
                }
            },
            "required": ["alert_id", "root_cause"]
        }
    },
    {
        "name": "update_system_setting",
        "description": "Updates a configuration setting in the system. Use this when the user asks to change a threshold, grace period, or shift hours.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "The settings section (e.g., 'detection', 'shifts', 'notifications')."
                },
                "key": {
                    "type": "string",
                    "description": "The specific setting key to update (e.g., 'person_confidence_threshold', 'grace_period_seconds')."
                },
                "value": {
                    "type": "number",
                    "description": "The new numeric value to set."
                }
            },
            "required": ["section", "key", "value"]
        }
    }
]

async def handle_chat_message(messages: list, repo) -> str:
    """Send conversation history to Claude and execute any tool calls."""
    if not CLAUDE_API_KEY or not CLAUDE_API_KEY.startswith("sk-ant"):
        return "The AI Chat feature is currently disabled because the CLAUDE_API_KEY in the .env file is missing or invalid."

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        
        # Prepare system prompt
        system_prompt = (
            f"You are a helpful and expert AI assistant for the Cologic Shop Floor Tracker. "
            f"Today's date and time is {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
            f"When users ask about factory metrics, alerts, or machine issues, use your available tools "
            f"to query the database. You can also MUTATE the database if requested (e.g. resolve alerts, update settings). "
            f"When you make a change, always confirm to the user what action you just took. "
            f"Keep your answers concise, professional, and easy to understand "
            f"for a non-technical factory supervisor. Do not show raw JSON to the user."
        )

        # 1. Send the user's message and history to Claude, providing the tools
        response = await client.messages.create(
            model="claude-sonnet-5",
            max_tokens=500,
            system=system_prompt,
            tools=_TOOLS,
            messages=messages
        )

        # 2. Check if Claude wants to use a tool
        if response.stop_reason == "tool_use":
            tool_use = next(block for block in response.content if block.type == "tool_use")
            tool_name = tool_use.name
            tool_args = tool_use.input
            
            logger.info("Claude requested tool: %s with args: %s", tool_name, tool_args)
            
            tool_result_content = ""
            if tool_name == "get_recent_alerts":
                days = tool_args.get("days", 1)
                cutoff = datetime.now() - timedelta(days=days)
                # Query DB
                rows = await repo.db.fetch_all(
                    "SELECT alert_type, machine_id, message, created_at, resolved, root_cause "
                    "FROM alerts WHERE created_at >= ? ORDER BY created_at DESC LIMIT 50",
                    (cutoff.isoformat(),)
                )
                tool_result_content = json.dumps([dict(r) for r in rows]) if rows else "No alerts found."

            elif tool_name == "get_unresolved_alerts":
                rows = await repo.db.fetch_all(
                    "SELECT id, alert_type, machine_id, message, created_at "
                    "FROM alerts WHERE resolved = 0 ORDER BY created_at DESC"
                )
                tool_result_content = json.dumps([dict(r) for r in rows]) if rows else "No unresolved alerts."

            elif tool_name == "resolve_alert":
                alert_id = tool_args.get("alert_id")
                root_cause = tool_args.get("root_cause")
                success = await repo.resolve_alert(alert_id, root_cause)
                tool_result_content = f"Success: Alert {alert_id} resolved with cause '{root_cause}'." if success else f"Error: Failed to resolve Alert {alert_id}."

            elif tool_name == "update_system_setting":
                section = tool_args.get("section")
                key = tool_args.get("key")
                value = tool_args.get("value")
                from engine.settings_manager import get_settings
                settings = get_settings()
                if settings:
                    await settings.set(section, key, value)
                    tool_result_content = f"Success: Updated setting {section}.{key} to {value}."
                else:
                    tool_result_content = "Error: SettingsManager not initialized."

            else:
                tool_result_content = f"Error: Unknown tool {tool_name}"

            # 3. Append Claude's tool request and the tool result to the message history
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": tool_result_content,
                    }
                ],
            })

            # 4. Get the final answer from Claude
            final_response = await client.messages.create(
                model="claude-sonnet-5",
                max_tokens=500,
                system=system_prompt,
                tools=_TOOLS,
                messages=messages
            )
            return final_response.content[0].text

        else:
            # No tool was called, Claude just answered directly
            # The text block is the first element of content
            text_block = next((block for block in response.content if block.type == "text"), None)
            return text_block.text if text_block else "I could not process that request."

    except Exception as e:
        logger.error("AI Chat Error: %s", e)
        return "I'm sorry, I encountered an internal error while trying to answer your question."
