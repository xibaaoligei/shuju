# 指数数据 · 在线公开版

这是一个**不依赖任何电脑开机**的静态看板：

- 打开即用，不需要登录、不需要密码
- 任何人拿到网址都可以直接查看
- 搜索、排序、筛选、自选、导出 CSV、CSV 导入都可以用
- 数据由 GitHub Actions 每天北京时间 06:00 自动抓取并重新发布
- 由 GitHub Pages 托管，任何手机或电脑都可以访问

访问地址：<https://xibaaoligei.github.io/shuju/>

## 能做什么

| 功能 | 在线版行为 |
| --- | --- |
| 搜索 / 排序 / 筛选 / 切换周期 | 浏览器本地完成 |
| 自选 / 列偏好 | 保存在当前浏览器的 localStorage |
| 导出 CSV | 浏览器本地下载 |
| 导入 CSV | 解析后保存在当前浏览器，并出现在「历史批次」下拉框 |
| 更新数据 | 每天由 GitHub Actions 自动抓取；页面上的「刷新最新」用于重新加载已发布的数据 |
| 停止服务 | 在线版没有服务进程，按钮已隐藏 |

注意：**导入的数据只保存在当前浏览器**，不会同步给其他人。如果需要所有人共享同一份导入数据，或者要在网页里直接触发理杏仁抓取，就需要一台云服务器后端。

## 本地预览

```bash
# 从本机 SQLite 最新批次生成公开数据
python tools/build_static.py --db --out docs --no-encrypt

# 起一个本地服务器
python -m http.server 8000 --directory docs
```

打开 <http://127.0.0.1:8000/>，不需要密码。

## 自动更新

仓库 Settings → Secrets and variables → Actions 中配置：

| Secret | 内容 |
| --- | --- |
| `LIXINGER_COOKIES` | `data/lixinger_state.json` 的完整 JSON 内容 |

`.github/workflows/pages.yml` 每天北京时间 06:00 自动运行，也可以用 Actions 页面的 **Run workflow** 手动触发。

如果理杏仁登录过期，重新运行本机的 `login_helper.py`，把新的 cookies JSON 更新到 `LIXINGER_COOKIES` 即可。更新失败时，工作流会使用仓库中最后一次成功发布的 `docs/data.json` 作为兜底。

## 目录

```
template/               原 Flask 前端模板
site/static-app.js      在线版适配层：静态 /api、导入、本地偏好
tools/build_static.py   生成 docs/ 数据
tools/encrypt_data.js   可选的 AES 加密脚本（公开版不使用）
tools/scraper.py        理杏仁接口抓取（GitHub Actions 使用）
docs/                   GitHub Pages 发布的静态文件
```

## 说明

- `docs/data.json` 是公开数据文件，任何人访问网址都能读取；这正是「任何人进去都能用」的要求。
- 自选、列偏好、导入的数据只保存在各自设备的浏览器里，换设备不会同步。
- 页面不会把理杏仁 cookies 或 GitHub token 发到浏览器；抓取只在 GitHub Actions 的服务器上执行。
