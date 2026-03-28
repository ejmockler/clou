async def task_a() -> A:
    """Do A."""

async def execute():
    a = await task_a()
    b = await task_b(a)
