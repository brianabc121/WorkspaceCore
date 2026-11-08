# 插拔式模块（后端）

一个文件夹一个模块，清单在 `<key>/manifest.py`，注册表在 `registry.py`。宿主只经注册表接线：
页面接口（自动加启用检查）、内部接口、模型、智能体工具、定时任务、运维检查、凭证轮换、访问日志脱敏。

- 新增模块：`make module-new key=<key> name=<中文名>`，然后照 [`docs/modules/README.md`](../../../../docs/modules/README.md) 做。
- 清单字段：`base.py`（`ModuleManifest`）；启用状态：`service.py`；接口启用检查：`deps.py`。
- 参考实现：`news/`（新闻早报）。
- 一致性检查：`services/api/tests/test_modules.py`。
