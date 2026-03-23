import random
import re
from typing import Dict, Tuple

from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors

logger = get_logger()


class Plugin:
    _FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９ｄＤ＋－", "0123456789dD+-")

    @handle_plugin_errors("骰子")
    async def run(self, args, ctx):
        raw_message = str(args or "").strip()
        if not raw_message:
            return self._help_text()

        clean_msg = raw_message.translate(self._FULLWIDTH_TRANS).lower().replace("。", ".")

        try:
            if self._wants_help(clean_msg):
                return self._help_text()

            # 1) CoC 模式 (.ra / .rc / .sc)
            if re.search(r"(?:^|\s)\.(?:ra|rc|sc)\b", clean_msg):
                target_match = re.search(r"(\d+)\s*$", clean_msg)
                if target_match:
                    target_val = int(target_match.group(1))
                    temp_text = re.sub(r"(?:^|\s)\.(?:ra|rc|sc)\b", "", raw_message, flags=re.IGNORECASE)
                    skill_name = temp_text.replace(str(target_val), "").strip() or "属性"
                    return self._coc_check(target_val, skill_name)
                return self._general_roll(1, 100, None, "1d100", rule_mode="coc")

            # 2) 选项表模式
            option_map, option_found = self._parse_options(raw_message)
            dice_search = re.search(r"(\d+)[dD](\d+)([+-]\d+)?", clean_msg)
            if option_found and dice_search:
                n = int(dice_search.group(1))
                f = int(dice_search.group(2))
                m = dice_search.group(3)
                return self._option_roll(n, f, m, option_map)

            # 3) 标准 D&D 模式
            std_match = re.search(r"(?:^|\s|[rR\.])(\d+)[dD](\d+)([+-]\d+)?", clean_msg)
            if std_match:
                n = int(std_match.group(1))
                f = int(std_match.group(2))
                m = std_match.group(3)
                expr = f"{n}d{f}{m if m else ''}"
                return self._general_roll(n, f, m, expr, rule_mode="dnd")

            single_match = re.search(r"(?:^|\s|[rR\.])[dD](\d+)([+-]\d+)?", clean_msg)
            if single_match:
                f = int(single_match.group(1))
                m = single_match.group(2)
                expr = f"1d{f}{m if m else ''}"
                return self._general_roll(1, f, m, expr, rule_mode="dnd")

            if ".r" in clean_msg:
                return self._general_roll(1, 20, None, "1d20", rule_mode="dnd")

            return "骰子指令未识别，请检查格式。"

        except Exception:
            import traceback
            logger.error(f"骰子异常: {traceback.format_exc()}")
            return "骰子炸了：发生异常，请重试。"

    def _help_text(self) -> str:
        return "\n".join([
            "🎲 骰子用法示例：",
            "- .r 1d20 / .r 2d100+3",
            "- .ra 80（CoC 判定）",
            "- 2d6（搭配 .r 前缀更稳）",
            "- 选项表：前面给编号列表，再写 1dN",
        ])

    def _wants_help(self, text: str) -> bool:
        low = str(text or "").strip().lower()
        if not low:
            return True
        compact = re.sub(r"\s+", "", low)
        return ("骰子用法" in compact) or ("骰子帮助" in compact)
    def _parse_options(self, text: str) -> Tuple[Dict[int, str], bool]:
        options: Dict[int, str] = {}
        found = False
        try:
            lines = text.split("\n")
            line_pattern = re.compile(r"^\s*(\d+)(?:\s*[-~]\s*(\d+))?[\s\.:、]+(.*)$")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if re.match(r"^[.。]?[rR]?\s*\d*[dD]\d+", line):
                    continue
                match = line_pattern.match(line)
                if match:
                    content = match.group(3).strip()
                    if re.match(r"^[dD]?\d+$", content):
                        continue
                    found = True
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else start
                    if end - start > 1000:
                        continue
                    for i in range(start, end + 1):
                        options[i] = content
        except Exception:
            return {}, False
        return options, found

    def _coc_check(self, target_val: int, skill_name: str) -> str:
        roll = random.randint(1, 100)
        is_fumble = False
        if target_val < 50:
            if roll >= 96:
                is_fumble = True
        else:
            if roll == 100:
                is_fumble = True

        if roll == 1:
            status_text = "【大成功! Critical】"
        elif is_fumble:
            status_text = "【大失败! Fumble】"
        elif roll <= target_val // 5:
            status_text = "【极难成功 / Extreme】"
        elif roll <= target_val // 2:
            status_text = "【困难成功 / Hard】"
        elif roll <= target_val:
            status_text = "【成功 / Success】"
        else:
            status_text = "【失败 / Failure】"

        return f"🎲 CoC检定({skill_name}) {target_val} -> 1d100={roll} {status_text}"

    def _general_roll(self, num_dice: int, num_faces: int, modifier_str: str, expression: str, rule_mode: str) -> str:
        rolls = [random.randint(1, num_faces) for _ in range(num_dice)]
        total_raw = sum(rolls)
        modifier = int(modifier_str) if modifier_str else 0
        final_total = total_raw + modifier
        status_text = ""

        details_str = f" {rolls}" if num_dice > 1 else ""

        if num_dice == 1:
            if rule_mode == "dnd":
                if num_faces == 20:
                    if total_raw == 20:
                        status_text = "【大成功 / Nat 20】"
                    elif total_raw == 1:
                        status_text = "【大失败 / Nat 1】"
                elif num_faces == 100:
                    if final_total > 95:
                        status_text = "【大成功 (Excellent)】"
                    elif final_total <= 5:
                        status_text = "【大失败 (Fumble)】"
            elif rule_mode == "coc":
                if num_faces == 100:
                    if final_total == 1:
                        status_text = "【大成功! Critical】"
                    elif final_total >= 96:
                        status_text = "【大失败! Fumble】"
                    elif final_total <= 5:
                        status_text = "【极佳 (Extreme)】"

        return f"🎲 {expression}={final_total}{details_str} {status_text}".strip()

    def _option_roll(self, num_dice: int, num_faces: int, modifier_str: str, option_map: Dict[int, str]) -> str:
        rolls = [random.randint(1, num_faces) for _ in range(num_dice)]
        final_total = sum(rolls) + (int(modifier_str) if modifier_str else 0)

        initial_result = option_map.get(final_total, "（未命中）")
        mod_disp = modifier_str if modifier_str else ""
        header_text = f"🎲 选项投掷 {num_dice}d{num_faces}{mod_disp}={final_total}"

        final_outcome = initial_result
        if "/" in initial_result:
            choices = [c.strip() for c in initial_result.split("/") if c.strip()]
            if len(choices) > 1:
                sub_roll = random.randint(1, len(choices))
                selected_choice = choices[sub_roll - 1]
                final_outcome = selected_choice
                header_text += f" -> 命中: 【{initial_result}】\n👉 自动判定 1d{len(choices)}={sub_roll} -> 结果: 【{selected_choice}】"
            else:
                header_text += f" -> 【{initial_result}】"
        else:
            header_text += f" -> 【{initial_result}】"

        return f"{header_text}\n结果：{final_outcome}".strip()