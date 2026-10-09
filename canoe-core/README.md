# canoe-core

轻舟 / Canoe 的公共库。客户端和服务端都依赖它。

存在的理由只有一个：**让"客户端拿不到真实节点"这条约束由类型系统强制，而不是靠约定。**

## 内容

| 模块 | 作用 |
|---|---|
| `constants.py` | 品牌名、界面文案、配色、API 路径、错误码。改文案不用翻代码 |
| `models.py` | 双方共享的请求/响应模型 |
| `version.py` | 版本号 |

## 关键设计

`models.py` 里只有 `EntryPayload`（中转层入口），**没有任何真实节点的模型**。
`ConfigResponse` —— 也就是客户端 `/api/config` 拿到的那个类型 —— 里面的
`entry` 字段类型就是 `EntryPayload`。

于是：

```python
from canoe_core import ConfigResponse

resp = ConfigResponse.model_validate(api.get(Api.CONFIG))
resp.entry.host          # 中转层入口，可以
resp.entry.real_host     # AttributeError —— 这个字段压根不存在
```

不是"服务端记得别下发"，而是"客户端侧根本没有接收它的类型"。

两个自校验方法把这层保护做成了可执行的断言：

- `EntryPayload.assert_whitelisted()` —— 字段集合必须与 `ENTRY_FIELDS` 完全一致，
  有人偷偷加了字段会直接报错。
- `ConfigResponse.assert_no_real_fields()` —— 递归扫描序列化结果，
  出现 `real_*` 就报错。测试里会调用它。

## 安装

```bash
pip install -e ./canoe-core          # 开发
# 或
pip install ./canoe-core             # 正式
```

服务端和客户端都不把 `canoe_core` 的源码复制进自己目录，而是作为依赖引入。
