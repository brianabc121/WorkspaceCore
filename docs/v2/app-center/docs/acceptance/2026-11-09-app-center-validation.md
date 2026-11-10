# AC-6：验收矩阵、升级与回退

本文件是Claude开发后的执行清单，不代表本次产品测试已运行。

## 1. 核心验收

| ID | 场景 | 预期与证据 | 负责 |
|---|---|---|---|
| A01 | news真实清单 | 2页/1组件/7只读工具/1cron，入口来自注册表 | AC-1/3 |
| A02 | 假注册第二个测试模块 | 新页自动出现，不改AppCenter宿主硬编码；不污染生产 | AC-1/3 |
| A03 | owner/admin/member | owner启停/重试，admin只读但可允许的健康检查，member仍可取导航开关 | AC-1/2/3 |
| A04 | enabled=true但missing配置 | 仍可启用，显示待部署，不伪报健康 | AC-1/2/3 |
| A05 | ready=true但服务down | ready和健康独立，checked_at/错误类别正确 | AC-1/3 |
| A06 | GET列表/详情 | 零主动外部请求，缺缓存显示未检测 | AC-1 |
| A07 | 显式健康检测 | 只调已声明probe；禁用/无probe/缺配置/timeout/限流各自处理 | AC-1 |
| A08 | 停用→再启用 | API/工具/组件/后台处理随开关变化，数据/原设置保留 | AC-2/5 |
| A09 | hook失败 | 开关已提交，sync失败可查；重试不再写开关事件 | AC-2/3 |
| A10 | hook超时/进程中断 | unknown而非成功；锁与attempt可恢复，不能并发重复执行 | AC-2 |
| A11 | 版本冲突/快速切换 | 旧回包不能覆盖新目标；state_version和发布version不混用 | AC-2/3 |
| A12 | 并发依赖启停 | 两个真实DB连接执行，依赖不变量不被打破 | AC-2 |
| A13 | 无hook/旧无历史结果 | not_required/unrecorded，不回填假成功 | AC-2 |
| A14 | 活动记录 | workspace+模块key精确匹配，news不混news_digest | AC-1/5 |
| A15 | scope/用户切换 | 旧数据/迟到回包不泄露到新身份 | AC-3 |
| A16 | 无设置页/前后端不一致 | 无错误跳转，有明确状态 | AC-3 |
| A17 | 核心功能/外部应用 | 不误变插件，不显示安装/升级/清数据 | AC-5 |

| A18 | 真实render吞掉OSError/解密异常 | hook必须报告failed，不因正常返回而显示成功 | AC-2 |
| A19 | 设置/worker/反馈与停用并发写配置 | 跨进程共享资源锁+最新状态，最终禁用不被旧启用配置覆盖，锁顺序无死锁 | AC-2 |
| A20 | 未部署/业务未开、旧None钩子 | not_required或unknown，不伪造同步成功 | AC-2 |

## 2. 页面入口验收

- [ ] P01：五类入口均可按既有规范添加/编辑/打开。
- [ ] P02：只更新目标项，完整布局其余内容保留，发送正确version。
- [ ] P03：两页并发编辑409保留草稿，owner确认后才合并保存。
- [ ] P04：admin无PUT；owner不使用低权限过滤缓存覆盖完整布局。
- [ ] P05：移除入口不删除目标笔记/看板，不产生模块启停调用。
- [ ] P06：embed来源/sandbox、外链http(s)、站内路径、目标workspace校验不退化。
- [ ] P07：min_role只控制导航可见，不能给资源增权。
- [ ] P08：移动/暗色/键盘/焦点与未保存提醒可用。

## 3. 数据升级

1. 先确认最新Alembic单head与正式任务路径；使用专用数据库做演练。
2. 扩展workspace_modules.state_version，旧行0；不改原enabled/default_enabled，不能因新增列把全部模块停用。
3. 新增sync attempt表/索引/权限约束，不创建news业务数据或假操作历史。
4. 旧GET/modules与PATCH仅enabled请求继续工作；新页面附版本字段。
5. 新管理API可用后再上线新页面；旧页面仍可读模块列表。
6. 用news验证停用/恢复、未部署启用与同步失败；保留真实测试记录。
7. 不执行drop模块表或清空数据；新功能可单独前端回退，新增列/表暂留。

回退：
- 新前端出问题先恢复旧AppCenter页面，保留后端兼容字段与统一锁。
- 如已运行新版同步attempt，不把其历史删掉；旧UI不显示不等于外部同步已完成。
- 后端回退前确认没有持锁/在途hook；恢复原代码不恢复未知的外部配置，需按最新目标协调验证。
- 生产不直接downgrade删表，测试环境可验证downgrade结构行为；不可逆清理另立任务并按既有审批边界处理。
- 导航只复用现有布局，不做批量重写或复制；回退页面不会丢原自定义页面。

## 4. 实施后命令与真实证据

~~~bash
make lint-api
make test-api
make openapi
npm --prefix apps/web ci
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run test
npm --prefix apps/web run check:api
npm --prefix apps/web run build
python3 scripts/collab.py check
~~~

额外运行新management/sync/迁移测试、原modules/navigation/operations/news回归、空库与现有库迁移、真实浏览器用例。
专用TEST_DATABASE_URL/TEST_REDIS_URL必须有效；跳过的集成测试不能当作权限、锁、迁移已通过。
真实浏览器至少留桌面、窄屏、暗色、同步失败、导航409冲突五类截图或录像；截图不含真实Key/内部服务地址。

每条检查记录：
~~~text
基线/交付commit:
执行目录、命令、工具版本:
专用DB/Redis配置状态（不写秘密）:
开始/结束时间:
exit_code:
PASS/FAIL/SKIP数量:
stdout/stderr或日志文件:
覆盖范围:
阻塞/未覆盖项:
~~~

PR说明按仓库模板写问题、行为、变更、验证、迁移与风险；不能把“预期PASS”“源码有测试”或本包文档校验冒充实际产品通过。
