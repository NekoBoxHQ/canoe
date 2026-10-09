# bin/

把代理内核放在这个目录，轻舟会自动找到它。

| 文件 | 必需性 | 用途 |
|---|---|---|
| `sing-box.exe` | **必需** | 代理内核 |
| `wintun.dll` | 仅 TUN 模式 | TUN 虚拟网卡驱动。**已随仓库提供**（427KB） |

## 获取 sing-box

从官方 Releases 下载 Windows amd64 版本：<https://github.com/SagerNet/sing-box/releases>

**版本要求：推荐 `1.9.x ~ 1.10.x`。** 1.11 起 `inbounds[].sniff` 被废弃，
本客户端生成的配置用的是旧写法。

```bash
bin\sing-box.exe version
```

## wintun.dll

**本仓库已包含**（`bin/wintun.dll`，取自 wintun 0.14.1 的 amd64 版本），
无需自行下载。许可证见同目录的 `wintun-LICENSE.txt` —— 它允许随
"通过其 API 使用它的软件"一同分发，sing-box 正是通过 API 使用它。

需要更新版本时从 <https://www.wintun.net/> 下载，把 `bin/amd64/wintun.dll`
覆盖到本目录即可。

## 也可以不放在这里

客户端按以下顺序查找内核：

1. `%APPDATA%\Canoe\client.json` 里的 `singbox_path`
2. `canoe-client/bin/`
3. `canoe-client/`（项目根）
4. 系统 `PATH`

所以你也可以直接把 sing-box 加进 PATH，或用配置指定路径。
