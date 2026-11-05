# 汽水音乐创作者工具 · 导出音频插件

把汽水作品的音频一键导出为本地文件（带 Cookie 直连，无需代理）。

## 这是什么

主程序「汽水音乐发布工作台」把功能拆成了独立插件，本仓库是其中之一。
它既能被 [qishui-host](https://github.com/604665656/qishui-host) 加载成一个页签，
也能单独打成 EXE 独立运行——独立运行时自带内核，不需要宿主。

## 实现状态

- **完整实现（非 stub）**：`engine.py` 与 `plugin.py` 均为可用逻辑，无占位代码。
- **不 import 任何主程序模块**：全程只通过 `ctx` 与 `qishui_core` 取数据/鉴权/写文件，
  符合插件契约硬规则。原 `export_work.py` 依赖的 `cdp_lite / update_all /
  studio_automation / song_review` 在本插件里**完全不需要**，因此不存在 stub 缺口
  （不走浏览器编辑器渲染那条慢路径，改为直接拿音频直链）。
- `compat.py` 仅作为「旧模块名 → qishui_core」的兼容重导出层与移植证据，
  运行时并不强依赖它。

## 工作原理

1. `ctx.api.song_info(song_id)` 取单曲完整信息，从 `data.clips[].clipUrl`
   拿到音频**直链**（实测完整 mp3，不走服务端渲染、不超时）。
2. 用 `ctx` 里的登录态 cookie 直连下载（已关闭系统代理，避开本机代理打光端口）。
3. 文件落到 **`ctx.account_dir(uid)/导出/`**（自动 `os.makedirs` 建目录），
   绝不会写到该目录之外；文件名取作品标题（已做非法字符清洗），重名自动加序号。

> 限制（服务端约束，非插件缺陷）：个别作品若 `clips` 为空（如
> `Downloadable=false` 且服务端未生成渲染音频），则没有直链可下，
> 会明确报错「该单曲没有可导出的音频直链」。

## 能力依赖

宿主/内核需提供：`store、auth`（`api_version` 2）。

## 用法

```bash
# 作为插件：放进 qishui-host 的 plugins/ 目录即可被加载成「工具 / 导出音频」页签
#   页签里填 song_id（可多个，逗号/空格/换行分隔）→ 点「导出」
#   另有「打开导出目录」按钮，导出文件在 账号目录/导出/

# 独立运行界面：
python -m export
# 无界面跑一次（计划任务用）：
python -m export --task
```

## 文件清单

| 文件 | 作用 |
| --- | --- |
| `export/plugin.py` | `ExportPlugin`：setup / build（页签 UI）/ run_task（无界面批量）/ summary / on_unload |
| `export/engine.py` | 纯逻辑：取直链 + 下载到导出目录；单首 `export_song`、批量 `export_batch`、进度回调 |
| `export/compat.py` | 旧主程序模块名 → qishui_core 的兼容重导出层 |
| `export/tests/smoke.py` | 冒烟测试（无浏览器/网络/真账号即可跑） |
| `export/__main__.py` | 独立形态入口（`python -m export`） |

## 测试

```bash
cd qishui-toolkit
PYTHONPATH=core;plugins/export python plugins/export/tests/smoke.py
python -m compileall -q plugins/export/export
```

冒烟检查：清单合法、插件类存在、setup/build 方法存在、独立 ctx 可构造与实例化；
全部通过则退出码 0。

## 打包成独立 EXE

```bash
pip install pyinstaller
python build_exe.py --onedir
```

## 更新

`plugin.json` 的 `update.feed` 留空时，宿主只在手动点「检查更新」时读
`update.json`。把 `update.json` 放到一个能被 `raw.githubusercontent.com`
访问的地址，再把地址填进 `update.feed` 即可开启热更新。

## 开源协议

MIT —— 与整套工具一致，每个插件独立开源、独立发版。
