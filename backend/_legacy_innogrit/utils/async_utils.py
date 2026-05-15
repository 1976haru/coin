# kim_bot/utils/async_utils.py

import asyncio
import inspect

async def call_maybe_await(callable_obj, *args, **kwargs):
    """
    주어진 객체가 코루틴(비동기) 함수이거나 코루틴 객체이면 await하여 실행하고,
    일반(동기) 함수이면 그냥 호출합니다.
    
    이를 통해 await 누락으로 인한 런타임 경고를 방지하고,
    호출하는 쪽에서는 동기/비동기 여부를 신경쓰지 않아도 됩니다.
    """
    if inspect.iscoroutinefunction(callable_obj):
        # 비동기 함수인 경우 (예: async def func(...))
        return await callable_obj(*args, **kwargs)
    elif inspect.iscoroutine(callable_obj):
        # 이미 생성된 코루틴 객체인 경우 (예: func(...))
        return await callable_obj
    else:
        # 동기 함수인 경우
        return callable_obj(*args, **kwargs)