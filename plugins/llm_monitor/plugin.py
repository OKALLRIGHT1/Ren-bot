from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from modules.llm import get_recent_llm_metrics


class Plugin:
    name = "llm_monitor"
    type = "react"

    async def run(self, args: str, ctx: Dict[str, Any]) -> str:
        raw = (args or "").strip()
        action = "summary"
        limit = 50
        if raw:
            parts = [x.strip() for x in raw.split("|||")]
            if parts and parts[0]:
                action = parts[0].lower()
            if len(parts) > 1 and parts[1].isdigit():
                limit = max(1, min(300, int(parts[1])))

        data = get_recent_llm_metrics(limit)
        if not data:
            return "暂无模型调用记录。"

        if action == "recent":
            return self._recent(data)
        return self._summary(data)

    def _summary(self, data: List[Dict[str, Any]]) -> str:
        by_model = defaultdict(lambda: {"ok": 0, "fail": 0, "dur": 0, "n": 0})
        for x in data:
            key = x.get("model_key", "unknown")
            ok = bool(x.get("success", False))
            by_model[key]["n"] += 1
            by_model[key]["dur"] += int(x.get("duration_ms", 0))
            if ok:
                by_model[key]["ok"] += 1
            else:
                by_model[key]["fail"] += 1

        lines = [f"最近 {len(data)} 次模型调用摘要："]
        for model, s in sorted(by_model.items(), key=lambda kv: kv[1]["n"], reverse=True):
            avg = int(s["dur"] / s["n"]) if s["n"] else 0
            lines.append(f"- {model}: total={s['n']} ok={s['ok']} fail={s['fail']} avg={avg}ms")
        return "\n".join(lines)

    def _recent(self, data: List[Dict[str, Any]]) -> str:
        out = [f"最近 {len(data)} 条调用记录："]
        for x in data[-60:]:
            ts = datetime.fromtimestamp(float(x.get("ts", 0))).strftime("%H:%M:%S")
            ok = "OK" if x.get("success") else "FAIL"
            out.append(
                f"- [{ts}] {ok} task={x.get('task_type')} model={x.get('model_key')} "
                f"dur={x.get('duration_ms', 0)}ms err={str(x.get('error',''))[:80]}"
            )
        return "\n".join(out)
