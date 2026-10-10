# bin/

把代理内核放在这个目录，轻舟会自动找到它。

| 文件 | 必需性 | 用途 |
|---|---|---|
| `sing-box.exe` | **必需** | 代理内核 |
| `wintun.dll` | 仅 TUN 模式 | TUN 虚拟网卡驱动。**已随仓库提供**（427KB） |

## 获取 sing-box

**换内核用脚本，别手工覆盖：**

```bash
python scripts\upgrade_kernel.py              # 升到最新
python scripts\upgrade_kernel.py --version 1.15.0
python scripts\upgrade_kernel.py --check      # 只看有没有新版
python scripts\upgrade_kernel.py --from <zip> # 已经自己下好 zip 时（国内常这样）
```

它从官方 Releases 拿 `-windows-amd64.zip`，**核对 GitHub 自己算的摘要**，
只取里面的 `sing-box.exe`（包里那个 9.5MB 的 `libcronet.dll` 不要 ——
那是 cronet 传输才用得到的可选件），把旧的备份到 `%TEMP%`，最后**真跑一遍
`tests/test_config.py`**（新内核跑 `sing-box check`）；跑不过自动换回去。
手工覆盖那条路最大的风险不是"装错版本"，而是没人去跑那一步检查。

要手工下也行：<https://github.com/SagerNet/sing-box/releases>（本目录现在这份是
**1.14.3**）。检查当前版本：

```bash
bin\sing-box.exe version
```

## 为什么仓库里没有 sing-box.exe

它被 `.gitignore` 了（80MB 的二进制，且是别人的发布物）。所以**换内核是一次
本机动作** —— 仓库里只留着这个 README、`wintun.dll` 和规则集。打版时
`canoe.spec` 会把 `bin/` 整个塞进客户端，用户那边不用自己放。

> 这里原本写着"推荐 1.9.x ~ 1.10.x，1.11 起 `inbounds[].sniff` 被废弃，
> 本客户端生成的配置用的是旧写法"—— 早就不成立了：配置生成器走的是现行的
> `route.rules` 里 `{"action": "sniff"}`，1.14.3 对系统代理 / TUN / 回国
> 三种配置 `check` 都是 rc=0 且一条警告都没有。

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
