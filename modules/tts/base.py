class BaseTTS:
    async def say(self, text: str, emotion: str | None = None):
        raise NotImplementedError
