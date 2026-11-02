from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, date, timedelta
from calendar import monthrange
from collections import defaultdict, deque
import time
import json
import re
import difflib

import anyio
import httpx

from app.database import get_db
from app.config import settings
from app.utils.dependencies import get_current_user, apply_company_scope, is_platform_admin
from app.models.core.user import User
from app.models.core.enums import TaskPriority
from app.models.core.team import Team
from app.models.core.team_membership import TeamMembership
from app.models.sales.audit import AuditLog
from app.models.sales.ai_conversation import AIConversation
from app.models.sales.lead import Lead
from app.models.sales.client import Client
from app.models.sales.task import Task
from app.models.finance.invoice import Invoice
from app.models.finance.ledger import LedgerEntry
from app.routers.finance.ledgers import ALL_LEDGERS, get_ledger_columns
from app.schemas.ai import AIChatRequest, AIChatResponse, AIExecutedAction, AIParamsResponse, AIRequestParams, AIResolvedParams
from app.utils.helpers import normalize_email, normalize_phone
from app.utils.audit import log_activity
from app.routers.ai import assistant_crm_actions as aca


router = APIRouter()
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
# Same mutating tools as MD/manager; admins manage the tenant and expect parity with other admin surfaces.
COMMAND_ROLES = {"admin", "md", "manager"}
AI_PARAM_OVERRIDE_ROLES = {"admin", "md", "manager"}
READ_ONLY_ACTIONS = {
    "monthly_best_sales_exec",
    "revenue_summary",
    "business_snapshot",
    "team_performance_summary",
    "get_best_manager",
    "list_teams",
    "list_company_users",
    "list_tasks",
}
COMMAND_ACTIONS = {
    "create_team",
    "delete_team",
    "add_team_member",
    "remove_team_member",
    "create_ledger_entry",
    "create_top_performing_team",
    "create_team_with_members",
    "create_task",
    "update_task",
    "complete_task",
    "delete_task",
}
# Sales reps: task tools on their own scope (no team/ledger admin commands).
SALES_AI_EXTRA = frozenset(
    {
        "update_task",
        "complete_task",
        "delete_task",
    }
)
ALLOWED_GEMINI_MODELS = {"gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"}
# User requested GPT-5.3 Codex; allow it when OPENAI_KEY is configured.
ALLOWED_OPENAI_MODELS = {"gpt-5.3-codex"}


def _extract_first_json_object(text: str) -> dict:
    """
    Best-effort extraction of a JSON object from model output.
    """
    # Find first {...} block (naive but effective with strict prompting)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object found in model output")
    return json.loads(m.group(0))


def _server_default_ai_params() -> AIResolvedParams:
    if settings.OPENAI_KEY:
        model = (settings.OPENAI_MODEL or "gpt-5.3-codex").strip() or "gpt-5.3-codex"
    else:
        model = (settings.GEMINI_MODEL or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    try:
        max_actions = max(1, int(settings.AI_MAX_ACTIONS_PER_REQUEST))
    except (TypeError, ValueError):
        max_actions = 5
    return AIResolvedParams(
        model=model,
        temperature=0.2,
        max_output_tokens=1024,
        max_actions=max_actions,
    )


def _resolve_ai_params(current_user: User, requested: AIRequestParams | None) -> AIResolvedParams:
    resolved = _server_default_ai_params()
    if requested is None:
        return resolved

    has_override = any(
        value is not None
        for value in [
            requested.model,
            requested.temperature,
            requested.max_output_tokens,
            requested.max_actions,
        ]
    )
    if has_override and current_user.role not in AI_PARAM_OVERRIDE_ROLES:
        raise HTTPException(status_code=403, detail="You are not allowed to override AI parameters")

    if requested.model is not None:
        model = requested.model.strip()
        allowed_models = set()
        if settings.OPENAI_KEY:
            allowed_models.update(ALLOWED_OPENAI_MODELS)
        if settings.GEMINI_API_KEY:
            allowed_models.update(ALLOWED_GEMINI_MODELS)
        if not allowed_models:
            raise HTTPException(status_code=503, detail="No AI provider configured (set OPENAI_KEY or GEMINI_API_KEY)")
        if model not in allowed_models:
            allowed = ", ".join(sorted(allowed_models))
            raise HTTPException(status_code=400, detail=f"Unsupported model '{model}'. Allowed: {allowed}")
        resolved.model = model

    if requested.temperature is not None:
        resolved.temperature = float(requested.temperature)

    if requested.max_output_tokens is not None:
        resolved.max_output_tokens = int(requested.max_output_tokens)

    if requested.max_actions is not None:
        requested_max_actions = int(requested.max_actions)
        if requested_max_actions > resolved.max_actions:
            raise HTTPException(
                status_code=400,
                detail=f"'max_actions' cannot exceed server limit ({resolved.max_actions})",
            )
        resolved.max_actions = requested_max_actions

    return resolved


async def _gemini_plan(prompt: str, ai_params: AIResolvedParams) -> dict:
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ai_params.model}:generateContent"
    headers = {"x-goog-api-key": settings.GEMINI_API_KEY, "content-type": "application/json"}

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": ai_params.temperature,
            "maxOutputTokens": ai_params.max_output_tokens,
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Gemini error: {r.status_code}")
        data = r.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected Gemini response format")

    try:
        return _extract_first_json_object(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI output was not valid JSON: {e}")


def _extract_openai_output_text(payload: dict) -> str:
    # Prefer the convenience field if present.
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]
    # Fallback: scan outputs.
    out = payload.get("output") or []
    if isinstance(out, list):
        parts: list[str] = []
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or []
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        if parts:
            return "\n".join(parts)
    raise ValueError("Unexpected OpenAI response format (no output text)")


async def _openai_plan(prompt: str, ai_params: AIResolvedParams) -> dict:
    if not settings.OPENAI_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_KEY not configured")

    url = "https://api.openai.com/v1/responses"
    headers = {"authorization": f"Bearer {settings.OPENAI_KEY}", "content-type": "application/json"}
    body = {
        "model": ai_params.model,
        "input": prompt,
        # Keep responses JSON-only; we still defensively extract JSON below.
        "text": {"format": {"type": "json_object"}},
        "temperature": ai_params.temperature,
        "max_output_tokens": ai_params.max_output_tokens,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise HTTPException(status_code=502, detail=f"OpenAI error: {r.status_code}{(' - ' + detail) if detail else ''}")
        data = r.json()

    try:
        text = _extract_openai_output_text(data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Unexpected OpenAI response format: {e}")

    try:
        return _extract_first_json_object(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI output was not valid JSON: {e}")


async def _ai_plan(prompt: str, ai_params: AIResolvedParams) -> dict:
    # Prefer OpenAI when configured (user requested GPT-5.3 Codex).
    if settings.OPENAI_KEY:
        return await _openai_plan(prompt, ai_params)
    return await _gemini_plan(prompt, ai_params)


def _audit(db: Session, user: User, action: str, entity_type: str, entity_id: str | None, entity_name: str | None, before: dict | None, after: dict | None):
    db.add(
        AuditLog(
            company_id=user.company_id,
            admin_id=user.id,
            admin_name=user.full_name,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            before_value=json.dumps(before) if before is not None else None,
            after_value=json.dumps(after) if after is not None else None,
        )
    )


def _ensure_company_user(user: User):
    if is_platform_admin(user) or user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")


def _allowed_actions_for_role(role: str) -> set[str]:
    allowed = set(READ_ONLY_ACTIONS)
    r = _role_value(role)
    if r in COMMAND_ROLES:
        allowed.update(COMMAND_ACTIONS)
    elif r == "sales":
        allowed.update(SALES_AI_EXTRA)
    return allowed


class ActionValidationError(ValueError):
    pass


def _require_non_empty_str(params: dict, key: str) -> str:
    value = params.get(key)
    if value is None:
        raise ActionValidationError(f"Missing required field '{key}'")
    text = str(value).strip()
    if not text:
        raise ActionValidationError(f"'{key}' cannot be empty")
    return text


def _require_int(params: dict, key: str, *, min_value: int | None = None, max_value: int | None = None) -> int:
    if key not in params:
        raise ActionValidationError(f"Missing required field '{key}'")
    try:
        value = int(params[key])
    except (TypeError, ValueError):
        raise ActionValidationError(f"'{key}' must be an integer")
    if min_value is not None and value < min_value:
        raise ActionValidationError(f"'{key}' must be >= {min_value}")
    if max_value is not None and value > max_value:
        raise ActionValidationError(f"'{key}' must be <= {max_value}")
    return value


def _build_system_prompt(role: str, allowed_actions: set[str], max_actions: int) -> str:
    action_specs = {
        "create_team": '{"action":"create_team","params":{"name": string}}',
        "delete_team": '{"action":"delete_team","params":{"team_id": int}}',
        "add_team_member": '{"action":"add_team_member","params":{"team_id": int, "user_id": int}}',
        "remove_team_member": '{"action":"remove_team_member","params":{"team_id": int, "user_id": int}}',
        "create_ledger_entry": '{"action":"create_ledger_entry","params":{"ledger_slug": string, "data": object}}',
        "monthly_best_sales_exec": '{"action":"monthly_best_sales_exec","params":{"year": int, "month": int}}',
        "revenue_summary": '{"action":"revenue_summary","params":{"period":"day|month|year","year": int?, "month": int?, "day": int?}}',
        "business_snapshot": '{"action":"business_snapshot","params":{}}',
        "team_performance_summary": '{"action":"team_performance_summary","params":{"period":"week|month"}}',
        "create_top_performing_team": '{"action":"create_top_performing_team","params":{"name": string, "size": int}}',
        "get_best_manager": '{"action":"get_best_manager","params":{"year": int, "month": int}}',
        "create_team_with_members": '{"action":"create_team_with_members","params":{"name": string, "manager_id": int, "member_ids": [int]}}',
        "create_task": '{"action":"create_task","params":{"title": string, "assignee_id": int, "due_date": "YYYY-MM-DD"?, "priority": "low|medium|high"?, "description": string?}}',
        "list_teams": '{"action":"list_teams","params":{}}',
        "list_company_users": '{"action":"list_company_users","params":{"role"?: "sales"|"manager"|"admin"|"md"|"purchase"|"all"}}',
        "list_tasks": '{"action":"list_tasks","params":{"limit"?: int}}',
        "update_task": '{"action":"update_task","params":{"task_id": int, "title"?: string, "status"?: string, "priority"?: string, "due_date"?: string}}',
        "complete_task": '{"action":"complete_task","params":{"task_id": int}}',
        "delete_task": '{"action":"delete_task","params":{"task_id": int}}',
    }
    allowed_specs = "\n".join(f"- {action_specs[a]}" for a in sorted(allowed_actions) if a in action_specs)

    ledger_schema_block = ""
    if "create_ledger_entry" in allowed_actions:
        ledger_schemas = ["\nRequired JSON keys for create_ledger_entry data object based on ledger_slug:"]
        for slug in ALL_LEDGERS.keys():
            cols = get_ledger_columns(slug)
            fields = ", ".join([f"'{c['key']}'" for c in cols])
            ledger_schemas.append(f" - {slug}: {fields}")
        ledger_schema_block = "\n".join(ledger_schemas)

    return f"""
You are the CRM Company Assistant for role '{role}'.
Return ONLY valid JSON with this exact shape:
{{
  "reasoning": "2-5 short sentences: what you understood, which data/actions you chose and why (shown as 'thinking' in the UI)",
  "say": "short response to user",
  "actions": [
    {{"action": "...", "params": {{...}}}}
  ]
}}

You may ONLY use these actions:
{allowed_specs}

Rules:
- Never output actions outside the allowed list.
- You may output at most {max_actions} actions in "actions". If the user asks for more, prioritize the most important calls in order and state clearly in "say" which items were left for a follow-up message.
- If the user asks for disallowed changes, keep actions empty and explain in "say".
- Use cautious wording in "say" (e.g. "I'll try to…") when success depends on company data; the system will append what actually happened after tools run.
- Use business_snapshot/revenue_summary for "what is going on" and revenue questions.{ledger_schema_block}
- When the user asks to create a team and assign people, prefer create_team_with_members or create_top_performing_team over create_team + add_team_member loops.
- When the user wants tasks assigned, use create_task (you may output multiple create_task actions).
- Use list_teams and list_company_users to discover ids. For managers, Context often includes active_team_id; list_tasks uses that team for manager-scoped views.
"""


def _role_value(role_or_user: object) -> str:
    role = getattr(role_or_user, "role", role_or_user)
    return str(getattr(role, "value", role)).strip().lower()


def _coerce_allowed_action(action: str, allowed_actions: set[str]) -> str:
    """Best-effort correction for small typos in action names (only within allowed actions)."""
    a = (action or "").strip()
    if not a:
        return ""
    if a in allowed_actions:
        return a
    close = difflib.get_close_matches(a, sorted(allowed_actions), n=1, cutoff=0.84)
    return close[0] if close else a


def _resolve_ledger_slug(raw: str) -> str:
    """Normalize + fuzzy-match ledger slugs so tiny typos don't block entry creation."""
    s = (raw or "").strip().lower()
    s = re.sub(r"[\s\\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    if not s:
        return s
    if s in ALL_LEDGERS:
        return s
    name_to_slug = {v.lower().replace(" ", "_"): k for k, v in ALL_LEDGERS.items()}
    if s in name_to_slug:
        return name_to_slug[s]
    candidates = list(ALL_LEDGERS.keys()) + list(name_to_slug.keys())
    close = difflib.get_close_matches(s, candidates, n=1, cutoff=0.78)
    if not close:
        return s
    best = close[0]
    return name_to_slug.get(best, best)


def _ai_opt_str(params: dict, key: str, max_len: int) -> str | None:
    v = params.get(key)
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return s[:max_len]


def _ctx_active_team_id(context: dict | None) -> int | None:
    if not context or not isinstance(context, dict):
        return None
    raw = context.get("active_team_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _normalize_action(action: str, params: dict) -> dict:
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()

    if action == "create_team":
        name = _require_non_empty_str(params, "name")
        if len(name) > 100:
            raise ActionValidationError("'name' must be <= 100 characters")
        return {"name": name}

    if action == "delete_team":
        return {"team_id": _require_int(params, "team_id", min_value=1)}

    if action in {"add_team_member", "remove_team_member"}:
        return {
            "team_id": _require_int(params, "team_id", min_value=1),
            "user_id": _require_int(params, "user_id", min_value=1),
        }

    if action == "create_ledger_entry":
        slug = _resolve_ledger_slug(_require_non_empty_str(params, "ledger_slug"))
        if slug not in ALL_LEDGERS:
            raise ActionValidationError(f"Unknown ledger_slug '{slug}'")
        data = params.get("data")
        if not isinstance(data, dict) or not data:
            raise ActionValidationError("'data' must be a non-empty object")
        return {"ledger_slug": slug, "data": data}

    if action == "monthly_best_sales_exec":
        try:
            year = int(params.get("year", today.year))
            month = int(params.get("month", today.month))
        except (TypeError, ValueError):
            raise ActionValidationError("'year' and 'month' must be integers")
        if year < 2000 or year > 2100:
            raise ActionValidationError("'year' must be between 2000 and 2100")
        if month < 1 or month > 12:
            raise ActionValidationError("'month' must be between 1 and 12")
        return {"year": year, "month": month}

    if action == "revenue_summary":
        period = str(params.get("period", "month")).strip().lower()
        if period not in {"day", "month", "year"}:
            raise ActionValidationError("'period' must be day, month, or year")
        try:
            year = int(params.get("year", today.year))
            month = int(params.get("month", today.month))
            day = int(params.get("day", today.day))
        except (TypeError, ValueError):
            raise ActionValidationError("'year', 'month', and 'day' must be integers")
        if year < 2000 or year > 2100:
            raise ActionValidationError("'year' must be between 2000 and 2100")
        if month < 1 or month > 12:
            raise ActionValidationError("'month' must be between 1 and 12")
        max_day = monthrange(year, month)[1]
        if day < 1 or day > max_day:
            raise ActionValidationError(f"'day' must be between 1 and {max_day} for {year}-{month:02d}")
        return {"period": period, "year": year, "month": month, "day": day}

    if action == "business_snapshot":
        return {}

    if action == "team_performance_summary":
        period = str(params.get("period", "week")).strip().lower()
        if period not in {"week", "month"}:
            raise ActionValidationError("'period' must be week or month")
        return {"period": period}

    if action == "create_top_performing_team":
        name = _require_non_empty_str(params, "name")
        size = _require_int(params, "size", min_value=1, max_value=50)
        return {"name": name, "size": size}

    if action == "get_best_manager":
        try:
            year = int(params.get("year", today.year))
            month = int(params.get("month", today.month))
        except (TypeError, ValueError):
            raise ActionValidationError("'year' and 'month' must be integers")
        return {"year": year, "month": month}

    if action == "create_team_with_members":
        name = _require_non_empty_str(params, "name")
        manager_id = _require_int(params, "manager_id", min_value=1)
        member_ids_raw = params.get("member_ids", [])
        if not isinstance(member_ids_raw, list):
            raise ActionValidationError("'member_ids' must be a list of integers")
        
        member_ids = []
        for v in member_ids_raw:
            try:
                vi = int(v)
                if vi > 0:
                    member_ids.append(vi)
            except (ValueError, TypeError):
                pass
        
        if not member_ids:
            raise ActionValidationError("'member_ids' cannot be empty")
            
        return {"name": name, "manager_id": manager_id, "member_ids": member_ids}

    if action == "create_task":
        title = _require_non_empty_str(params, "title")
        assignee_id = _require_int(params, "assignee_id", min_value=1)
        description = params.get("description")
        if description is not None:
            description = str(description).strip() or None

        priority_raw = str(params.get("priority", "medium")).strip().lower()
        priority_map = {"low": TaskPriority.LOW, "medium": TaskPriority.MEDIUM, "high": TaskPriority.HIGH}
        if priority_raw not in priority_map:
            raise ActionValidationError("'priority' must be low, medium, or high")

        due_dt = None
        due_date_raw = params.get("due_date")
        if due_date_raw is not None and str(due_date_raw).strip():
            txt = str(due_date_raw).strip()
            try:
                d = datetime.strptime(txt, "%Y-%m-%d").date()
                due_dt = datetime(d.year, d.month, d.day)
            except Exception as exc:
                raise ActionValidationError("'due_date' must be YYYY-MM-DD") from exc

        return {
            "title": title,
            "assignee_id": assignee_id,
            "description": description,
            "priority": priority_map[priority_raw],
            "due_date": due_dt,
        }

    if action == "list_teams":
        return {}

    if action == "list_company_users":
        r = params.get("role")
        role_f = str(r).strip().lower() if r is not None and str(r).strip() else None
        return {"role": role_f}

    if action == "list_tasks":
        try:
            lim = int(params.get("limit", 20))
        except (TypeError, ValueError):
            lim = 20
        return {"limit": lim}

    if action == "complete_task":
        return {"task_id": _require_int(params, "task_id", min_value=1)}

    if action == "delete_task":
        return {"task_id": _require_int(params, "task_id", min_value=1)}

    if action == "update_task":
        tid = _require_int(params, "task_id", min_value=1)
        out: dict = {"task_id": tid}
        for k in ("title", "status", "priority", "due_date"):
            if k in params and params[k] is not None:
                if k == "due_date" and isinstance(params[k], str) and not params[k].strip():
                    continue
                out[k] = params[k]
        if len(out) <= 1:
            raise ActionValidationError("Provide at least one of title, status, priority, due_date to update")
        return out

    raise ActionValidationError(f"Unknown action '{action}'")


def _rate_limit_key(user: User) -> str:
    return f"{user.company_id}:{user.id}"


def _enforce_rate_limit(user: User):
    limit = max(1, int(settings.AI_RATE_LIMIT_PER_MINUTE))
    now = time.monotonic()
    bucket = _rate_buckets[_rate_limit_key(user)]
    while bucket and (now - bucket[0]) > 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="AI rate limit exceeded. Please retry shortly.")
    bucket.append(now)


def _parse_json_list(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _serialize_actions(actions: list[AIExecutedAction]) -> str:
    # `params`/`result` may contain datetimes or Enums; normalize to JSON-safe types.
    return json.dumps(jsonable_encoder(actions))


def _company_role_counts(db: Session, current_user: User) -> dict[str, int]:
    q = apply_company_scope(db.query(User.role, func.count(User.id)), User, current_user).group_by(User.role)
    out: dict[str, int] = {}
    for role, n in q.all():
        key = getattr(role, "value", role)
        out[str(key)] = int(n or 0)
    return out


def _lines_for_tool_feedback(action: str, result: object) -> list[str]:
    """Human-readable lines for chat when tool results need explanation (model `say` is pre-execution)."""
    if not isinstance(result, dict):
        return []
    lines: list[str] = []

    if result.get("status") == "skipped":
        reason = str(result.get("reason") or "unknown")
        friendly = {
            "action_not_allowed_for_role": "not allowed for your role",
            "unsupported_action": "this assistant version does not support that action yet",
        }.get(reason, reason.replace("_", " "))
        lines.append(f"{action} was skipped ({friendly}).")
        return lines

    if result.get("status") == "dry_run":
        lines.append(f"Preview only (not applied): {action}.")
        return lines

    if result.get("ok") is False:
        msg = (result.get("summary") or result.get("message") or "").strip()
        sug = (result.get("suggestion") or "").strip()
        chunk = msg
        if sug:
            chunk = f"{msg} Suggested next step: {sug}" if msg else f"Suggested next step: {sug}"
        lines.append(f"{action}: {chunk}" if chunk else f"{action} did not complete successfully.")
        return lines

    st = result.get("status")
    if st == "no_sales_executives":
        lines.append(
            f"{action}: no team was created. There are no users with the sales role in your company, "
            "so there is nobody to rank as top performers by revenue/conversions. "
            "Add or invite sales users first, then ask again; or ask to create an empty team by name and add people manually."
        )
        return lines

    if st == "no_teams_found":
        lines.append(
            f"{action}: you are not on any team yet, so there is no team-scoped performance to report. "
            "Ask an MD/manager to add you to a team, or ask for a company business_snapshot instead."
        )
        return lines

    if st == "no_members_found":
        lines.append(
            f"{action}: your teams exist but have no members linked, so there is nothing to analyze yet."
        )
        return lines

    if action == "create_team_with_members":
        skipped = result.get("members_skipped")
        if isinstance(skipped, list) and skipped:
            lines.append(
                f"{action}: {len(skipped)} user(s) were not added (wrong role, not found, or duplicate). "
                "See the tool result for details."
            )

    if action == "list_teams":
        teams = result.get("teams")
        if isinstance(teams, list):
            lines.append(f"{action}: returned {len(teams)} team(s); see “What ran” for ids and names.")
    if action == "list_company_users":
        users = result.get("users")
        if isinstance(users, list):
            lines.append(f"{action}: returned {len(users)} user(s); see “What ran” for ids.")
    if action == "list_tasks":
        tasks = result.get("tasks")
        if isinstance(tasks, list):
            lines.append(f"{action}: returned {len(tasks)} task(s); see “What ran” for ids.")
    return lines


def _compose_final_ai_message(
    say: str,
    executed: list[AIExecutedAction],
    db: Session,
    current_user: User,
) -> str:
    blocks: list[str] = []
    for ex in executed:
        blocks.extend(_lines_for_tool_feedback(ex.action, ex.result))

    if any(
        isinstance(ex.result, dict) and ex.result.get("status") == "no_sales_executives" for ex in executed
    ):
        counts = _company_role_counts(db, current_user)
        if counts:
            parts = [f"{counts[k]} {k}(s)" for k in sorted(counts.keys())]
            blocks.append("Current roster in your company: " + ", ".join(parts) + ".")

    if not blocks:
        return (say or "Done.").strip()

    feedback = "\n".join(f"• {b}" for b in blocks)
    base = (say or "").strip()
    return (
        "Here is what actually happened (see “What ran” below for raw JSON):\n\n"
        f"{feedback}\n\n"
        f"—\n\n"
        f"{base if base else 'Done.'}"
    )


def _conversation_to_response(conv: AIConversation) -> AIChatResponse:
    executed = [AIExecutedAction(**item) for item in _parse_json_list(conv.executed_actions_json)]
    used_params = None
    if conv.context_json:
        try:
            parsed = json.loads(conv.context_json)
            if isinstance(parsed, dict) and isinstance(parsed.get("used_params"), dict):
                used_params = AIResolvedParams(**parsed["used_params"])
        except Exception:
            used_params = None
    return AIChatResponse(
        message=conv.ai_message or "Done.",
        executed_actions=executed,
        used_params=used_params,
        reasoning=(conv.ai_reasoning or "").strip() or None,
    )


def _create_team(db: Session, current_user: User, name: str) -> dict:
    normalized = (name or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Team name is required")
    if len(normalized) > 100:
        raise HTTPException(status_code=400, detail="Team name must be <= 100 characters")

    existing = (
        apply_company_scope(db.query(Team), Team, current_user)
        .filter(func.lower(Team.name) == normalized.lower())
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Team name already exists")
    t = Team(company_id=current_user.company_id, name=normalized)
    db.add(t)
    db.flush()
    _audit(db, current_user, "ai_team_created", "team", str(t.id), t.name, None, {"name": t.name})
    db.commit()
    db.refresh(t)
    return {"id": t.id, "name": t.name}


def _delete_team(db: Session, current_user: User, team_id: int) -> dict:
    team = apply_company_scope(db.query(Team), Team, current_user).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    member_count = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(TeamMembership.team_id == team_id).count()
    apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(TeamMembership.team_id == team_id).delete(synchronize_session="fetch")
    apply_company_scope(db.query(User), User, current_user).filter(User.team_id == team_id).update({"team_id": None}, synchronize_session="fetch")

    _audit(db, current_user, "ai_team_deleted", "team", str(team.id), team.name, {"member_count": member_count}, None)
    db.delete(team)
    db.commit()
    return {"id": team_id, "name": team.name, "member_count": member_count}


def _add_member(db: Session, current_user: User, team_id: int, user_id: int) -> dict:
    team = apply_company_scope(db.query(Team), Team, current_user).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    target = apply_company_scope(db.query(User), User, current_user).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
        TeamMembership.team_id == team_id, TeamMembership.user_id == user_id
    ).first()
    if existing:
        return {"message": "already_member", "team_id": team_id, "user_id": user_id}

    if _role_value(target) == "manager":
        from app.utils.validators import ensure_one_manager_per_team
        ensure_one_manager_per_team(db, team_id, exclude_user_id=target.id)

    from app.utils.validators import validate_team_membership_role
    validate_team_membership_role(_role_value(target))

    db.add(TeamMembership(company_id=current_user.company_id, team_id=team_id, user_id=user_id))
    if target.team_id is None:
        target.team_id = team_id
    _audit(db, current_user, "ai_team_member_added", "team", str(team_id), team.name, None, {"user_id": user_id})
    db.commit()
    return {"team_id": team_id, "user_id": user_id}


def _remove_member(db: Session, current_user: User, team_id: int, user_id: int) -> dict:
    team = apply_company_scope(db.query(Team), Team, current_user).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    target = apply_company_scope(db.query(User), User, current_user).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    membership = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
        TeamMembership.team_id == team_id, TeamMembership.user_id == user_id
    ).first()
    if not membership:
        return {"message": "not_a_member", "team_id": team_id, "user_id": user_id}

    before_primary = target.team_id
    db.delete(membership)
    if target.team_id == team_id:
        next_membership = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
            TeamMembership.user_id == user_id
        ).order_by(TeamMembership.id.desc()).first()
        target.team_id = next_membership.team_id if next_membership else None

    _audit(
        db,
        current_user,
        "ai_team_member_removed",
        "team",
        str(team_id),
        team.name,
        {"user_id": user_id, "primary_team_id": before_primary},
        {"user_id": user_id, "primary_team_id": target.team_id},
    )
    db.commit()
    return {"team_id": team_id, "user_id": user_id}


def _create_ledger_entry(db: Session, current_user: User, ledger_slug: str, data: dict) -> dict:
    if _role_value(current_user) not in COMMAND_ROLES:
        raise HTTPException(status_code=403, detail="You are not allowed to create ledger entries via AI")
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")
    ledger_slug = _resolve_ledger_slug(ledger_slug)
    if ledger_slug not in ALL_LEDGERS:
        raise HTTPException(status_code=400, detail=f"Unknown ledger '{ledger_slug}'")

    entry = LedgerEntry(
        company_id=current_user.company_id,
        ledger_slug=ledger_slug,
        data=data,
        created_by=current_user.id,
    )
    db.add(entry)
    db.flush()
    _audit(
        db,
        current_user,
        "ai_ledger_entry_created",
        "ledger",
        str(entry.id),
        ledger_slug,
        None,
        {"ledger_slug": ledger_slug, "data": data},
    )
    db.commit()
    db.refresh(entry)
    return {
        "id": entry.id,
        "ledger_slug": entry.ledger_slug,
        "data": entry.data or {},
    }


def _revenue_summary(db: Session, current_user: User, period: str, year: int, month: int, day: int) -> dict:
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)

    if period == "day":
        start_date = date(year, month, day)
        end_date = start_date + timedelta(days=1)
        period_label = start_date.isoformat()
    elif period == "month":
        start_date = date(year, month, 1)
        end_date = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        period_label = f"{year}-{month:02d}"
    else:
        start_date = date(year, 1, 1)
        end_date = date(year + 1, 1, 1)
        period_label = str(year)

    scoped = inv_q.filter(Invoice.issued_date.isnot(None), Invoice.issued_date >= start_date, Invoice.issued_date < end_date)
    total_invoices = scoped.count()
    billed = float(scoped.with_entities(func.sum(Invoice.total)).scalar() or 0.0)
    paid = float(scoped.filter(Invoice.status == "Paid").with_entities(func.sum(Invoice.total)).scalar() or 0.0)
    pending = float(scoped.filter(Invoice.status.in_(["Pending", "Overdue"])).with_entities(func.sum(Invoice.total)).scalar() or 0.0)
    overdue = float(scoped.filter(Invoice.status == "Overdue").with_entities(func.sum(Invoice.total)).scalar() or 0.0)

    return {
        "period": period,
        "period_label": period_label,
        "invoice_count": total_invoices,
        "billed_revenue": round(billed, 2),
        "paid_revenue": round(paid, 2),
        "pending_revenue": round(pending, 2),
        "overdue_revenue": round(overdue, 2),
    }


def _business_snapshot(db: Session, current_user: User) -> dict:
    lead_q = apply_company_scope(db.query(Lead), Lead, current_user).filter(Lead.deleted_at.is_(None))
    inv_q = apply_company_scope(db.query(Invoice), Invoice, current_user)
    task_q = apply_company_scope(db.query(Task), Task, current_user)

    total_leads = lead_q.count()
    converted_leads = lead_q.filter(Lead.status == "Converted").count()
    lost_leads = lead_q.filter(Lead.status == "Lost").count()
    open_tasks = task_q.filter(Task.status != "Completed").count()
    completed_tasks = task_q.filter(Task.status == "Completed").count()
    total_invoices = inv_q.count()
    pending_invoices = inv_q.filter(Invoice.status.in_(["Pending", "Overdue"])).count()

    billed_total = float(inv_q.with_entities(func.sum(Invoice.total)).scalar() or 0.0)
    paid_total = float(inv_q.filter(Invoice.status == "Paid").with_entities(func.sum(Invoice.total)).scalar() or 0.0)
    overdue_total = float(inv_q.filter(Invoice.status == "Overdue").with_entities(func.sum(Invoice.total)).scalar() or 0.0)

    return {
        "leads": {
            "total": total_leads,
            "converted": converted_leads,
            "lost": lost_leads,
            "active": max(0, total_leads - converted_leads - lost_leads),
        },
        "tasks": {
            "open": open_tasks,
            "completed": completed_tasks,
        },
        "invoices": {
            "total": total_invoices,
            "pending": pending_invoices,
            "billed_revenue": round(billed_total, 2),
            "paid_revenue": round(paid_total, 2),
            "overdue_revenue": round(overdue_total, 2),
        },
    }


def _monthly_best_sales_exec(db: Session, current_user: User, year: int, month: int) -> dict:
    # Reuse the same definition as /md/performance/monthly (finance source of truth).
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    sales_users = apply_company_scope(db.query(User), User, current_user).filter(User.role == "sales").all()
    sales_ids = [u.id for u in sales_users]
    if not sales_ids:
        return {"top_sales_exec": None}

    inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user)
    inv_q = inv_q.filter(
        Invoice.created_by_id.in_(sales_ids),
        Invoice.status == "Paid",
        Invoice.paid_date.isnot(None),
        Invoice.paid_date >= start.date(),
        Invoice.paid_date < end.date(),
    ).group_by(Invoice.created_by_id)
    revenue_map = {uid: float(total or 0) for uid, total in inv_q.all()}

    conv_q = apply_company_scope(db.query(Lead.assigned_to_id, func.count(Lead.id)), Lead, current_user).filter(Lead.deleted_at.is_(None))
    conv_q = conv_q.filter(
        Lead.assigned_to_id.in_(sales_ids),
        Lead.status == "Converted",
        Lead.converted_at.isnot(None),
        Lead.converted_at >= start,
        Lead.converted_at < end,
    ).group_by(Lead.assigned_to_id)
    conv_map = {uid: int(cnt or 0) for uid, cnt in conv_q.all()}

    rows = []
    for u in sales_users:
        rows.append(
            {
                "user_id": u.id,
                "name": u.full_name,
                "email": u.email,
                "revenue": revenue_map.get(u.id, 0.0),
                "converted_leads": conv_map.get(u.id, 0),
            }
        )
    rows.sort(key=lambda x: (x["revenue"], x["converted_leads"]), reverse=True)
    return {"top_sales_exec": rows[0] if rows else None}


def _team_performance_summary(db: Session, current_user: User, period: str) -> dict:
    teams = apply_company_scope(db.query(Team), Team, current_user).join(
        TeamMembership, TeamMembership.team_id == Team.id
    ).filter(TeamMembership.user_id == current_user.id).all()
    
    if not teams:
        return {
            "status": "no_teams_found",
            "ok": False,
            "summary": "You are not a member of any team in this company.",
            "suggestion": "Ask to be added to a team, or request a company-wide business_snapshot.",
        }
        
    team_ids = [t.id for t in teams]
    team_names = [t.name for t in teams]
    
    team_members = apply_company_scope(db.query(User), User, current_user).join(
        TeamMembership, TeamMembership.user_id == User.id
    ).filter(TeamMembership.team_id.in_(team_ids)).all()
    
    member_ids = [m.id for m in team_members]
    if not member_ids:
        return {
            "status": "no_members_found",
            "ok": False,
            "summary": "Your teams have no members attached in the system.",
            "suggestion": "Add members to teams first, then ask for performance again.",
        }
        
    now = datetime.now(timezone.utc)
    if period == "week":
        start_date = now - timedelta(days=7)
    else:
        start_date = now - timedelta(days=30)
        
    conv_q = apply_company_scope(db.query(Lead.assigned_to_id, func.count(Lead.id)), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(
        Lead.assigned_to_id.in_(member_ids),
        Lead.status == "Converted",
        Lead.converted_at >= start_date
    ).group_by(Lead.assigned_to_id)
    conv_map = {uid: int(cnt or 0) for uid, cnt in conv_q.all()}
    
    task_q = apply_company_scope(db.query(Task.assigned_to_id, func.count(Task.id)), Task, current_user).filter(
        Task.assigned_to_id.in_(member_ids),
        Task.status == "Completed"
    ).group_by(Task.assigned_to_id)
    task_map = {uid: int(cnt or 0) for uid, cnt in task_q.all()}
    
    inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user).filter(
        Invoice.created_by_id.in_(member_ids),
        Invoice.status == "Paid",
        Invoice.paid_date >= start_date.date()
    ).group_by(Invoice.created_by_id)
    inv_map = {uid: float(total or 0) for uid, total in inv_q.all()}
    
    member_stats = []
    total_converted = 0
    total_tasks = 0
    total_revenue = 0.0
    for m in team_members:
        c = conv_map.get(m.id, 0)
        t = task_map.get(m.id, 0)
        r = inv_map.get(m.id, 0.0)
        total_converted += c
        total_tasks += t
        total_revenue += r
        member_stats.append({
            "name": m.full_name,
            "role": m.role,
            "converted_leads": c,
            "completed_tasks": t,
            "revenue": r
        })
    
    return {
        "teams_managed": team_names,
        "period_examined": period,
        "total_converted_leads": total_converted,
        "total_completed_tasks": total_tasks,
        "total_revenue_generated": round(float(total_revenue), 2),
        "member_stats": member_stats
    }


def _get_best_manager(db: Session, current_user: User, year: int, month: int) -> dict:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        
    managers = apply_company_scope(db.query(User), User, current_user).filter(User.role == "manager").all()
    if not managers:
        return {"top_manager": None}
        
    # Get all team memberships of managers
    manager_ids = [m.id for m in managers]
    m_memberships = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
        TeamMembership.user_id.in_(manager_ids)
    ).all()
    
    # Find team IDs each manager manages
    manager_teams = defaultdict(list)
    managed_team_ids = set()
    for tm in m_memberships:
        manager_teams[tm.user_id].append(tm.team_id)
        managed_team_ids.add(tm.team_id)
        
    # Find all members in these teams
    team_memberships = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
        TeamMembership.team_id.in_(managed_team_ids)
    ).all()
    team_to_members = defaultdict(list)
    all_member_ids = set()
    for tm in team_memberships:
        team_to_members[tm.team_id].append(tm.user_id)
        all_member_ids.add(tm.user_id)
        
    if not all_member_ids:
        return {"top_manager": managers[0].full_name if managers else None}
        
    # Calculate revenue for these members
    inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user).filter(
        Invoice.created_by_id.in_(list(all_member_ids)),
        Invoice.status == "Paid",
        Invoice.paid_date >= start.date(),
        Invoice.paid_date < end.date()
    ).group_by(Invoice.created_by_id)
    inv_map = {uid: float(total or 0) for uid, total in inv_q.all()}
    
    manager_scores = []
    for m in managers:
        total = 0.0
        teams = manager_teams[m.id]
        mems = set()
        for t in teams:
            for uid in team_to_members[t]:
                mems.add(uid)
        for uid in mems:
            total += inv_map.get(uid, 0.0)
        manager_scores.append({"id": m.id, "name": m.full_name, "revenue": total})
        
    manager_scores.sort(key=lambda x: x["revenue"], reverse=True)
    return {"top_manager": manager_scores[0] if manager_scores else None}


def _create_top_performing_team(db: Session, current_user: User, name: str, size: int) -> dict:
    if _role_value(current_user) not in COMMAND_ROLES:
        raise HTTPException(status_code=403, detail="Un-authorized to create teams via AI")
    
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Team name is required")
        
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=30)
    
    sales_users = apply_company_scope(db.query(User), User, current_user).filter(User.role == "sales").all()
    sales_ids = [u.id for u in sales_users]
    if not sales_ids:
        return {
            "status": "no_sales_executives",
            "ok": False,
            "summary": "No sales-role users exist in this company; top-performer teams need sales reps to rank.",
            "suggestion": "Add users with the sales role, then retry; or use create_team / create_team_with_members with explicit members.",
        }
        
    inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user).filter(
        Invoice.created_by_id.in_(sales_ids),
        Invoice.status == "Paid",
        Invoice.paid_date >= start_date.date()
    ).group_by(Invoice.created_by_id)
    revenue_map = {uid: float(total or 0) for uid, total in inv_q.all()}
    
    conv_q = apply_company_scope(db.query(Lead.assigned_to_id, func.count(Lead.id)), Lead, current_user).filter(Lead.deleted_at.is_(None)).filter(
        Lead.assigned_to_id.in_(sales_ids),
        Lead.status == "Converted",
        Lead.converted_at >= start_date
    ).group_by(Lead.assigned_to_id)
    conv_map = {uid: int(cnt or 0) for uid, cnt in conv_q.all()}
    
    scores = []
    for u in sales_users:
        scores.append({
            "user": u,
            "revenue": revenue_map.get(u.id, 0.0),
            "converted": conv_map.get(u.id, 0)
        })
        
    scores.sort(key=lambda x: (x["revenue"], x["converted"]), reverse=True)
    top_performers = scores[:size]
    
    team_result = _create_team(db, current_user, normalized_name)
    team_id = team_result["id"]
    
    # MD cannot be a team member (global role). If the caller is MD, pick the best manager
    # (last 30 days revenue across their teams) to be the team lead.
    manager_id = current_user.id
    chosen_manager = None
    if _role_value(current_user) == "md":
        # Reuse the scoring logic used for best manager, but for last 30 days.
        managers = apply_company_scope(db.query(User), User, current_user).filter(User.role == "manager").all()
        if managers:
            manager_ids = [m.id for m in managers]
            m_memberships = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.user_id.in_(manager_ids)
            ).all()
            manager_teams = defaultdict(list)
            managed_team_ids = set()
            for tm in m_memberships:
                manager_teams[tm.user_id].append(tm.team_id)
                managed_team_ids.add(tm.team_id)
            team_memberships = apply_company_scope(db.query(TeamMembership), TeamMembership, current_user).filter(
                TeamMembership.team_id.in_(managed_team_ids)
            ).all()
            team_to_members = defaultdict(list)
            all_member_ids = set()
            for tm in team_memberships:
                team_to_members[tm.team_id].append(tm.user_id)
                all_member_ids.add(tm.user_id)
            inv_q = apply_company_scope(db.query(Invoice.created_by_id, func.sum(Invoice.total)), Invoice, current_user).filter(
                Invoice.created_by_id.in_(list(all_member_ids)),
                Invoice.status == "Paid",
                Invoice.paid_date >= start_date.date()
            ).group_by(Invoice.created_by_id)
            inv_map = {uid: float(total or 0) for uid, total in inv_q.all()}
            manager_scores = []
            for m in managers:
                total = 0.0
                mems = set()
                for t in manager_teams.get(m.id, []):
                    for uid in team_to_members.get(t, []):
                        mems.add(uid)
                for uid in mems:
                    total += inv_map.get(uid, 0.0)
                manager_scores.append({"id": m.id, "name": m.full_name, "revenue": total})
            manager_scores.sort(key=lambda x: x["revenue"], reverse=True)
            chosen_manager = manager_scores[0] if manager_scores else None
            if chosen_manager:
                manager_id = chosen_manager["id"]

    _add_member(db, current_user, team_id, manager_id)
    
    added_members = []
    for performer in top_performers:
        u = performer["user"]
        if u.id == manager_id:
            continue
        _add_member(db, current_user, team_id, u.id)
        added_members.append({"id": u.id, "name": u.full_name, "revenue": performer["revenue"]})
        
    return {
        "team": team_result,
        "manager_added": chosen_manager["name"] if chosen_manager else current_user.full_name,
        "members_added": added_members
    }


def _create_team_with_members(db: Session, current_user: User, name: str, manager_id: int, member_ids: list[int]) -> dict:
    if _role_value(current_user) not in COMMAND_ROLES:
        raise HTTPException(status_code=403, detail="Un-authorized to create teams via AI")
    
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Team name is required")

    # Validate the manager BEFORE creating the team, so a bad manager_id cannot
    # leave an orphaned team behind (each helper self-commits, so there is no
    # outer transaction to roll the team back).
    manager = apply_company_scope(db.query(User), User, current_user).filter(User.id == manager_id).first()
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")
    if _role_value(manager) != "manager":
        raise HTTPException(status_code=400, detail="manager_id must belong to a manager user")

    team_result = _create_team(db, current_user, normalized_name)
    team_id = team_result["id"]

    added_members = []
    _add_member(db, current_user, team_id, manager_id)
    added_members.append({"id": manager_id, "name": manager.full_name if manager else "Unknown", "role": "manager"})
    
    skipped = []
    for uid in set(member_ids):
        if uid == manager_id:
            continue
        u = apply_company_scope(db.query(User), User, current_user).filter(User.id == uid).first()
        if not u:
            skipped.append({"id": uid, "reason": "not_found"})
            continue
        if _role_value(u) != "sales":
            skipped.append({"id": uid, "reason": f"role_not_sales:{_role_value(u)}"})
            continue
        _add_member(db, current_user, team_id, uid)
        added_members.append({"id": uid, "name": u.full_name, "role": _role_value(u)})
            
    return {"team": team_result, "members_added": added_members, "members_skipped": skipped}


def _create_task(db: Session, current_user: User, title: str, assignee_id: int, description: str | None, priority: TaskPriority, due_date: datetime | None) -> dict:
    if _role_value(current_user) not in COMMAND_ROLES:
        raise HTTPException(status_code=403, detail="You are not allowed to create tasks via AI")
    if current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")

    assignee = apply_company_scope(db.query(User), User, current_user).filter(User.id == assignee_id).first()
    if not assignee:
        raise HTTPException(status_code=404, detail="Assignee not found")

    task = Task(
        company_id=current_user.company_id,
        title=title,
        description=description,
        assigned_to_id=assignee_id,
        assigned_by_id=current_user.id,
        is_manager_assigned=True,
        priority=priority,
        due_date=due_date,
    )
    db.add(task)
    db.flush()
    _audit(
        db,
        current_user,
        "ai_task_created",
        "task",
        str(task.id),
        task.title,
        None,
        {"assigned_to_id": assignee_id, "priority": str(getattr(priority, "value", priority)), "due_date": due_date.isoformat() if due_date else None},
    )
    db.commit()
    db.refresh(task)
    return {"id": task.id, "title": task.title, "assigned_to_id": task.assigned_to_id, "due_date": task.due_date.isoformat() if task.due_date else None}


@router.get("/company-assistant/params", response_model=AIParamsResponse)
def get_company_assistant_params(current_user: User = Depends(get_current_user)):
    _ensure_company_user(current_user)
    allowed_models = set()
    if settings.OPENAI_KEY:
        allowed_models.update(ALLOWED_OPENAI_MODELS)
    if settings.GEMINI_API_KEY:
        allowed_models.update(ALLOWED_GEMINI_MODELS)
    return AIParamsResponse(
        can_override=_role_value(current_user) in AI_PARAM_OVERRIDE_ROLES,
        allowed_models=sorted(allowed_models),
        params=_server_default_ai_params(),
    )


class _UnsupportedActionError(Exception):
    """Raised when a role-allowed action has no dispatch implementation."""


def _execute_action(db: Session, current_user: User, action: str, normalized: dict, context) -> dict:
    """Run one AI action and return its result dict. Raises HTTPException on a
    business error and _UnsupportedActionError for an unknown action."""
    if action == "create_team":
        return _create_team(db, current_user, name=normalized["name"])
    if action == "delete_team":
        return _delete_team(db, current_user, team_id=normalized["team_id"])
    if action == "add_team_member":
        return _add_member(db, current_user, team_id=normalized["team_id"], user_id=normalized["user_id"])
    if action == "remove_team_member":
        return _remove_member(db, current_user, team_id=normalized["team_id"], user_id=normalized["user_id"])
    if action == "monthly_best_sales_exec":
        return _monthly_best_sales_exec(db, current_user, year=normalized["year"], month=normalized["month"])
    if action == "create_ledger_entry":
        return _create_ledger_entry(db, current_user, ledger_slug=normalized["ledger_slug"], data=normalized["data"])
    if action == "create_task":
        return _create_task(
            db,
            current_user,
            title=normalized["title"],
            assignee_id=normalized["assignee_id"],
            description=normalized["description"],
            priority=normalized["priority"],
            due_date=normalized["due_date"],
        )
    if action == "revenue_summary":
        return _revenue_summary(
            db,
            current_user,
            period=normalized["period"],
            year=normalized["year"],
            month=normalized["month"],
            day=normalized["day"],
        )
    if action == "business_snapshot":
        return _business_snapshot(db, current_user)
    if action == "team_performance_summary":
        return _team_performance_summary(db, current_user, period=normalized["period"])
    if action == "get_best_manager":
        return _get_best_manager(db, current_user, year=normalized["year"], month=normalized["month"])
    if action == "create_team_with_members":
        return _create_team_with_members(
            db,
            current_user,
            name=normalized["name"],
            manager_id=normalized["manager_id"],
            member_ids=normalized["member_ids"],
        )
    if action == "create_top_performing_team":
        return _create_top_performing_team(db, current_user, name=normalized["name"], size=normalized["size"])
    if action == "list_teams":
        return aca.ai_list_teams(db, current_user)
    if action == "list_company_users":
        return aca.ai_list_company_users(db, current_user, normalized.get("role"))
    if action == "list_tasks":
        return aca.ai_list_tasks(db, current_user, normalized["limit"], context)
    if action == "complete_task":
        return aca.ai_complete_task(db, current_user, normalized["task_id"], context)
    if action == "delete_task":
        return aca.ai_delete_task(db, current_user, normalized["task_id"], context)
    if action == "update_task":
        fields = {k: v for k, v in normalized.items() if k != "task_id"}
        return aca.ai_update_task(db, current_user, normalized["task_id"], fields, context)
    raise _UnsupportedActionError(action)


@router.post("/company-assistant", response_model=AIChatResponse)
def company_assistant(
    request: Request,
    body: AIChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Company-scoped AI endpoint.
    Admin/MD/Manager get full command tools (teams, ledger, tasks, leads, follow-ups); sales get lead/task/follow-up
    tools within their scope plus read-only insights; other roles get read-only insights only.
    """
    _ensure_company_user(current_user)
    _enforce_rate_limit(current_user)
    allowed_actions = _allowed_actions_for_role(current_user.role)
    ai_params = _resolve_ai_params(current_user, body.ai_params)

    idempotency_key = (request.headers.get("Idempotency-Key") or "").strip() or None
    if idempotency_key and len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key too long")
    if idempotency_key:
        existing = (
            db.query(AIConversation)
            .filter(
                AIConversation.company_id == current_user.company_id,
                AIConversation.user_id == current_user.id,
                AIConversation.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing:
            if existing.status == "completed":
                return _conversation_to_response(existing)
            if existing.status == "processing":
                raise HTTPException(status_code=409, detail="Duplicate request is still processing")

    system = _build_system_prompt(current_user.role, allowed_actions, ai_params.max_actions)

    conversation = AIConversation(
        company_id=current_user.company_id,
        user_id=current_user.id,
        idempotency_key=idempotency_key,
        status="processing",
        user_message=body.message.strip(),
        context_json=json.dumps(
            {
                "context": body.context,
                "used_params": ai_params.model_dump(),
            }
        ),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    try:
        context_block = ""
        if body.context is not None:
            try:
                # Keep prompt bounded even if client sends large context.
                dumped = json.dumps(body.context, ensure_ascii=False)
                if len(dumped) > 6000:
                    dumped = dumped[:6000] + "…"
                context_block = "\nContext (recent chat/history and UI state):\n" + dumped + "\n"
            except Exception:
                # Context is optional; ignore serialization issues.
                context_block = ""

        prompt = system + context_block + "User: " + body.message.strip()
        # The database work above and below runs in this worker thread; only the AI call goes back to the event loop.
        plan = anyio.from_thread.run(_ai_plan, prompt, ai_params)
        if not isinstance(plan, dict):
            raise HTTPException(status_code=502, detail="AI output was not a JSON object")

        reasoning_raw = plan.get("reasoning")
        reasoning = str(reasoning_raw).strip() if reasoning_raw is not None else ""
        reasoning = reasoning or None

        say = str(plan.get("say", "")).strip() or "Done."
        actions_raw = plan.get("actions", []) or []
        if not isinstance(actions_raw, list):
            raise HTTPException(status_code=400, detail="Invalid actions payload: expected a list")

        selected_actions = actions_raw[: ai_params.max_actions]
        action_inputs: list[tuple[int, str, dict]] = []
        executed_map: dict[int, AIExecutedAction] = {}

        # Validate first so we fail with clean 400s before executing mutating actions.
        for idx, raw_action in enumerate(selected_actions):
            if not isinstance(raw_action, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid action payload at index {idx}: action must be an object",
                )
            action = _coerce_allowed_action(str(raw_action.get("action", "")).strip(), allowed_actions)
            if not action:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid action payload at index {idx}: missing 'action'",
                )
            params = raw_action.get("params") or {}
            if not isinstance(params, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid params for action '{action}' at index {idx}: params must be an object",
                )

            if action not in allowed_actions:
                executed_map[idx] = AIExecutedAction(
                    action=action,
                    params=params,
                    result={"status": "skipped", "reason": "action_not_allowed_for_role"},
                )
                continue

            try:
                normalized = _normalize_action(action, params)
            except ActionValidationError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid params for action '{action}': {exc}",
                ) from exc
            action_inputs.append((idx, action, normalized))

        for idx, action, normalized in action_inputs:
            if body.dry_run and action not in READ_ONLY_ACTIONS:
                executed_map[idx] = AIExecutedAction(
                    action=action,
                    params=normalized,
                    result={"status": "dry_run", "params": normalized},
                )
                continue
            try:
                result = _execute_action(db, current_user, action, normalized, body.context)
            except _UnsupportedActionError:
                executed_map[idx] = AIExecutedAction(
                    action=action,
                    params=normalized,
                    result={"status": "skipped", "reason": "unsupported_action"},
                )
                continue
            except HTTPException as exc:
                # A single failing action must not abort the turn: record it as a
                # per-action error and audit the failed attempt.
                executed_map[idx] = AIExecutedAction(
                    action=action,
                    params=normalized,
                    result={"status": "error", "detail": exc.detail},
                )
                if action in COMMAND_ACTIONS:
                    log_activity(
                        db,
                        user=current_user,
                        action=f"ai:{action}:failed",
                        entity_type="ai_action",
                        entity_id=None,
                        entity_name=action,
                        after=json.dumps({"params": normalized, "error": exc.detail}, default=str),
                    )
                continue

            executed_map[idx] = AIExecutedAction(action=action, params=normalized, result=result)

            # Audit trail: every AI-driven mutation records who did what, in which
            # company, with the payload and result. Read-only actions are not logged.
            if action in COMMAND_ACTIONS:
                log_activity(
                    db,
                    user=current_user,
                    action=f"ai:{action}",
                    entity_type="ai_action",
                    entity_id=None,
                    entity_name=action,
                    after=json.dumps({"params": normalized, "result": result}, default=str),
                )

        executed = [executed_map[idx] for idx in range(len(selected_actions)) if idx in executed_map]

        final_message = _compose_final_ai_message(say, executed, db, current_user)

        conversation.status = "completed"
        conversation.ai_message = final_message
        conversation.ai_reasoning = reasoning
        conversation.planned_actions_json = json.dumps(selected_actions)
        conversation.executed_actions_json = _serialize_actions(executed)
        db.commit()
        return AIChatResponse(
            message=final_message,
            executed_actions=executed,
            used_params=ai_params,
            reasoning=reasoning,
            dry_run=bool(body.dry_run),
        )
    except HTTPException as exc:
        conversation.status = "failed"
        conversation.error_detail = str(exc.detail)
        db.commit()
        raise
    except Exception as exc:
        conversation.status = "failed"
        conversation.error_detail = str(exc)
        db.commit()
        raise

