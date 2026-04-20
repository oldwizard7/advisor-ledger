# Cloudflare Worker 中继 — 匿名评论

网页上的匿名表单 → 这个 Worker → GitHub Issues。commenter 从头到尾不用登录 GitHub;Issue 里看到的是 bot 身份发的 comment。

## 一次性部署(大约 10 分钟)

### 1. 生成 GitHub PAT(fine-grained)
- https://github.com/settings/personal-access-tokens/new
- **Repository access**: Only select repositories → `the-hidden-fish/advisor-ledger`
- **Permissions** → Repository permissions:
  - Issues: **Read and write**
  - Metadata: **Read-only**(默认)
- Expiration: 1 year 或者 custom
- Generate → 复制下来(只显示一次)

### 2. 创建 Turnstile 站点
- https://dash.cloudflare.com/?to=/:account/turnstile → Add site
- Site name: `advisor-ledger` 随便
- Domain: `the-hidden-fish.github.io`
- Widget mode: **Managed**(自动)
- 创建后会拿到 **Site Key**(公开,放 HTML) + **Secret Key**(私密,放 Worker)

### 3. 创建 Worker
- https://dash.cloudflare.com/?to=/:account/workers-and-pages → Create Application → Create Worker
- 命名 `ledger-relay`(或其他),创建
- 点进去 → Edit code
- 把本目录 `worker.js` 的内容全部粘贴替换默认模板 → **Deploy**
- 回到 Worker 详情页,记下 URL:`https://ledger-relay.<你的子域>.workers.dev`

### 4. 配置环境变量 / Secrets
Worker → **Settings** → **Variables and Secrets**:

| 名字 | 类型 | 值 |
|---|---|---|
| `REPO` | Plaintext | `the-hidden-fish/advisor-ledger` |
| `ALLOWED_ORIGIN` | Plaintext | `https://the-hidden-fish.github.io` |
| `TURNSTILE_SECRET` | **Secret** | Turnstile Secret Key |
| `GH_TOKEN` | **Secret** | 上一步的 PAT |

保存后 Worker 自动 reload。

### 5. 把两个公开值告诉代码
- `Worker URL`
- `Turnstile Site Key`

把这俩塞进 `config/relay.json`:
```json
{
  "worker_url": "https://ledger-relay.YOUR_SUBDOMAIN.workers.dev",
  "turnstile_site_key": "0xAAAAAAAAAAAAAAAAAAAAAA"
}
```

`render_ledger.py` 下一次跑(timer 触发)会自动把这俩值注入 `docs/*.html` 的 form。

## 本地验证
```bash
curl -X POST https://ledger-relay.YOUR.workers.dev/ \
  -H 'content-type: application/json' \
  -d '{"pathname":"/advisor-ledger/","comment":"test from curl","token":"dummy"}'
```
应返回 `captcha failed` —— 证明 Worker 在跑,Turnstile 在挡。真实前端会带上合法 token。

## 安全底线
- `GH_TOKEN` 只在 Worker 里,前端拿不到。
- PAT 范围窄到 Issues only,就算泄漏最坏也只能恶意开/评 Issue,不能改代码。
- Turnstile 拦自动化请求;每条评论 3–4000 字符、链接 ≤ 3 的硬性限制写在 Worker 里。
- 评论里附带的 `day-hash` 是 IP + UTC 当天的截断 SHA,同一 IP 一天内是同一个 hash,不同天换新;反推 IP 不现实。
