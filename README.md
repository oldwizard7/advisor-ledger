# Advisor Ledger(学术黑榜镜像)

只增不减地镜像社区维护的"学术黑榜 / Advisor Red Flags Notes" Google Doc,记录每一次编辑,保留每一次删除。

**渲染后的实时视图**:https://the-hidden-fish.github.io/advisor-ledger/

## 做什么 / 为什么

原文档是匿名可编辑的,也就是说实质性的观察可能被悄悄删掉。本仓库每隔几分钟抓一次原文档并把结果提交到 git,这样编辑历史——包括被撤回或被覆盖的内容——都保留下来。

`main` 分支上的每一个 commit 对应原文档的一次真实变更。

## 目录结构

| 路径 | 用途 |
|---|---|
| `snapshots/YYYY/MM/DD/<source>/*.json` | 每次抓取的完整 `documents.get` JSON |
| `snapshots/.../*.txt` | 纯文本导出 |
| `snapshots/.../*.meta.json` | Drive 元信息 + 抓取内容的 SHA-256 |
| `deltas/.../*.delta.json` | 相对上一次快照的结构化差异(按段落的 insert / delete / replace) |
| `reviews/.../*.review.json` | 每次 diff 的本地 LLM 审查结果,标注可能的人肉信息、纯人身攻击、可疑删除。**只做提示,不会阻塞 commit** |
| `docs/index.html` | 渲染视图:当前文本,被删段落原地保留(删除线 + 删除时间戳),新增段落高亮。由 GitHub Pages 提供 |
| `scripts/` | 流水线:fetch → normalize → diff → review → render → commit → push |

## 流水线

由 systemd timer 每 2 分钟触发:

1. 查询 Drive 的 `modifiedTime`,如果自上次快照以来没变化,直接短路退出。
2. 抓取结构化 JSON 和纯文本导出。
3. 把段落规范化成确定性、便于 diff 的形式(NFC Unicode、按行 rstrip、每段生成内容哈希)。
4. 对比新旧规范化快照,按段落内容哈希生成操作,让真正没变的段落不算 churn。
5. 对本次 delta 跑一次本地 LLM 审查,标三类问题:对私人的身份信息(PII)、纯人身攻击(不是对具体行为的批评)、看起来像压制性删除的改动。审查结果以 JSON 写在 delta 旁边。
6. 重新渲染 `docs/index.html`——当前文本加上按最后已知位置锚定的 ghost 段落。
7. `git add` 新快照、delta、review、渲染产物;commit;push。

流水线用 `flock` 保护,防止手动触发和 timer 触发撞车。

## 如果原 Google Doc 被下架

仓库根目录的 `MIRROR.md` 是**社区维护的备份源**。当原 Doc 不可用时,它就是下一个源。

**贡献方式**(都不改代码,任何人都能参与):

1. **网页匿名发言**(无需登录):访问 [live 页面](https://the-hidden-fish.github.io/advisor-ledger/) 顶部评论区,点"匿名发言",填 Turnstile 验证 → 提交 → Cloudflare Worker 代你以 bot 身份在 GitHub Issue 里发评论。
2. **GitHub 登录发言**:同一评论区的下半部分用 utterances 组件,GitHub 登录后直接评。
3. **PR**:结构性改动(新加章节、格式调整)直接对 `MIRROR.md` 开 PR。

任何评论进入 Issue 后,每 2 分钟后端的 Kimi 会扫描一次,根据既定规则(见 `scripts/merge_agent.py`)判断:
- **并入**:把内容以 MD 片段附加到 `MIRROR.md` 对应章节,自动 commit + push,并在你的评论下回复 "已并入 `<sha>`"
- **跳过**:带理由回复,比如"非实质性观察"、"含 PII"、"已有同义内容"
- **需要补充**:让你补具体学校/导师/事件

## 关于原文档

本仓库是**观察性镜像**。不代表原文档中被点名的任何一方,也不由其制作、背书或审核。`snapshots/` 和 `docs/` 里的内容归原匿名贡献者所有。

## 许可证

流水线代码(`scripts/`)以公有领域(CC0)发布。`snapshots/`、`deltas/`、`docs/` 中被镜像的内容保留原作者权利。
