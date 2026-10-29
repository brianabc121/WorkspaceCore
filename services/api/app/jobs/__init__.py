"""后台作业：ARQ Worker 与周期对账。关键作业状态先写入 PostgreSQL，Redis 不当作唯一持久化队列。"""
