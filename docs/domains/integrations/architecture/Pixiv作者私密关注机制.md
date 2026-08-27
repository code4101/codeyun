# Pixiv 作者私密关注机制

本文描述媒体同步插件当前的作者自动关注契约，是 Pixiv 作者关注逻辑的权威说明。

## 触发与计数

- 每次 Pixiv 远端采集开始前，先运行作者自动关注检查，再运行图集 BFS、作者时间线和首页发现。
- 同一作者下，本地 `DeviceFile.weight >= 1` 且仍能通过路径或内容哈希关联到 Pixiv 来源记录的作品进入统计。
- 统计键是 Pixiv `artwork_id`。图集下载多页仍只算一个作品；相同作品的旧路径、别名和重复来源也只算一次。
- 达到 `10` 个不同有权重作品的作者进入关注队列。单次最多处理 `10` 位，剩余作者由后续采集继续处理。
- 缺少有效作者 ID 的旧数据失败关闭，不允许按目录名猜作者身份。

## DrissionPage 执行协议

正式执行只使用共享的 DrissionPage `Chromium()` 控制面，不依赖 Browser Use 或一次性人工点击。

1. 复用一个任务 Tab 打开 `https://www.pixiv.net/users/<author_id>`。
2. 等待同时带有 `data-click-label="follow"` 和目标 `data-gtm-user-id` 的按钮，以此确认作者身份和页面就绪。
3. 从当前 `__NEXT_DATA__` 的 `serverSerializedPreloadedState.api.token` 读取 API token，但不记录或输出 token。
4. 调用作者详情接口确认 `isFollowed`。未关注时点击页面自己的关注按钮，并再次读取接口验证。
5. 读取 `/ajax/following/user/details`。当 `restrict != "1"` 时，调用 `/ajax/following/user/restrict_change` 设置 `restrict=1`。
6. 再次读取详情；只有明确得到 `restrict="1"` 才算完成。

该流程是幂等的：已经私密关注的作者只读取和确认状态，不重复点击或提交。

## 状态与失败语义

`private_media_sync_pixiv_author_state` 保存：

- 当前不同有权重作品数；
- 最近观测到的关注状态；
- 最近检查时间；
- 首次自动关注完成时间；
- 最近关注错误。

登录失效、验证码、账号风控、作者身份不一致、未知 `restrict` 值、提交后未验证成功，均视为显式失败。任务 Tab 由调用方创建并在 `finally` 中回收；不得关闭共享浏览器或用户原有 Tab。

## 代码入口

- 资格统计：`resolve_weighted_pixiv_author_candidates()`
- 单作者幂等动作：`ensure_pixiv_author_privately_followed()`
- 批量触发入口：`run_pixiv_author_auto_follow_sync()`
- 采集调用点：`SyncJobManager._run_pixiv_discover_download()`

