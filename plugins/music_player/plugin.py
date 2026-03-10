import os
import random
import difflib
import asyncio
import json
import time
from core.logger import get_logger
from plugins.plugin_utils import handle_plugin_errors, safe_get_context

# 🟢 引入 mutagen 读取元数据
try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
except ImportError:
    mutagen = None

logger = get_logger()


class Plugin:
    def __init__(self):
        self.config_file = os.path.join(os.path.dirname(__file__), "config.json")
        self.music_directories = []
        self.favorites = []
        self.aliases = []

        # 🟢 音乐库缓存
        self.library = []
        self.last_scan_time = 0
        self._scan_task = None

        self._load_config()

    async def start(self):
        """插件启动时调用（事件循环已运行）"""
        if mutagen and not self._scan_task:
            logger.info("🎵 启动音乐库扫描任务...")
            self._scan_task = asyncio.create_task(self._scan_library_async())
        elif not mutagen:
            logger.warning("⚠️ 未安装 mutagen 库，只能使用文件名匹配")

    async def stop(self):
        """插件停止时调用"""
        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            logger.info("🎵 音乐库扫描任务已停止")

    def _load_config(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.full_config = json.load(f)

            settings = self.full_config.get('settings', {})
            music_dirs = settings.get('music_directories', {})
            self.music_directories = music_dirs.get('value', music_dirs.get('default', []))

            fav_setting = settings.get('favorites', {})
            self.favorites = fav_setting.get('value', fav_setting.get('default', []))

            self.aliases = self.full_config.get('aliases', [])
            self.aliases.sort(key=len, reverse=True)

        except Exception as e:
            logger.error(f"加载配置失败: {e}")

    def _save_favorites(self):
        try:
            self.full_config['settings']['favorites']['value'] = self.favorites
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.full_config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    async def _scan_library_async(self):
        """后台扫描音乐库并提取标签"""
        logger.info("🎵 开始构建音乐库索引...")
        start_t = time.time()

        extensions = {'.mp3', '.flac', '.wav', '.m4a', '.ogg'}
        new_lib = []

        for folder in self.music_directories:
            if not os.path.exists(folder):
                continue
            for root, dirs, files in os.walk(folder):
                for file in files:
                    if os.path.splitext(file)[1].lower() in extensions:
                        full_path = os.path.join(root, file)
                        meta = self._extract_metadata(full_path)
                        new_lib.append(meta)

        self.library = new_lib
        self.last_scan_time = time.time()
        logger.info(f"✅ 音乐库构建完成，共 {len(self.library)} 首歌曲，耗时 {time.time() - start_t:.2f}s")

    def _extract_metadata(self, file_path):
        """提取单文件的元数据"""
        filename = os.path.basename(file_path)
        title = os.path.splitext(filename)[0]
        artist = "未知歌手"
        album = "未知专辑"

        if not mutagen:
            return {
                "path": file_path,
                "title": title,
                "artist": artist,
                "album": album,
                "search_text": f"{title}".lower()
            }

        try:
            audio = mutagen.File(file_path, easy=True)
            if audio:
                title = audio.get('title', [title])[0]
                artist = audio.get('artist', [artist])[0]
                album = audio.get('album', [album])[0]
        except Exception:
            pass

        search_text = f"{title} {artist} {album} {filename}".lower()

        return {
            "path": file_path,
            "title": title,
            "artist": artist,
            "album": album,
            "search_text": search_text
        }

    def _clean_keyword(self, text):
        cleaned = text.strip()
        for alias in self.aliases:
            if cleaned.lower().startswith(alias.lower()):
                cleaned = cleaned[len(alias):].strip()
                break
        return cleaned

    @handle_plugin_errors("音乐播放器")
    async def run(self, args, ctx):
        raw_arg = args.strip()

        # 指令：强制刷新缓存
        if raw_arg == "refresh_lib":
            await self._scan_library_async()
            return f"🔄 音乐库刷新完毕，当前共有 {len(self.library)} 首歌。"

        # 指令：收藏
        if raw_arg.startswith("add_fav"):
            try:
                _, song_name = raw_arg.split("|||", 1)
                song_name = song_name.strip()
                if song_name not in self.favorites:
                    self.favorites.append(song_name)
                    self._save_favorites()
                    return f"已将《{song_name}》加入红心歌单！"
                return f"《{song_name}》已在歌单中。"
            except:
                return "添加失败。"

        # 如果库是空的，临时扫一下
        if not self.library:
            await self._scan_library_async()

        if not self.library:
            return "📂 没找到任何音乐文件，请检查目录配置。"

        keyword = self._clean_keyword(raw_arg)
        target_entry = None
        match_reason = "随机"

        # 1. 红心模式
        fav_triggers = ["你喜欢的", "你爱听的", "推荐", "你的歌", "喜欢的", "红心"]
        is_requesting_fav = any(t in keyword for t in fav_triggers)

        if is_requesting_fav:
            fav_candidates = []
            for entry in self.library:
                if any(k.lower() in entry['search_text'] for k in self.favorites):
                    fav_candidates.append(entry)

            if fav_candidates:
                target_entry = random.choice(fav_candidates)
                match_reason = "五十铃的私藏推荐"
            else:
                return f"我的红心歌单({len(self.favorites)}首)里的歌在本地好像没找到..."

        # 2. 随机模式
        elif not keyword:
            target_entry = random.choice(self.library)

        # 3. 搜索模式 (歌名/歌手/专辑)
        else:
            kw_lower = keyword.lower()
            matches = [e for e in self.library if kw_lower in e['search_text']]

            if matches:
                # ✅ 优先精确匹配歌名
                exact_matches = [e for e in matches if kw_lower == e['title'].lower()]
                if exact_matches:
                    target_entry = random.choice(exact_matches)
                    match_reason = f"歌名精确匹配('{keyword}')"
                else:
                    target_entry = random.choice(matches)
                    # 判断匹配类型
                    if kw_lower in target_entry['artist'].lower():
                        match_reason = f"歌手匹配('{keyword}')"
                    elif kw_lower in target_entry['album'].lower():
                        match_reason = f"专辑匹配('{keyword}')"
                    else:
                        match_reason = f"歌名匹配('{keyword}')"
            else:
                # 模糊匹配
                titles = [e['title'] for e in self.library]
                close = difflib.get_close_matches(keyword, titles, n=1, cutoff=0.4)
                if close:
                    best_title = close[0]
                    for e in self.library:
                        if e['title'] == best_title:
                            target_entry = e
                            match_reason = f"模糊匹配('{best_title}')"
                            break

        # 执行播放
        if target_entry:
            path = target_entry['path']
            title = target_entry['title']
            artist = target_entry['artist']

            try:
                os.startfile(path)

                trigger_motion = safe_get_context(ctx, "trigger_motion")
                if trigger_motion:
                    await trigger_motion("happy")

                # 触发音乐评价
                chat_service = ctx.get("chat_service")
                if chat_service and hasattr(chat_service, "handle_music_event"):
                    artist_hint = "五十铃推荐" if is_requesting_fav else artist
                    asyncio.create_task(
                        chat_service.handle_music_event(title=title, artist=artist_hint)
                    )

                prefix = "✨ " if is_requesting_fav else "🎵 "
                info_str = f"{title}"
                if artist != "未知歌手":
                    info_str += f" - {artist}"

                return f"{prefix}正在播放：{info_str} ({match_reason})"

            except Exception as e:
                return f"播放失败: {e}"
        else:
            return f"🤔 没找到与 '{keyword}' 相关的歌/歌手/专辑。"
