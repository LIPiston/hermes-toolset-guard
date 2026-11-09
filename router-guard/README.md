# router-guard

让**入口 profile 变成纯路由器**：它派活、回读、验证，但不自己改文件。

## 拦什么

| 模式 | 被拦的工具 |
| --- | --- |
| `edits`（默认） | `write_file`, `patch` |
| `strict` | 上面两个 + `terminal`, `execute_code` |

只读工具（`read_file` / `search_files` / `terminal` 在 `edits` 模式下）始终可用，
这样入口在派活之后还能独立核对子智能体的产出，而不是听它自报。

## 为什么不干脆在 config 里禁工具

因为禁不掉。子智能体的 toolset 是从**父 profile** 继承的
（`tools/delegate_tool.py::_build_children` 传 `toolsets=None`，注释写着
"always inherit the parent's toolsets"）。在入口 profile 上禁用 `file`，
所有子智能体跟着一起失能。

`pre_tool_call` 钩子没这个问题：它在调用发生时才判断，而且插件管理器按 `HERMES_HOME`
缓存，所以装在入口 profile 的插件目录里，就只对入口 profile 本身的调用生效 ——
路由出去的子智能体跑在自己的 home 下，根本看不到这个钩子。

## 配置

```yaml
plugins:
  entries:
    router-guard:
      settings:
        mode: edits        # edits（默认）| strict
```

配置每次调用实时读取，改完不用重启。

## 两种模式的取舍

`edits` 是默认值，理由和 OpenCode orchestrator 的 `edit: * -> deny` 一样：把
「顺手改文件」这个默认动作关掉，同时保留 shell，入口才能跑 `git status` /
`git diff` 去验证派发结果。

`strict` 把 shell 也关掉，边界更硬，但代价很实在 —— 入口从此没有任何独立验证手段，
只能相信子智能体的自述。

## 诚实说明

`edits` 模式**不是**写入封锁。`terminal` 是货真价实的写入通道：

```bash
echo 'x' > file          # 重定向
sed -i 's/a/b/' file     # 原地编辑
python -c "open('f','w').write('x')"
```

`execute_code` 在 `edits` 模式下同样开着，直接就是原生 Python。所以 `edits` 拦的是
**默认路径**，让越界必须刻意规避；只有 `strict` 真正关门，并接受失去验证能力的代价。
这是一个明确的取舍，写在这里而不是留给使用者自己撞。

## 安装

```bash
cp -r router-guard "$HERMES_HOME/plugins/"
hermes plugins enable router-guard
hermes plugins doctor
```

装哪个目录决定管哪个 profile：`$HERMES_HOME/plugins/` 是 base profile，
`$HERMES_HOME/profiles/<name>/plugins/` 是指定 profile。
