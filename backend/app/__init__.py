"""ElementWar Backend 应用包。

按架构设计的五层分层：
- 连接层 (app.connection)
- 游戏逻辑层 (app.core)
- 化学引擎层 (app.chemkit_adapter，包装 chemkit)
- 数据持久层 (app.models, app.services)
- API 层 (app.api)
"""

__version__ = "0.2.0"
