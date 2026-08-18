import os
import glob
import asyncio


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

    def list_configured_learn_files(self):
        files = []
        seen = set()
        for one_dir in self._get_knowledge_dirs():
            if not os.path.isdir(one_dir):
                continue
            for fpath in self._scan_supported_files(one_dir):
                key = os.path.normcase(os.path.abspath(fpath))
                if key in seen:
                    continue
                seen.add(key)
                files.append(fpath)
        return files

    def _emit_progress(self, progress_callback, payload):
        if not callable(progress_callback):
            return
        try:
            progress_callback(payload)
        except Exception:
            pass

    def _import_one_file(
        self,
        brain,
        fpath,
        *,
        progress_callback=None,
        file_index=1,
        file_count=1,
    ):
        def on_progress(info):
            payload = dict(info or {})
            payload.setdefault("file_index", file_index)
            payload.setdefault("file_count", file_count)
            payload.setdefault("file_path", fpath)
            self._emit_progress(progress_callback, payload)

        if progress_callback:
            self._emit_progress(
                progress_callback,
                {
                    "stage": "prepared",
                    "batch": 0,
                    "batches": 1,
                    "total": 0,
                    "added": 0,
                    "skipped": 0,
                    "file_index": file_index,
                    "file_count": file_count,
                    "file_path": fpath,
                },
            )
        return brain.import_knowledge_from_file(
            fpath, progress_callback=on_progress if progress_callback else None
        )

    def _ingest_paths(self, brain, files, *, progress_callback=None):
        from services.gui_api.knowledge_service import ingest_knowledge_paths

        enabled, batch, sleep_ms, adaptive = self._slow_ingest_config()
        return ingest_knowledge_paths(
            files,
            import_file=lambda path, progress_callback=None: self._import_one_file(
                brain, path, progress_callback=progress_callback
            ),
            get_stats=lambda: getattr(brain, "get_knowledge_stats", lambda: {})() or {},
            on_progress=progress_callback,
            slow={
                "enabled": enabled,
                "batch": batch,
                "sleep_ms": sleep_ms,
                "adaptive": adaptive,
            },
        )

    @staticmethod
    def _knowledge_store(context):
        runtime = context or {}
        return runtime.get("knowledge") or runtime.get("brain")

    def _slow_ingest_config(self):
        settings = getattr(self, "settings", {}) or {}
        enabled = bool(self._read_setting(settings, "slow_ingest_enabled", True))
        batch = int(self._read_setting(settings, "slow_ingest_batch", 20) or 20)
        sleep_ms = int(
            self._read_setting(settings, "slow_ingest_sleep_ms", 1200) or 1200
        )
        adaptive = bool(self._read_setting(settings, "adaptive_slow_ingest", True))
        return enabled, max(1, batch), max(0, sleep_ms), adaptive

    async def gui_ingest_configured_dirs(
        self, context: dict, progress_callback=None
    ) -> str:
        brain = self._knowledge_store(context)
        if not brain:
            return "❌ 内部错误：无法访问记忆系统 (Brain Not Found)"
        self.brain = brain

        dirs = self._get_knowledge_dirs()
        if not dirs:
            return "⚠️ 还没有在 GUI 里配置知识目录。"

        valid_dirs = [item for item in dirs if os.path.isdir(item)]
        if not valid_dirs:
            return "⚠️ 当前配置的知识目录都不存在，请先检查路径。"

        files = self.list_configured_learn_files()

        try:
            payload = await asyncio.to_thread(
                self._ingest_paths,
                brain,
                files,
                progress_callback=progress_callback,
            )
            file_count = int(payload.get("file_count") or 0)
            added_count = int(payload.get("added") or 0)
            skipped_count = int(payload.get("skipped") or 0)
            failed_count = int(payload.get("failed") or 0)
            rate_limit_hits = int(payload.get("rate_limit_hits") or 0)
            fallback_uses = int(payload.get("fallback_uses") or 0)
            adaptive_hits = int(payload.get("adaptive_hits") or 0)
            prefix = "⚠️ GUI 学习完成，但有文件失败" if failed_count else "✅ GUI 学习完成"
            return (
                f"{prefix}！共扫描 {file_count} 个文件，新增 {added_count} 条知识片段，"
                f"跳过 {skipped_count} 条已存在片段，失败 {failed_count} 个文件。"
                f"限频 {rate_limit_hits} 次，fallback {fallback_uses} 次，自适应触发 {adaptive_hits} 次。"
            )
        except Exception as e:
            return f"❌ GUI 学习过程中出错: {e}"

    async def run(self, args: str, context: dict) -> str:
        brain = self._knowledge_store(context)
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

            try:
                payload = await asyncio.to_thread(
                    self._ingest_paths,
                    brain,
                    files,
                    progress_callback=context.get("progress_callback"),
                )
                total_added = int(payload.get("added") or 0)
                total_skipped = int(payload.get("skipped") or 0)
                total_failed = int(payload.get("failed") or 0)
                rate_limit_hits = int(payload.get("rate_limit_hits") or 0)
                fallback_uses = int(payload.get("fallback_uses") or 0)
                adaptive_hits = int(payload.get("adaptive_hits") or 0)
                prefix = "⚠️ 学习完成，但有文件失败" if total_failed else "✅ 学习完成"
                return (
                    f"{prefix}！共扫描 {len(files)} 个文件，新增 {total_added} 条知识片段，"
                    f"跳过 {total_skipped} 条已存在片段，失败 {total_failed} 个文件。"
                    f"限频 {rate_limit_hits} 次，fallback {fallback_uses} 次，自适应触发 {adaptive_hits} 次。"
                )
            except Exception as e:
                return f"❌ 学习过程中出错: {e}"

        # --- 功能 2: 搜索 (Search) ---
        elif cmd == "search":
            # 调用 brain 的检索方法
            # 注意：brain._retrieve_knowledge 是内部方法，但Python里可以直接调
            # 或者你可以给 AdvancedMemorySystem 加一个 public 方法
            try:
                from modules.memory.retrieval import format_knowledge_hits_for_display

                hits = await asyncio.to_thread(brain.search_knowledge, content, 3)
                results = format_knowledge_hits_for_display(hits)
                if not results:
                    return "📭 知识库中没有找到相关内容。"

                return f"📚 检索结果:\n" + "\n---\n".join(results)
            except Exception as e:
                return f"❌ 检索失败: {e}"

        return "❌ 未知指令，请使用 learn 或 search"
