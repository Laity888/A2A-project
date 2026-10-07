# 代码审查起手文档（code review kickoff）

> 目的：任何新会话里，把这份文档（或其路径）交给 agent，就能立刻开始代码审查、不用再问一遍流程。
>
> 用法（二选一）：
>
> 1. 新会话开头一句：`按 docs/agents/code-review-kickoff.md 起手审`，再补上「起手三答」。
> 2. 直接把整份文档贴进对话。

---

## 一、起手三答（先答完再动手）

| # | 要答的 | 具体给什么 |
|---|--------|-----------|
| 1 | **审什么**（基点） | git 场景：一个 fixed point（`main` / `HEAD~5` / commit SHA / 分支名）；agent 用 `git diff <base>...HEAD` 取 merge-base 三点式。非 git/未提交场景：给一份"变更文件清单 + 改动意图"，agent 退回把变更集全量当 diff 处理。 |
| 2 | **按什么评**（spec 与规范源） | spec/票位置：本仓库在 `.scratch/<effort>/issues/NN-xxx.md`。规范源：`AGENTS.md` + `GLOSSARY.md` + `docs/adr/` + `docs/agents/*`。 |
| 3 | **走哪条链子** | A：agent 亲自跑双轴（`code-review`）｜B：派一个独立评审员（`requesting-code-review` + `code-reviewer.md` 模板）｜C：全流程（审完紧接 `receiving-code-review` 处置）。 |

三答缺任一项，agent 应停下问、不自作主张。

---

## 二、三条链子的分工与产出

- **`code-review`（我亲自跑双轴）**：两个并行 subagent，轴 A=Standards（仓库规范 + Fowler code-smell 基线），轴 B=Spec（原始票/spec）。产出 = 两段结构正文（`## Standards` / `## Spec`），末尾一行"各轴发现数 + 每轴内最严重的一条"。**禁止跨轴合并或重排**。
- **`requesting-code-review`（派独立评审员）**：一次**一个** general-purpose subagent，按 `code-reviewer.md` 模板。产出 = 四段：Strengths / Issues（Critical / Important / Minor）/ Recommendations / Assessment（Ready to merge：Yes / No / With fixes）。**明令不许再 spawn 子评审**、只读不改工作树。
- **`receiving-code-review`（收到反馈后怎么处置）**：产出不是报告，是**动作**——按 blocking→简单→复杂 逐条改、每改一条测一次；措辞禁止"You're absolutely right!""Great point!""Thanks"，只写"Fixed. <改了啥>"这类技术回执。

三者不重复：`code-review` 与 `requesting` 都是"出评审"但机制不同（双轴 vs 单评审员分级）；`receiving` 是消费端。

---

## 三、复制即用的起手句模板

**A｜我亲自跑双轴**
```
按 docs/agents/code-review-kickoff.md 走。用 code-review 技能审 <BASE>...HEAD 的 diff：
- spec 在 .scratch/<effort>/issues/<NN-xxx>.md
- 规范源 AGENTS.md + GLOSSARY.md + docs/adr/
输出两段正文（Standards / Spec，不合并），末尾给分轴小结。
```

**B｜派独立评审员（走 requesting-code-review）**
```
按 docs/agents/code-review-kickoff.md 走。用 requesting-code-review，派 general-purpose subagent、按 code-reviewer.md 模板审：
- BASE_SHA=<sha>  HEAD_SHA=<sha>
- DESCRIPTION：<一句话说清做了什么>
- PLAN_OR_REQUIREMENTS：<票/plan 片段或路径>
只读、不 spawn 子评审。返回按 Strengths / Critical / Important / Minor / Assessment 分类。
```

**C｜全流程（审完紧接处置）**
```
按 docs/agents/code-review-kickoff.md 走。先 code-review 出双轴正文，再按 receiving-code-review 逐条核验处置：
- 每条先对着代码库验一遍（是不是真需要、会不会破现有功能、YAGNI 先 grep）
- 按 blocking → 简单 → 复杂 顺序改，每改一条测一次
- 全部落定后跑一次离线 pytest，把结果贴回来
```

**D｜没 git / 未提交的兜底**
```
按 docs/agents/code-review-kickoff.md 走。没 git 或未提交，把下列变更集全量当 diff 审：<文件清单>
改动意图：<一段说明>
规范源/票位置：<路径>
```

---

## 四、项目侧要点与踩过的坑

- **git 基点必须先验**：`git rev-parse <base>` 通、`git diff <base>..HEAD --stat` 非空，再往下走。坏基点或空 diff 应该在这一步就拒，别把错扔进并行 subagent。
- **`code-review` 属 matt-pocock 系，可能标了 `disable-model-invocation`**，Skill 工具调不进去。绕法：起手句里写"按 `skills/code-review/SKILL.md` 手动执行双轴流程"，agent 用 Read 那份 SKILL.md 后照协议跑。`requesting/receiving-code-review` 属 superpowers 系，正常 Skill 调用即可。
- **`code-review` 依赖 `docs/agents/issue-tracker.md`** 来定位票。本仓库 `AGENTS.md` 已登记，票在 `.scratch/`。缺这条会提示 `/setup-matt-pocock-skills`。
- **无 git 场景**：把本次改到的文件当变更集全量喂给双轴（不是"假装 diff"）。
- **配置/提示词一致性红线**：改 `config/agents/*.yaml` 的提示词/参数须同步 `docs/prompts/*.md` 的 promptspec 块，否则 `test_prompt_consistency` / `common.prompt_check` 会红。Standards 轴必查。
- **评审正文默认不落盘**。要留档就在起手句里加一句：`评审正文落到 .scratch/<effort>/review-YYYYMMDD.md` 或 workspace outputs 里。

---

## 五、处置阶段（走链子 C 时必守）

按 `receiving-code-review`：

1. **读完不立刻动**：先复述每条要求，把不明确的停下发问（不做半懂半做、不 batch 改）。
2. **对着代码库核验**：是不是真需要（YAGNI 先 grep）、会不会破现有功能、评审员有没有全上下文。
3. **实施顺序**：blocking（安全/崩坏/数据丢失）→ 简单（typo/import）→ 复杂（重构/逻辑）。每改一条测一次。
4. **措辞**：不写"You're absolutely right!""Great point!""Thanks"，只写"Fixed. <改了啥>"或"Good catch — <具体问题>. Fixed in <位置>"。
5. **走不通的评审意见要推回**：给技术理由、指能证明的测试/代码，不硬扛。发现自己之前推回错了：直接说"Verified this and you're correct. <原因>. Fixing."，不长篇道歉。

---

## 六、完成前必过的验证（`verification-before-completion`）

- 离线 `pytest` 全套（当前口径：**201 passed, 4 deselected**，用项目 `.venv` 的 `python -m pytest -q`）。
- 有 `A2A_LOCAL=1` 全链路的话，起 `检索→答复→接待→UI` 手工过一遍本次改动路径（比如新增事件/新 UI 反馈/DB 迁移）。
- 涉及 DB schema 变更：确认二次启动迁移是 no-op、老行是否被正确转换（本项目的 users / conversations / mem_session 迁移都是幂等的）。
- 涉及前端：浏览器 Ctrl+F5 强刷（`ui/app.py` 每请求现读 `index.html`），后端改动则要重启对应服务。

---

## 七、一页清单（贴给 agent 前自检）

- [ ] 基点给定，且 `git rev-parse <base>` 能解析、diff 非空（或已明说走无-git 兜底）
- [ ] spec/票位置写清（`.scratch/<effort>/issues/NN-xxx.md` 或直接把票文贴进来）
- [ ] 规范源写清（`AGENTS.md` + `GLOSSARY.md` + `docs/adr/`）
- [ ] 选走哪条链子：A / B / C / D
- [ ] 是否要求评审正文落盘（要就写路径）
- [ ] 完成前必过：离线 pytest + 手工验证 + DB 迁移幂等（如涉及）
- [ ] 处置阶段是否也一并跑（链子 C 才跑）

---

## 八、快速引用：模板文件位置

- 双轴评审协议：`~/.qoderworkcn/skills/code-review/SKILL.md`
- 派独立评审的模板：`~/.qoderworkcn/skills/requesting-code-review/SKILL.md` + `code-reviewer.md`
- 收到反馈的处置协议：`~/.qoderworkcn/skills/receiving-code-review/SKILL.md`
- 项目的 issue tracker / triage / domain 约定：`docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`
