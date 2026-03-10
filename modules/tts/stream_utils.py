# modules/tts/stream_utils.py
import re


class StreamSentenceBuffer:
    def __init__(self, min_chars=10):
        self.buffer = ""
        self.min_chars = min_chars
        # 分隔符：句号、感叹号、问号、分号、换行，包括中英文
        # 增加逗号可以降低延迟，但在 GPT-SoVITS 上可能导致语气破碎，建议根据模型调整
        self.separators = re.compile(r'([。！？!?\n;；]+)')

    def feed(self, chunk: str):
        """
        喂入文本块，返回提取出的完整句子生成器
        """
        self.buffer += chunk

        # 循环处理缓冲区，直到无法再切分
        while True:
            match = self.separators.search(self.buffer)
            if not match:
                break

            # 找到分隔符的位置
            end_idx = match.end()

            # 提取句子（包含标点）
            sentence = self.buffer[:end_idx].strip()

            # 剩余部分放回缓冲区
            self.buffer = self.buffer[end_idx:]

            # 如果句子太短（比如只是一个"。"或者"嗯"），且缓冲区还有内容，
            # 可以选择暂时不发，防止碎片化语音。这里简单起见，只要非空就发。
            if sentence:
                yield sentence

    def close(self):
        """
        流结束时，将缓冲区剩余内容作为最后一句返回
        """
        rest = self.buffer.strip()
        self.buffer = ""
        if rest:
            yield rest