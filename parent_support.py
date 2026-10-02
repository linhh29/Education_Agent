"""Isolated parent conversation, using read-only child evidence projections.

Only save_draft crosses into the existing child mutation API, on an explicit click.
Model I/O always runs outside JsonStore transactions. No child state repair here.
"""
import copy
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta, date
import calendar
from zoneinfo import ZoneInfo

from memory_support import (active, validity, version, reference, dependencies_valid,
                            exploration_valid, exploration_view, memory_view)

TZ = ZoneInfo("Asia/Shanghai")


def clean_advice(value):
    """Project user prose, not serialized fields; never rewrite saved history."""
    from parent_prompts import PARENT_TASKS
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        decoded = value
    if isinstance(decoded, dict):
        return clean_advice(decoded.get("advice"))
    if not isinstance(decoded, str):
        return ""
    def fields(schema):
        if isinstance(schema, dict):
            return set(schema.get("properties", {})) | set().union(*(fields(v) for v in schema.values()))
        if isinstance(schema, list):
            return set().union(*(fields(v) for v in schema))
        return set()
    internal = set().union(*(fields(t["schema"]) for t in PARENT_TASKS.values()))
    lines = []
    for line in value.strip()[:2000].splitlines():
        line = line.strip()
        if line.startswith("```"):
            continue
        field = re.fullmatch(r'[\s{,]*[\"\']?([A-Za-z_][\w]*)[\"\']?\s*:\s*(.*)', line)
        if field:
            key, content = field.groups()
            if key == "advice":
                line = content.strip(' \"\',}')
            elif key in internal or not content.strip():
                continue
        # Empty JSON values are absence, including fragments such as ':null'.
        if re.fullmatch(r'[\s:;,\[\]{}\"\'`]*(?:(?i:null|none|undefined|true|false))?[\s:;,\[\]{}\"\'`]*', line):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    return text if any(unicodedata.category(char)[0] in "LNS" for char in text) else ""


def local_time(value):
    """Parent-only display/projection; leave persisted child timestamps intact."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("缺少时间的时区")
    return parsed.astimezone(TZ).isoformat(timespec="seconds")


class ParentService:
    def __init__(self, service):
        self.service, self.store, self.model = service, service.store, service.model
        self.save_lock = threading.Lock()
        with self.store.transaction() as db:
            for key in ("parentMessages", "parentRequests"):
                db.setdefault(key, {})
            for r in db["parentRequests"].values():
                if r["status"] == "pending":
                    r.update(status="failed", error="服务已重启，家长的问题保留了，可以重试。")

    @staticmethod
    def error(text, status=400, code="parent_request"):
        from companion import ProductError
        return ProductError(text, status, code)

    def valid_result(self, db, result, child_id):
        blocked, _ = validity(db, child_id)
        return (dependencies_valid(db, result.get("memoryVersions", []), child_id)
                and all((m := db["messages"].get(mid, {})).get("childId") == child_id
                        and mid not in blocked and not m.get("deleted")
                        and not db["conversations"].get(m.get("conversationId"), {}).get("deleted")
                        for mid in result.get("sourceMessageIds", [])))

    def source(self, db, source_id, child_id):
        blocked, invalid = validity(db, child_id)
        m = db["messages"].get(source_id, {})
        if (m.get("childId") == child_id and source_id not in blocked and not m.get("deleted")
                and not db["conversations"].get(m.get("conversationId"), {}).get("deleted")):
            return {"id": source_id, "label": "孩子原话" if m["role"] == "user" else "当时的回答",
                    "quote": m.get("text", "")[:100], "createdAt": m.get("createdAt"),
                    "url": "/memory?conversation=" + m["conversationId"] + "&source=" + source_id}
        item = db["memoryItems"].get(source_id, {})
        if source_id not in invalid and active(item, child_id, item.get("conversationId")):
            return {"id": source_id, "label": "提醒" if item["kind"] == "reminder" else "对话记录",
                    "quote": item.get("summary", "")[:100], "createdAt": item.get("updatedAt"),
                    "url": "/memory?memory=" + source_id}
        return None

    def snapshot(self, child_id):
        db = self.store.read()
        self.service.profile(db, child_id)
        messages = sorted((m for m in db["parentMessages"].values() if m["childId"] == child_id), key=lambda m: m["createdAt"])
        latest_activity = self.current_activity(db, child_id)
        activity = None
        if latest_activity and self.valid_result(db, latest_activity, child_id):
            activity = {**latest_activity["activity"], "createdAt": latest_activity["createdAt"],
                        "url": "/parent?view=ask#parent-" + latest_activity["id"]}
        visible = []
        for m in messages[-40:]:
            value = {k: copy.deepcopy(m[k]) for k in ("id", "role", "text", "advice", "activity", "draft", "createdAt", "requestId", "timeWindow", "partial") if k in m}
            value["advice"] = clean_advice(value.get("advice"))
            value["stale"] = m["role"] == "assistant" and not self.valid_result(db, m, child_id)
            source_ids = m.get("sources", [])
            if m.get("activity") and not source_ids:
                # A material-only follow-up keeps its previous activity's real
                # dependencies. Keep those inspectable even with no new quotes.
                source_ids = ([r["id"] for r in m.get("memoryVersions", [])] + m.get("sourceMessageIds", []))[:4]
            value["sources"] = [s for sid in source_ids if (s := self.source(db, sid, child_id))]
            if value["stale"]:
                value["activity"] = None
                if (value.get("draft") or {}).get("status") == "pending" and value["draft"].get("action") == "add":
                    value["draft"] = None
            visible.append(value)
        requests = [r for r in db["parentRequests"].values() if r["childId"] == child_id]
        return {"messages": visible, "activity": activity,
                "pending": next((r for r in requests if r["status"] == "pending"), None),
                "lastRequest": requests[-1] if requests else None}

    def current_activity(self, db, child_id):
        # One existing object, independently of the bounded dialogue tail.
        # Never fall back to an older activity if the newest one is invalid.
        latest = next((m for m in reversed(list(db["parentMessages"].values()))
                       if m["childId"] == child_id and (m.get("activity") or m.get("activityReset"))), None)
        return latest if latest and latest.get("activity") and self.valid_result(db, latest, child_id) else None

    def begin(self, data):
        from companion import identifier, stamp
        child_id, rid = str(data.get("childId", "")), str(data.get("requestId", ""))
        text = str(data.get("text", "")).strip()
        record_id = str(data.get("recordId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", rid) or not text or len(text) > 1000:
            raise self.error("请填写1000字以内的家长问题。")
        with self.store.transaction() as db:
            self.service.profile(db, child_id)
            old = db["parentRequests"].get(rid)
            if old:
                if (old["childId"] != child_id or old["text"] != text
                        or str(old.get("recordId") or "") != record_id):
                    raise self.error("这次请求的问题或目标记录与原请求不一致，请重新发送。", 409, "parent_request_conflict")
                return copy.deepcopy(old)
            if any(r["childId"] == child_id and r["status"] == "pending" for r in db["parentRequests"].values()):
                raise self.error("这个档案还有一个家长问题正在回答，请稍候或停止。", 409)
            if record_id and not self.source(db, record_id, child_id):
                raise self.error("这条依据不可用，请从当前档案重新选择。", 404)
            mid = identifier("parent")
            db["parentMessages"][mid] = {"id": mid, "childId": child_id, "role": "user", "text": text, "requestId": rid, "createdAt": stamp()}
            request = {"id": rid, "childId": child_id, "text": text, "recordId": record_id, "status": "pending", "createdAt": stamp()}
            db["parentRequests"][rid] = request
        threading.Thread(target=self.work, args=(copy.deepcopy(request),), name="parent-answer", daemon=True).start()
        return request

    def cancel(self, data):
        with self.store.transaction() as db:
            self.service.profile(db, data.get("childId"))
            r = db["parentRequests"].get(data.get("requestId"), {})
            if r.get("childId") != data.get("childId"):
                raise self.error("没有找到这次家长提问。", 404)
            if r["status"] == "pending":
                r["status"] = "cancelled"
            return {"status": r["status"]}

    @staticmethod
    def window(selection, now, question="", previous=None):
        today = now.astimezone(TZ).date()
        mode = selection.get("mode")
        if mode == "all":
            quote = selection.get("timeQuote", "")
            if not quote or quote not in question:
                raise ValueError("请明确要查看的时间范围")
            return None, today, "全部历史（本次仅选取相关记录）"
        if mode == "recent":
            days = int(selection.get("days", 7))
            if not 1 <= days <= 366:
                raise ValueError("请缩小查询时间范围")
            first, last = today - timedelta(days=days - 1), today
        elif mode in ("day", "week", "month"):
            offset = int(selection.get("offset", 0))
            if not -366 <= offset <= 0:
                raise ValueError("请明确过去的查询时间范围")
            if mode == "day":
                first = last = today + timedelta(days=offset)
            elif mode == "week":
                first = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
                last = min(first + timedelta(days=6), today)
            else:
                year, month = divmod(today.year * 12 + today.month - 1 + offset, 12)
                first = date(year, month + 1, 1)
                last = min(date(year, month + 1, calendar.monthrange(year, month + 1)[1]), today)
        elif mode == "previous" and previous:
            first = date.fromisoformat(previous["from"]) if previous.get("from") else None
            last = date.fromisoformat(previous["to"])
        elif mode == "range":
            quote = selection.get("timeQuote", "")
            if not quote or quote not in question:
                raise ValueError("请明确起止日期")
            def explicit_day(value):
                # Missing year means this application year, never model recall.
                if re.fullmatch(r"\d{2}-\d{2}", value):
                    value = f"{today.year}-{value}"
                elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or value[:4] not in quote:
                    raise ValueError("请说明年份或使用今天、昨天、上周等范围")
                return date.fromisoformat(value)
            first, last = (explicit_day(selection[k]) for k in ("fromDate", "toDate"))
            if first > last:
                raise ValueError("日期范围无效")
        else:
            raise ValueError("请说明希望查看哪段时间，例如最近一周或具体日期")
        return first, last, f"{first.isoformat() if first else '记录起始'} 至 {last.isoformat()}（Asia/Shanghai）"

    @staticmethod
    def within(value, first, last):
        try:
            day = datetime.fromisoformat(local_time(value)).date()
            return (first is None or day >= first) and day <= last
        except (ValueError, TypeError):
            return False

    def activity_requirements(self, selected, previous, intent, question):
        """Apply explicit semantic edits; omitted requirements remain authoritative."""
        previous = previous or {}
        constraints = list(previous.get("constraints", [])) if intent == "continue" else []
        count = previous.get("stepCount") if intent == "continue" else None
        edits = selected.get("constraintEdits", [])
        replacements, additions = {}, []
        if not isinstance(edits, list):
            raise self.error("活动要求尚未整理完整，请重新说明要改哪一项。", 502, "parent_activity_contract")
        for edit in edits:
            index, text, quote = edit.get("index"), edit.get("text"), edit.get("quote")
            if (type(index) is not int or index < -1 or index >= len(constraints)
                    or not isinstance(text, str) or len(text) > 300
                    or not isinstance(quote, str) or not quote.strip() or quote not in question
                    or index >= 0 and index in replacements):
                raise self.error("未能核对这次活动要求的变更，原方案保留。", 502, "parent_activity_contract")
            if index == -1:
                if text.strip():
                    additions.append(text.strip())
            else:
                replacements[index] = text.strip()
        constraints = [replacements.get(i, text) for i, text in enumerate(constraints)] + additions
        constraints = list(dict.fromkeys(text for text in constraints if text))
        if len(constraints) > 24:
            raise self.error("活动要求较多，请先简化安排；原方案保留。", 400, "parent_activity_contract")
        new_count = selected.get("stepCount")
        if new_count is not None:
            quote = selected.get("stepCountQuote", "")
            if type(new_count) is not int or not 0 <= new_count <= 12 or not quote or quote not in question:
                raise self.error("未能核对步骤数量的变更，原方案保留。", 502, "parent_activity_contract")
            count = new_count or None
        return {"constraints": constraints, "stepCount": count}

    def context(self, request):
        child_id = request["childId"]
        db = self.store.read()
        profile = self.service.profile(db, child_id)
        blocked, invalid = validity(db, child_id)
        records = sorted((m for m in db["memoryItems"].values() if m["id"] not in invalid and active(m, child_id, m.get("conversationId"))), key=lambda m: m.get("updatedAt", ""), reverse=True)
        conversations = sorted((c for c in db["conversations"].values() if c.get("childId") == child_id and not c.get("deleted")), key=lambda c: c["startedAt"], reverse=True)
        raw = [m for m in db["messages"].values() if m.get("childId") == child_id and m["id"] not in blocked and not m.get("deleted") and m.get("status") == "completed"]
        history = [m for m in db["parentMessages"].values() if m["childId"] == child_id and m.get("requestId") != request["id"] and self.valid_result(db, m, child_id)][-6:]
        def parent_history(limit, parent_only=False):
            return [{"role": m["role"], "speaker": "parent" if m["role"] == "user" else "parent_assistant", "text": m["text"][:limit]}
                    for m in history if not parent_only or m["role"] == "user"]
        current_activity = self.current_activity(db, child_id)
        def record_view(m, include_quote=False):
            value = memory_view(m, include_quote)
            value["updatedAt"] = local_time(m.get("updatedAt"))
            value["sourceOccurredAt"] = [local_time(db["messages"][mid]["createdAt"]) for mid in m.get("sourceMessageIds", [])
                if mid in db["messages"] and db["messages"][mid].get("childId") == child_id and db["messages"][mid].get("role") == "user" and not db["messages"][mid].get("deleted")]
            return value
        directory = {"records": [record_view(m) for m in records[:40]], "conversations": []}
        for c in conversations[:40]:
            msgs = [m for m in raw if m["conversationId"] == c["id"]]
            directory["conversations"].append({"id": c["id"], "topic": c.get("title"), "startedAt": local_time(c["startedAt"]),
                "lastAt": local_time(msgs[-1]["createdAt"] if msgs else c["startedAt"]),
                "questionSpeaker": "child", "questions": [m["text"][:120] for m in msgs if m["role"] == "user"][-8:]})
        partial = len(records) > 40 or len(conversations) > 40
        while len(json.dumps(directory, ensure_ascii=False).encode()) > 18000:
            key = max(directory, key=lambda k: len(json.dumps(directory[k], ensure_ascii=False)))
            directory[key].pop()
            partial = True
        now = datetime.fromisoformat(request["createdAt"]).astimezone(TZ)
        previous_window = next((m.get("dateWindow") for m in reversed(history) if m.get("dateWindow")), None)
        selected, _ = self.model.complete({"now": now.isoformat(), "question": request["text"], "questionSpeaker": "parent",
            "recordId": request.get("recordId"), "directory": directory, "partial": partial,
            "currentActivity": current_activity["activity"] if current_activity else None,
            "previousWindow": previous_window,
            "parentHistory": parent_history(500)}, request["id"], purpose="parent_select")
        try:
            first, last, label = self.window(selected, now, request["text"], previous_window)
        except (ValueError, TypeError, KeyError) as exc:
            raise self.error(str(exc), 400, "parent_time_range")
        evidence, refs, message_ids = {}, {}, set()

        def add_message(m):
            if m["id"] in evidence:
                return
            evidence[m["id"]] = {"id": m["id"], "type": "message", "role": m["role"],
                "speaker": "child" if m["role"] == "user" else "child_assistant", "text": m["text"][:1600], "createdAt": local_time(m["createdAt"])}
            message_ids.add(m["id"])

        def add_record(m):
            evidence[m["id"]] = {**record_view(m, True), "type": "record"}
            refs[m["id"]] = reference(m)
            for mid in m.get("sourceMessageIds", []):
                msg = next((x for x in raw if x["id"] == mid), None)
                if msg and self.within(msg["createdAt"], first, last):
                    add_message(msg)

        chosen_records = set(selected.get("memoryIds", [])[:6]) & {m["id"] for m in directory["records"]}
        if request.get("recordId"):
            chosen_records.add(request["recordId"])
        for m in records:
            if m["id"] not in chosen_records:
                continue
            # A parent correction blocks the old answer/interpretation, but the
            # current edited record still has dated provenance. Do not discard
            # the corrected record merely because its old source is blocked.
            dates = [x.get("createdAt") for x in db["messages"].values()
                     if x["id"] in m.get("sourceMessageIds", []) and x.get("childId") == child_id and x.get("role") == "user" and not x.get("deleted")]
            if m["kind"] == "reminder" or any(self.within(d, first, last) for d in dates):
                add_record(m)
        selected_convs = set(selected.get("conversationIds", [])[:4]) & {c["id"] for c in directory["conversations"]}
        summaries = []
        for c in conversations:
            if c["id"] not in selected_convs:
                continue
            msgs = [m for m in raw if m["conversationId"] == c["id"] and self.within(m["createdAt"], first, last)]
            if len(msgs) > 20:
                partial = True
            for m in msgs[-20:]:
                add_message(m)
            exp = c.get("exploration", {})
            if exploration_valid(db, c, blocked) and set(exp.get("sourceMessageIds", [])).issubset(message_ids):
                view = exploration_view(db, exp)
                summaries.append({k: view.get(k) for k in ("topic", "focus", "difficulties", "attempts", "openQuestions")})
                for r in exp.get("memoryVersions", []):
                    add_record(db["memoryItems"][r["id"]])
        previous_activity = None
        activity_intent = selected.get("activityIntent", "continue" if selected.get("inheritActivity") else "none")
        if activity_intent in ("continue", "retain"):
            previous = current_activity
            if previous and all(self.within(db["messages"].get(mid, {}).get("createdAt"), first, last) for mid in previous.get("sourceMessageIds", [])):
                previous_activity = previous["activity"]
                for r in previous.get("memoryVersions", []):
                    add_record(db["memoryItems"][r["id"]])
                for mid in previous.get("sourceMessageIds", []):
                    add_message(db["messages"][mid])
        context = {"question": request["text"], "questionSpeaker": "parent", "child": {"nickname": profile["nickname"], "age": profile["age"]},
                   "now": now.isoformat(), "timezone": str(TZ), "activityIntent": activity_intent,
                   "timeWindow": label, "partial": partial, "evidence": list(evidence.values()), "summaries": summaries,
                   # For confirmations/readbacks, the saved plan supplies its
                   # content; older AI paraphrases must not become new constraints.
                   "parentHistory": parent_history(600, parent_only=activity_intent == "retain"),
                   "previousActivity": previous_activity}
        if activity_intent in ("new", "continue"):
            context["activityRequirements"] = self.activity_requirements(selected, previous_activity, activity_intent, request["text"])
        if len(json.dumps(context, ensure_ascii=False).encode()) > 28000:
            raise self.error("相关记录较多，请缩小到一个话题或时间段再问。", 400, "parent_capacity")
        inherited = ({"memoryVersions": previous.get("memoryVersions", []), "sourceMessageIds": previous.get("sourceMessageIds", [])}
                     if previous_activity else {"memoryVersions": [], "sourceMessageIds": []})
        return context, {"memoryVersions": list(refs.values()), "sourceMessageIds": sorted(message_ids), "timeWindow": label, "partial": partial,
                         "dateWindow": {"from": first.isoformat() if first else None, "to": last.isoformat()},
                         "activityReset": activity_intent in ("new", "clear"),
                         "inheritedActivity": inherited}, evidence

    def work(self, request):
        from companion import identifier, stamp, safe_suggestion
        try:
            context, dependencies, evidence = self.context(request)
            # Snapshot again after selection; never spend on an already stopped query.
            current = self.store.read()
            if self.service.closed.is_set() or current["parentRequests"][request["id"]]["status"] != "pending":
                return
            self.service.profile(current, request["childId"])
            if not self.valid_result(current, dependencies, request["childId"]):
                raise self.error("依据刚有更新，请重新提问。", 409)
            result, meta = self.model.complete(context, request["id"], purpose="parent_answer")
            answer = str(result.get("answer") or "").strip()
            if not answer or len(answer) > 4000:
                raise self.error("这次回答没有完整生成，请重试。", 502)
            sources = list(dict.fromkeys(result.get("sources", [])))[:4]
            used = set(result.get("usedEvidenceIds", [])) | set(sources)
            if any(s not in evidence for s in used):
                raise self.error("这次回答的依据未能核对，请重试。", 502, "parent_sources")
            # Timing-only confirmations/readbacks and unrelated queries cannot
            # replace the plan even if the answer model returns another card.
            activity = result.get("activity") if context["activityIntent"] in ("new", "continue") else None
            if activity:
                requirements = context["activityRequirements"]
                steps = activity.get("steps")
                if isinstance(steps, list):
                    if (not 1 <= len(steps) <= 12 or any(not isinstance(s, str) or not s.strip() for s in steps)
                            or requirements["stepCount"] is not None and len(steps) != requirements["stepCount"]):
                        raise self.error("活动步骤未符合已确认的安排，原方案保留，请重试。", 502, "parent_activity_contract")
                    activity["steps"] = "\n".join(f"{i+1}. {s.strip()}" for i, s in enumerate(steps))
                elif requirements["stepCount"] is not None:
                    raise self.error("活动步骤未完整整理，原方案保留，请重试。", 502, "parent_activity_contract")
                activity["constraints"] = requirements["constraints"]
                if requirements["stepCount"] is not None:
                    activity["stepCount"] = requirements["stepCount"]
                else:
                    activity.pop("stepCount", None)
                if not all(isinstance(activity.get(k), str) and activity[k].strip() for k in ("title", "materials", "steps", "adultAction", "why")):
                    raise self.error("活动步骤尚不完整，可以重新问一个更简单的活动。", 502)
                check = safe_suggestion({"title": activity["title"], "steps": "\n".join(activity[k] for k in ("materials", "steps", "adultAction")), "why": activity["why"]})
                if not check:
                    raise self.error("这项活动暂不适合直接操作，请换一个更简单的桌边观察。", 422)
            draft = result.get("draft")
            if draft:
                if draft.get("action") not in ("add", "edit") or not 0 < len(str(draft.get("summary", "")).strip()) <= 300:
                    raise self.error("提醒草稿没有完整生成，请重新说明。", 502)
                if draft["action"] == "edit":
                    target = evidence.get(draft.get("memoryId"), {})
                    if target.get("type") != "record":
                        raise self.error("未能定位要修改的记录，请从记忆详情修改。", 409)
                    draft["expectedVersion"] = target["version"]
                else:
                    draft["memoryId"] = ""
                draft["status"] = "pending"
                if draft["action"] == "edit":
                    used.add(draft["memoryId"])
            inherited = (dependencies.pop("inheritedActivity") if activity or context["activityIntent"] == "retain"
                         else {"memoryVersions": [], "sourceMessageIds": []})
            dependencies.pop("inheritedActivity", None)
            refs = {r["id"]: r for r in dependencies["memoryVersions"] if r["id"] in used}
            refs.update({r["id"]: r for r in inherited["memoryVersions"]})
            dependencies["memoryVersions"] = list(refs.values())
            dependencies["sourceMessageIds"] = sorted(set(dependencies["sourceMessageIds"]) & used | set(inherited["sourceMessageIds"]))
            with self.store.transaction() as db:
                r = db["parentRequests"][request["id"]]
                if r["status"] != "pending" or self.service.closed.is_set():
                    return
                self.service.profile(db, request["childId"])
                if not self.valid_result(db, dependencies, request["childId"]):
                    raise self.error("依据刚有更新，请重新提问。", 409)
                mid = identifier("parent")
                db["parentMessages"][mid] = {"id": mid, "childId": request["childId"], "role": "assistant", "text": answer,
                    "advice": "" if context["activityIntent"] == "retain" else clean_advice(result.get("advice")),
                    "sources": sources, "activity": activity, "draft": draft,
                    "requestId": r["id"], "createdAt": stamp(), "model": meta.get("model"), **dependencies}
                r.update(status="completed", finishedAt=stamp())
        except Exception as exc:
            from companion import ProductError
            with self.store.transaction() as db:
                r = db["parentRequests"].get(request["id"], {})
                if r.get("status") == "pending":
                    r.update(status="failed", error=str(exc) if isinstance(exc, ProductError) else "这次没有回答完成，原问题保留了，请重试。", finishedAt=stamp())

    def save_draft(self, data):
        """One explicit save, reusing child validation/versioning/invalidation.

        A persisted saving marker prevents duplicate adds after an uncertain crash;
        a known validation conflict is retryable. No second memory version system.
        """
        from companion import ProductError, stamp
        child_id, mid = data.get("childId"), data.get("messageId")
        with self.save_lock:
            with self.store.transaction() as db:
                self.service.profile(db, child_id)
                m = db["parentMessages"].get(mid, {})
                draft = m.get("draft")
                if m.get("childId") != child_id or not draft:
                    raise self.error("没有找到这个档案的提醒草稿。", 404)
                if draft["status"] == "saved":
                    return {"memoryId": draft["savedMemoryId"], "status": "saved"}
                if draft["status"] != "pending":
                    raise self.error("上次保存结果尚未确认，请先到记忆与提醒查看，避免重复添加。", 409)
                if draft["action"] == "edit":
                    target = db["memoryItems"].get(draft["memoryId"], {})
                    if not active(target, child_id, target.get("conversationId")):
                        raise self.error("这条记录已撤回或不可用，草稿未保存。请先在孩子档案中明确恢复记录。", 409, "parent_target_inactive")
                if draft["action"] == "add" and not self.valid_result(db, m, child_id):
                    raise self.error("草稿依据已经变化，请重新整理提醒。", 409)
                payload = {"childId": child_id, "summary": data.get("summary"), "topic": data.get("topic", draft.get("topic", "")),
                           "scope": data.get("scope", draft.get("scope")), "action": "edit",
                           "expectedVersion": data.get("expectedVersion", draft.get("expectedVersion"))}
                # A scoped draft has no implicit "latest conversation". The
                # parent confirms a concrete owned conversation in the form.
                if payload["scope"] == "conversation" and (draft["action"] == "add" or target.get("kind") == "reminder"):
                    conversation_id = str(data.get("conversationId", draft.get("conversationId", "")) or "")
                    if "conversationId" not in data and "conversationId" not in draft and draft["action"] == "edit":
                        conversation_id = target.get("conversationId", "")
                    conversation = db["conversations"].get(conversation_id, {})
                    if not conversation_id:
                        raise self.error("请选择这条提醒适用的聊天；未选择时不会保存或扩大范围。", 400, "reminder_conversation")
                    if conversation.get("childId") != child_id or conversation.get("deleted"):
                        raise self.error("所选聊天不属于当前档案或已被删除，请重新选择。", 409, "reminder_conversation")
                    payload["conversationId"] = conversation_id
                else:
                    payload["conversationId"] = ""
                saved_draft = copy.deepcopy(draft)
                draft["status"] = "saving"
            try:
                if saved_draft["action"] == "edit":
                    item = self.service.update_memory(saved_draft["memoryId"], payload)
                else:
                    item = self.service.add_reminder(payload)
            except ProductError:
                with self.store.transaction() as db:
                    db["parentMessages"][mid]["draft"]["status"] = "pending"
                raise
            with self.store.transaction() as db:
                db["parentMessages"][mid]["draft"].update(status="saved", savedMemoryId=item["id"], savedAt=stamp(),
                    summary=item["summary"], topic=item.get("topic", ""), scope=item.get("scope"),
                    conversationId=item.get("conversationId", ""))
            return {"memoryId": item["id"], "status": "saved"}
