# Skills 目录说明

项目现在支持兼容 `SKILL.md` 风格的轻量 Skill。

## 当前定位

Skill 是**可选**的提示词 / 工作流注入层，不是插件，也不是脚本执行器。桌面助手主能力走 `plugins/`。**只用插件、不放任何 `SKILL.md`，功能仍然完整。** 把 `SKILL.md` 放到下面的目录后会自动注入对话，不用再装技能插件。

- 适合放：写作规范、代码规范、固定工作流、某类任务的注意事项。
- 不适合放：需要直接执行的 Python/Node 脚本、MCP server 启动逻辑、依赖安装步骤。
- 如果需要执行能力，应做成 `plugins/` 插件，并通过权限和 GUI 配置控制。

## 默认扫描目录

- 当前仓库内：`./skills`
- 当前用户目录：`~/.codex/skills`

## 推荐结构

```text
skills/
  your-skill/
    SKILL.md
```

也支持更深一层的目录，例如：

```text
skills/
  provider/
    writing-helper/
      SKILL.md
```

生成的 Skill ID 会按相对目录拼接，例如：

- `your-skill`
- `provider:writing-helper`

## 如何启用

把 Skill 放进扫描目录后重启程序（或重新加载角色/会话）。宿主会自动读 `SKILL.md` 并注入对话，不需要单独的技能插件。

## 当前兼容范围

兼容的是“文档型 Skill”：

- 读取 `SKILL.md`
- 提取标题和简介
- 作为附加提示词注入主对话

当前不自动执行：

- Skill 目录中的脚本
- MCP 启动逻辑
- 依赖安装逻辑
- 其它外部进程

这样做的目的，是先让项目稳定吃下常见的 Skill 提示词和工作流说明，再逐步扩展更强的执行层。

## 示例

```text
skills/
  code-review/
    SKILL.md
```

`SKILL.md` 示例：

```markdown
# Code Review

用于代码审查。优先找 bug、回归风险、边界条件和缺失测试。
```

放进 `skills/code-review/SKILL.md` 后重启即可生效。

## 排障

- 看不到效果：确认文件名必须是 `SKILL.md`，并已放在扫描目录里。
- 启用了但没效果：当前会话可能被更高优先级提示覆盖。
- 内容太长：Skill 会进入主提示词，过长会挤占上下文；建议写流程和约束，不要整篇资料塞进去。
