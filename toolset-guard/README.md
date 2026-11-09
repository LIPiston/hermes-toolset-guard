# toolset-guard

让一个 profile **真正只握有它声明过的 toolset** —— 在调用时执行边界，因为配置层做不到。

## 问题

profile 的 config 里可以写自己拥有哪些 toolset，但被委派出来的子智能体运行时
（`tools/delegate_tool.py::_build_children` 传 `toolsets=None`，注释
"always inherit the parent's toolsets"），拿到的是**父 profile** 的 toolset 集合。
声明等于没写。

`pre_tool_call` 钩子补不上 allow-list（工具已经绑好了），但可以否决那些落在声明边界
之外的**具体调用**。插件装在某个 profile 的插件目录里，插件管理器按 `HERMES_HOME` 缓存，
所以它只在该 profile 内触发。

## 配置

```yaml
plugins:
  entries:
    toolset-guard:
      settings:
        profile_label: "device-ops"      # 拦截提示里的显示名
        blocked_toolsets:                # 该 profile 不拥有的 toolset
          - code_execution
          - computer_use
          - connections
          - tts
```

只写 **toolset 名**，不用写工具名 —— 插件用
`toolsets.resolve_toolset(name, include_registry=False)` 展开成工具集合（例如
`code_execution` → `execute_code`，`connections` → `manage_connections`）。
展开结果按配置内容缓存，`resolve_toolset` 自身也有 memo。

两个键都是每次调用实时读取，改配置不用重启。

### 关于 `browser`

示例配置里从不包含 `browser`：浏览器自动化通常是所有 profile 故意共享的能力，
拦掉它多半不是本意。只有该 profile 确实不该开浏览器时才加进 `blocked_toolsets`。

## 容错

**fail-open**：任何内部异常（配置读取失败、toolset 名解析失败、缓存问题）都返回
`None`，也就是放行。一个守卫的 bug 绝不能卡死一次正常调用。写错的 toolset 名解析为空，
同样只是被忽略，不会连带关掉其他 toolset 的拦截。

## 安装

```bash
cp -r toolset-guard "$HERMES_HOME/profiles/<name>/plugins/"
hermes plugins enable toolset-guard
hermes plugins doctor
```

每个需要边界的 profile 各装一份，各自在自己的 config 里写 `blocked_toolsets`。
同一份代码可以服务多个 profile —— 边界由配置决定，不写死在代码里。

## 验证

```python
class Ctx:
    def __init__(self, settings): self.settings, self.hooks = settings, {}
    def get_config(self, k, d=None): return self.settings.get(k, d)
    def register_hook(self, n, cb): self.hooks[n] = cb

ctx = Ctx({"profile_label": "demo", "blocked_toolsets": ["computer_use"]})
import importlib.util
spec = importlib.util.spec_from_file_location("tsg", "__init__.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
mod.register(ctx)

assert ctx.hooks["pre_tool_call"]("computer_use")["action"] == "block"
assert ctx.hooks["pre_tool_call"]("read_file") is None
```

## 局限

拦的是**工具入口**，不是能力。工具被禁不影响同一台机器上的 `terminal` 装包、跑脚本、
读配置。它的作用是让声明与实际行为一致、让越界显式失败，不是替代容器或用户隔离。
