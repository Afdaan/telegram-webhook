import asyncio
import json
import os
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
from telegram.request import HTTPXRequest

with patch.dict(os.environ, {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "CHANNEL_ID": "-100123",
    "AUTHORIZED_USERS": "1",
    "POST_LIMIT": "50",
}):
    import main


async def check_reactions():
    cases = [
        ([True], []),
        ([NetworkError("read failed"), True], [1]),
        ([TimedOut(), NetworkError("read failed"), True], [1, 2]),
        ([NetworkError("read failed")] * 3, [1, 2]),
        ([BadRequest("REACTION_INVALID")], []),
        ([Forbidden("access denied")], []),
    ]
    for results, delays in cases:
        message = SimpleNamespace(
            message_id=42,
            chat_id=-100123,
            set_reaction=AsyncMock(side_effect=results),
        )
        context = SimpleNamespace(bot=SimpleNamespace(
            send_photo=AsyncMock(return_value=message),
        ))
        post = main.PostData()
        post.photos = ["photo-id"]
        post.texts = ["caption"]
        post.buttons_per_post = [[]]
        main.posts[1] = post
        main.user_post_count.clear()

        with (
            patch.object(main.asyncio, "sleep", new_callable=AsyncMock) as sleep,
            patch.object(main, "send_message", new_callable=AsyncMock) as notify,
            patch.object(main.logger, "warning"),
        ):
            await main.publish_posts(None, context, 1)

        assert context.bot.send_photo.await_count == 1
        assert message.set_reaction.await_count == len(results)
        assert all(call.args == ("❤",) for call in message.set_reaction.await_args_list)
        assert [call.args[0] for call in sleep.await_args_list] == delays
        assert main.user_post_count[1] == 1
        assert 1 not in main.posts
        assert notify.await_args.args[1] == "✅ Berhasil mengirim 1 postingan ke channel!"

    bot = Bot("123456:TEST")
    message = SimpleNamespace(
        message_id=42,
        chat_id=-100123,
        set_reaction=partial(bot.set_message_reaction, -100123, 42),
    )
    with patch.object(
        HTTPXRequest,
        "do_request",
        new_callable=AsyncMock,
        return_value=(200, b'{"ok":true,"result":true}'),
    ) as request:
        await main.add_love_reaction(message)

    assert request.await_count == 1
    payload = request.await_args.kwargs["request_data"].json_parameters
    assert json.loads(payload["reaction"]) == [{"type": "emoji", "emoji": "❤"}]


if __name__ == "__main__":
    asyncio.run(check_reactions())
    print("Reaction checks passed")
