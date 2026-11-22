# 指数数据 · 在线版

这是一个**不依赖任何电脑开机**的静态看板：

- 前端完全运行在浏览器里，表格、卡片、搜索、排序、筛选、自选、导出 CSV 都可用
- 数据文件 `docs/data.enc.json` 使用 AES-256-GCM 加密，打开页面时输入访问密码
- 由 GitHub Pages 托管，任何手机或电脑都可以访问
- GitHub Actions 每天北京时间 06:00 自动抓取一次数据并重新部署

预计地址：<https://xibaaoligei.github.io/shuju/>

## 密码在哪

访问密码 **不写入仓库**，由仓库的 Actions Secret `SITE_PASSPHRASE` 提供，并在交付时单独告知。
如果需要更换密码，重新执行下面的本地构建，并把新的 `SITE_PASSPHRASE` 更新到仓库 Secret 即可。

## 本地预览

```bash
# 用本机 SQLite 最新批次生成（需要上级目录的 data/index_tool.db）
export SITE_PASSPHRASE='你的密码'
python tools/build_static.py --db --out docs

# 起一个本地服务器
python -m http.server 8000 --directory docs
```

打开 <http://127.0.0.1:8000/>，输入密码即可。

## 更新数据

### 自动（推荐）

仓库 Settings → Secrets and variables → Actions 中配置：

| Secret | 内容 |
| --- | --- |
| `SITE_PASSPHRASE` | 页面访问密码 |
| `LIXINGER_COOKIES` | `data/lixinger_state.json` 的完整 JSON 内容 |

配置后，`.github/workflows/pages.yml` 会每天自动运行；也可以在 Actions 页面手动点 **Run workflow**。

如果理杏仁登录过期，重新运行本机的 `login_helper.py`，把新的 cookies JSON 更新到 `LIXINGER_COOKIES` 即可。

### 手动

```bash
export SITE_PASSPHRASE='你的密码'
python tools/build_static.py --db --out docs
git add docs
git commit -m "update data"
git push
```

## 目录

```
template/               原 Flask 前端模板（复制自 static/index.html）
site/static-app.js      在线版适配层：密码解锁 + 静态 /api 实现
tools/build_static.py   生成 docs/ 和加密数据
tools/encrypt_data.js   AES-GCM 加密
tools/scraper.py        理杏仁接口抓取（GitHub Actions 使用）
docs/                   GitHub Pages 实际发布的文件
```

## 安全说明

- 仓库可以是公开的：`data.enc.json` 只有密文，没有密码无法还原数据
- 密码使用 PBKDF2-SHA256（250,000 次）派生，再使用 AES-256-GCM 加密
- 自选和列偏好只保存在各自设备的 `localStorage`，不会上传
- 这是只读版，网页内不提供导入/更新/停止功能
