---
name: new-module
description: 在工作台里新增一个插拔式功能模块（后端 app/modules/<key> + 前端 src/modules/<key>），或把已有功能迁成模块、停用 / 移除模块。用户说“加一个新功能 / 插件 / 二级分类页 / 新模块”时使用。
---

# 新增插拔式模块

完整手册是 `docs/modules/README.md`，先读它；方案与取舍在 `docs/v2/MODULES_PLAN.md`。要点：

1. 先判断：只是一个链接、笔记或由现有小组件拼成的看板，不写代码，让 owner 在网页上加零代码页面；
   有自己的页面、数据、接口、工具的功能才做成模块。
2. 按 `docs/AGENTS.md` 建任务（`python3 scripts/collab.py new --as claude --owner claude ...`）并 start。
3. `make module-new key=<key> name=<中文名> description=<一句话>` 生成骨架并登记两个注册表；先跑
   `cd services/api && .venv/bin/pytest -q tests/test_module_<key>.py` 确认骨架是绿的。
4. 按手册第 5 节补：模型 + 迁移（`schema="workspace"`，表名 `<key>_` 开头，接在最后一条迁移后面）、接口（角色依赖 +
   CSRF + 审计 + `events.mark`）、前端页面（路由按需加载）、可选的智能体工具（风险等级照实写，写操作默认审批，不得放宽）、
   定时任务（自己检查 `is_enabled`）、外部服务（`requires` + `service_checks`，Compose 锁定 digest）。
5. 新的事件主题或首页小组件类型要同时加进宿主的 `EventTopic` / `WidgetCreate` 联合类型（一致性测试会提醒）。
6. 跑手册第 7 节的全部检查命令，更新 `docs/api-contract.md`、`docs/data-dictionary.md`，开 PR。
7. 模块默认停用，合并部署后由 owner 在“设置 → 应用中心”启用。

不要：在公共文件里手写登记、导入其他模块的内部代码、让宿主代码导入模块内部、把写工具标成 read、用浮动 latest 镜像。
