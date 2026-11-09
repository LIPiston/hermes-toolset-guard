# hermes-toolset-guard

两个 Hermes 插件，用来在**配置层做不到**的地方补上工具边界：

| 插件 | 解决的问题 |
| --- | --- |
| [`router-guard`](router-guard/) | 让入口 profile 只派活、不改文件 |
| [`toolset-guard`](toolset-guard/) | 让一个 profile 真正只握有它声明过的 toolset |

---

## 为什么需要它们

Hermes 的工具权限是**按 profile 声明**的，但**委派出来的子智能体会完整继承父 profile 的 toolset**。
`tools/delegate_tool.py` 的 `_build_children()` 里写死了这一点：

```python
child = _build_child_preserving_parent_tools(
    ...
    toolsets=None,  # always inherit the parent's toolsets
    ...
)
```

后果是两种需求在配置层都表达不出来：

1. **「入口要收权，但子智能体要能干活」** — 在入口 profile 上 `hermes tools disable file`，
   所有子智能体一起失能，于是只能放弃收权。
2. **「这个 profile 只要 4 个 toolset」** — profile 的 config 里写了 `platform_toolsets`，
   但被委派运行时它不生效，子智能体仍然看到父 profile 的整套工具。

两个插件都走同一条路：**`pre_tool_call` 钩子**。它在调用发生的瞬间带着当前会话上下文触发，
所以能区分「谁在调用」，也能拿到具体的工具名去否决。

## 机制原理（为什么装在哪个目录就只对哪个 profile 生效）

插件管理器按 **`HERMES_HOME`** 分缓存。`hermes_cli/plugins.py::get_plugin_manager()`：

> Return the plugin manager for the active Hermes profile/home (cached per resolved home;
> a profile switch gets its own manager and plugin submodules).

于是：

```
$HERMES_HOME/plugins/<name>/                       <- 只对 base profile 生效
$HERMES_HOME/profiles/<profile>/plugins/<name>/    <- 只对该 profile 生效
```

一个 profile 路由出去的子智能体跑在**它自己的 home** 里，看不到入口 profile 的插件目录，
所以钩子对它是**不存在**的 —— 这正是「入口被拦、子智能体照常工作」的实现方式，不需要任何
白名单，也不需要判断「我是不是子智能体」。（`router-guard` 里还有第二道保险：即使子智能体
进程内仍跑在本 home 下，`is_delegated_child_context()` 也会放行。）

## 安装

选一个目标 profile，把插件目录拷进它的 `plugins/` 下，然后启用：

```bash
# base profile
cp -r router-guard "$HERMES_HOME/plugins/"

# 某个具名 profile
cp -r toolset-guard "$HERMES_HOME/profiles/<profile>/plugins/"

hermes plugins enable router-guard
hermes plugins doctor
```

`hermes plugins doctor` 会列出加载成功/失败的插件；`plugin.yaml` 里的
`provides_hooks: [pre_tool_call]` 必须被识别，否则钩子不会注册。

## 配置

### router-guard

```yaml
plugins:
  entries:
    router-guard:
      settings:
        mode: edits        # edits（默认）| strict
```

| 模式 | 拦 | 留给入口的自检能力 |
| --- | --- | --- |
| `edits`（默认） | `write_file`, `patch` | `terminal`、`read_file`、`search_files` 全在，可以 `git diff` 回读子智能体的产出 |
| `strict` | 上面两个 + `terminal`, `execute_code` | 只剩只读工具，无法独立验证派发结果 |

参考的是 OpenCode orchestrator 的姿态：`edit: * -> deny`，shell 留着。

### toolset-guard

```yaml
plugins:
  entries:
    toolset-guard:
      settings:
        profile_label: "device-ops"      # 只用于拦截提示里的显示名
        blocked_toolsets:                # 这个 profile 不拥有的 toolset
          - code_execution
          - computer_use
          - connections
          - tts
```

toolset 名会被 `toolsets.resolve_toolset(name, include_registry=False)` 展开成工具名，
**不需要手写工具名**；写错的 toolset 名解析为空、直接忽略（fail-open）。
两个键都是每次调用实时读取，改配置不用重启。

`browser` 默认不列进去 —— 浏览器自动化通常是各 profile 故意共享的。只有该 profile
确实不该开浏览器时才加。

## 验证

```bash
# 1. 插件被识别
hermes plugins doctor

# 2. 真的调一个被拦的工具，看返回
#    在被保护的 profile 里让 agent 调 write_file / computer_use，
#    期望拿到 action=block 的提示，而不是工具执行结果
```

也可以离线验证钩子逻辑（不需要起 Hermes）：

```python
import sys; sys.path.insert(0, "toolset-guard")
import importlib.util
spec = importlib.util.spec_from_file_location("tsg", "toolset-guard/__init__.py")

class Ctx:
    hooks = {}
    def __init__(self, settings): self.settings = settings
    def get_config(self, key, default=None): return self.settings.get(key, default)
    def register_hook(self, name, cb): self.hooks[name] = cb

ctx = Ctx({"profile_label": "demo", "blocked_toolsets": ["computer_use"]})
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.register(ctx)
print(ctx.hooks["pre_tool_call"]("computer_use"))   # -> {'action': 'block', ...}
print(ctx.hooks["pre_tool_call"]("read_file"))      # -> None
```

（上面的片段需要 `toolsets` 模块在 `sys.path` 上，即 Hermes 自己的运行环境。）

## 诚实的局限

**这不是沙箱。** 两个插件都是调用前的纪律屏障，不是能力隔离：

- `router-guard` 的 `edits` 模式不拦 `terminal`，而 shell 就是写入通道：`echo > file`、
  `sed -i`、`python -c "open(...).write(...)"` 都能绕过。`execute_code` 更是直接跑原生 Python。
  它拦的是「顺手就改」这个默认动作，让越界变成一次**刻意的规避**，而不是把路封死。
  真要封死只有 `strict`，代价是入口失去回读验证的能力 —— 这是一个真实的取舍，不是疏忽。
- `toolset-guard` 拦的是**工具入口**。工具被禁不代表能力消失：同一台机器上的
  `terminal` 照样能装包、跑脚本、读配置文件。它的价值在于让 profile 的声明与实际
  行为一致，并让越界显式可见，不是替代容器/用户隔离。
- 两者都**不覆盖**通过 MCP、shell hooks 或自定义插件引入的新工具入口。

如果你的威胁模型是「不可信代码」，用容器/独立用户/独立机器；这两个插件服务的是
「一堆可信但职责不同的 agent 之间保持边界清晰」。

## License

MIT，见 [LICENSE](LICENSE)。
