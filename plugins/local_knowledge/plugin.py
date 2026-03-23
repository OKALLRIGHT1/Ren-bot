import os
import glob
import asyncio
import time


class Plugin:
    def _legacy_knowledge_dirs(self):
        candidates = []
        legacy_dir = os.path.abspath("./knowledge_docs")
        if os.path.isdir(legacy_dir):
            candidates.append(legacy_dir)
        return candidates

    def _read_setting(self, settings, key, default):
        value = settings.get(key, default)
        if isinstance(value, dict):
            return value.get("default", default)
        return default if value is None else value

    def _get_knowledge_dirs(self):
        settings = getattr(self, "settings", {}) or {}
        dirs = self._read_setting(settings, "knowledge_source_dirs", [])
        normalized = []
        if isinstance(dirs, list):
            for item in dirs:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    enabled = bool(item.get("enabled", True))
                    if path and enabled:
                        normalized.append(path)
                else:
                    path = str(item).strip()
                    if path:
                        normalized.append(path)
        for item in self._legacy_knowledge_dirs():
            if item not in normalized:
                normalized.append(item)
        return normalized

    def _scan_supported_files(self, content):
        files = []
        for ext in ["*.md", "*.txt", "*.py", "*.json"]:
            files.extend(glob.glob(os.path.join(content, "**", ext), recursive=True))
        return files

    def _slow_ingest_config(self):
        settings = getattr(self, "settings", {}) or {}
        enabled = bool(self._read_setting(settings, "slow_ingest_enabled", True))
        batch = int(self._read_setting(settings, "slow_ingest_batch", 20) or 20)
        sleep_ms = int(
            self._read_setting(settings, "slow_ingest_sleep_ms", 1200) or 1200
        )
        adaptive = bool(self._read_setting(settings, "adaptive_slow_ingest", True))
        return enabled, max(1, batch), max(0, sleep_ms), adaptive

    async def gui_ingest_configured_dirs(self, context: dict) -> str:
        brain = context.get("brain")
        if not brain:
            return "❌ 内部错误：无法访问记忆系统 (Brain Not Found)"
        self.brain = brain

        dirs = self._get_knowledge_dirs()
        if not dirs:
            return "⚠️ 还没有在 GUI 里配置知识目录。"

        valid_dirs = [item for item in dirs if os.path.isdir(item)]
        if not valid_dirs:
            return "⚠️ 当前配置的知识目录都不存在，请先检查路径。"

        def _do_import():
            file_count = 0
            added_count = 0
            skipped_count = 0
            stats_before = getattr(brain, "get_knowledge_stats", lambda: {})() or {}
            slow_enabled, slow_batch, slow_sleep_ms, adaptive = (
                self._slow_ingest_config()
            )
            adaptive_hits = 0
            dynamic_sleep_ms = slow_sleep_ms
            last_rate_limit_hits = int(stats_before.get("rate_limit_hits", 0))
            for one_dir in valid_dirs:
                for fpath in self._scan_supported_files(one_dir):
                    file_count += 1
                    result = brain.import_knowledge_from_file(fpath)
                    if isinstance(result, dict):
                        added_count += int(result.get("added", 0))
                        skipped_count += int(result.get("skipped", 0))
                    else:
                        added_count += int(result or 0)
                    if adaptive:
                        current_stats = (
                            getattr(brain, "get_knowledge_stats", lambda: {})() or {}
                        )
                        current_rate_limit_hits = int(
                            current_stats.get("rate_limit_hits", 0)
                        )
                        if current_rate_limit_hits > last_rate_limit_hits:
                            adaptive_hits += (
                                current_rate_limit_hits - last_rate_limit_hits
                            )
                            dynamic_sleep_ms = min(
                                max(dynamic_sleep_ms * 2, slow_sleep_ms), 10000
                            )
                            last_rate_limit_hits = current_rate_limit_hits
                        elif dynamic_sleep_ms > slow_sleep_ms:
                            dynamic_sleep_ms = max(
                                slow_sleep_ms, dynamic_sleep_ms - 300
                            )
                    if (
                        slow_enabled
                        and dynamic_sleep_ms > 0
                        and file_count % slow_batch == 0
                    ):
                        time.sleep(dynamic_sleep_ms / 1000.0)
            stats_after = getattr(brain, "get_knowledge_stats", lambda: {})() or {}
            rate_limit_hits = int(stats_after.get("rate_limit_hits", 0)) - int(
                stats_before.get("rate_limit_hits", 0)
            )
            fallback_uses = int(stats_after.get("fallback_uses", 0)) - int(
                stats_before.get("fallback_uses", 0)
            )
            return (
                file_count,
                added_count,
                skipped_count,
                rate_limit_hits,
                fallback_uses,
                adaptive_hits,
            )

        try:
            (
                file_count,
                added_count,
                skipped_count,
                rate_limit_hits,
                fallback_uses,
                adaptive_hits,
            ) = await asyncio.to_thread(_do_import)
            return (
                f"✅ GUI 学习完成！共扫描 {file_count} 个文件，新增 {added_count} 条知识片段，"
                f"跳过 {skipped_count} 条已存在片段。限频 {rate_limit_hits} 次，fallback {fallback_uses} 次，自适应触发 {adaptive_hits} 次。"
            )
        except Exception as e:
            return f"❌ GUI 学习过程中出错: {e}"

    async def run(self, args: str, context: dict) -> str:
        # 从上下文获取 brain 实例
        brain = context.get("brain")
        if not brain:
            return "❌ 内部错误：无法访问记忆系统 (Brain Not Found)"

        if "|||" not in args:
            return "❌ 格式错误，请使用: 指令 ||| 内容"

        cmd, content = args.split("|||", 1)
        cmd = cmd.strip().lower()
        content = content.strip()

        # --- 功能 1: 学习 (Ingest) ---
        if cmd == "learn":
            if not os.path.exists(content):
                return f"❌ 路径不存在: {content}"

            files = self._scan_supported_files(content)

            if not files:
                return "⚠️ 该目录下没有找到支持的文档格式 (.md/.txt/.py)"

            total_chunks = 0

            # 这是一个耗时操作，建议放到线程池
            def _do_import():
                added = 0
                skipped = 0
                stats_before = getattr(brain, "get_knowledge_stats", lambda: {})() or {}
                slow_enabled, slow_batch, slow_sleep_ms, adaptive = (
                    self._slow_ingest_config()
                )
                processed = 0
                adaptive_hits = 0
                dynamic_sleep_ms = slow_sleep_ms
                last_rate_limit_hits = int(stats_before.get("rate_limit_hits", 0))
                for fpath in files:
                    processed += 1
                    result = brain.import_knowledge_from_file(fpath)
                    if isinstance(result, dict):
                        added += int(result.get("added", 0))
                        skipped += int(result.get("skipped", 0))
                    else:
                        added += int(result or 0)
                    if adaptive:
                        current_stats = (
                            getattr(brain, "get_knowledge_stats", lambda: {})() or {}
                        )
                        current_rate_limit_hits = int(
                            current_stats.get("rate_limit_hits", 0)
                        )
                        if current_rate_limit_hits > last_rate_limit_hits:
                            adaptive_hits += (
                                current_rate_limit_hits - last_rate_limit_hits
                            )
                            dynamic_sleep_ms = min(
                                max(dynamic_sleep_ms * 2, slow_sleep_ms), 10000
                            )
                            last_rate_limit_hits = current_rate_limit_hits
                        elif dynamic_sleep_ms > slow_sleep_ms:
                            dynamic_sleep_ms = max(
                                slow_sleep_ms, dynamic_sleep_ms - 300
                            )
                    if (
                        slow_enabled
                        and dynamic_sleep_ms > 0
                        and processed % slow_batch == 0
                    ):
                        time.sleep(dynamic_sleep_ms / 1000.0)
                stats_after = getattr(brain, "get_knowledge_stats", lambda: {})() or {}
                rate_limit_hits = int(stats_after.get("rate_limit_hits", 0)) - int(
                    stats_before.get("rate_limit_hits", 0)
                )
                fallback_uses = int(stats_after.get("fallback_uses", 0)) - int(
                    stats_before.get("fallback_uses", 0)
                )
                return added, skipped, rate_limit_hits, fallback_uses, adaptive_hits

            try:
                (
                    total_added,
                    total_skipped,
                    rate_limit_hits,
                    fallback_uses,
                    adaptive_hits,
                ) = await asyncio.to_thread(_do_import)
                return (
                    f"✅ 学习完成！共扫描 {len(files)} 个文件，新增 {total_added} 条知识片段，"
                    f"跳过 {total_skipped} 条已存在片段。限频 {rate_limit_hits} 次，fallback {fallback_uses} 次，自适应触发 {adaptive_hits} 次。"
                )
            except Exception as e:
                return f"❌ 学习过程中出错: {e}"

        # --- 功能 2: 搜索 (Search) ---
        elif cmd == "search":
            # 调用 brain 的检索方法
            # 注意：brain._retrieve_knowledge 是内部方法，但Python里可以直接调
            # 或者你可以给 AdvancedMemorySystem 加一个 public 方法
            try:
                results = await asyncio.to_thread(brain.search_knowledge, content, 3)
                if not results:
                    return "📭 知识库中没有找到相关内容。"

                return f"📚 检索结果:\n" + "\n---\n".join(results)
            except Exception as e:
                return f"❌ 检索失败: {e}"

        return "❌ 未知指令，请使用 learn 或 search"
