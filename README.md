# Maolao Image Studio

基于 React、FastAPI 和 MaolaoAPI 的对话式 4K 图片生成工作台，目标模型固定为 `gpt-image-2-4k`。

## 功能

- ChatGPT 风格的会话界面与对话搜索
- 文生图，或上传最多 16 张参考图进行图生图
- 支持 1:1、16:9、9:16 三种 4K 尺寸，一次最多生成 10 张图片
- 选择任意生成结果，通过自然语言继续优化
- 每个对话独立保存提示词、参数、任务状态和生成结果
- SQLite 与图片文件通过 Docker volume 持久化
- 删除对话时同步删除关联的上传图和生成图
- 后端持续轮询异步任务，页面关闭或服务重启后仍可恢复

## 本地 Docker 启动

1. 在项目根目录创建 `.env`：

   ```env
   MAOLAO_API_KEY=sk-your-api-key
   MAOLAO_BASE_URL=https://maolaoapi.com
   ```

2. 构建并启动：

   ```bash
   docker compose up -d --build
   ```

3. 打开 <http://localhost:5173>。

查看日志：

```bash
docker compose logs -f --tail=200
```

停止服务：

```bash
docker compose down
```

## GitHub Actions 镜像

推送到 `main` 后，GitHub Actions 会构建 Linux AMD64 镜像并发布到 GHCR：

- `ghcr.io/linyeegiong/maolao-image-studio-backend`
- `ghcr.io/linyeegiong/maolao-image-studio-frontend`

每个镜像都有 `latest` 和 `sha-<短提交号>` 标签。第一次发布完成后，到仓库的 GitHub Packages 页面检查两个包的 Package settings；如果仍为 Private，将 Change visibility 设置为 Public。公开镜像在服务器上无需执行 `docker login`。

## 服务器生产部署

服务器只需要 Docker Engine 和 Docker Compose 插件，不需要 Node.js、Python 或项目构建工具。

1. 新建部署目录并进入：

   ```bash
   mkdir -p /opt/maolao-image-studio
   cd /opt/maolao-image-studio
   ```

2. 从仓库下载 `compose.prod.yml` 和 `.env.example`：

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/LinYeeGiong/maolao-image-studio/main/compose.prod.yml
   curl -fsSL https://raw.githubusercontent.com/LinYeeGiong/maolao-image-studio/main/.env.example -o .env
   ```

3. 编辑 `.env`，填入真实密钥：

   ```env
   MAOLAO_API_KEY=sk-your-api-key
   MAOLAO_BASE_URL=https://maolaoapi.com
   ```

4. 拉取镜像并启动：

   ```bash
   docker compose -f compose.prod.yml pull
   docker compose -f compose.prod.yml up -d
   ```

5. 在服务器面板中新增反向代理，目标地址填写：

   ```text
   http://127.0.0.1:7820
   ```

服务只监听服务器本机的 `7820` 端口，不会直接暴露 FastAPI 后端端口。

### 更新

```bash
cd /opt/maolao-image-studio
curl -fsSLO https://raw.githubusercontent.com/LinYeeGiong/maolao-image-studio/main/compose.prod.yml
docker compose -f compose.prod.yml pull
docker compose -f compose.prod.yml up -d
```

### 查看状态和日志

```bash
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs -f --tail=200
```

### 按提交版本回滚

在 GitHub Actions 或 Packages 页面找到同一次构建的 `sha-xxxxxxx` 标签，在服务器 `.env` 中加入：

```env
BACKEND_IMAGE=ghcr.io/linyeegiong/maolao-image-studio-backend:sha-xxxxxxx
FRONTEND_IMAGE=ghcr.io/linyeegiong/maolao-image-studio-frontend:sha-xxxxxxx
```

然后执行：

```bash
docker compose -f compose.prod.yml up -d
```

恢复最新版时删除 `.env` 中的 `BACKEND_IMAGE` 和 `FRONTEND_IMAGE`，再执行 `pull` 和 `up -d`。

## 数据位置

容器内数据保存在 `/data`：

- `/data/maolao.db`：对话、轮次和任务记录
- `/data/media/`：用户参考图和生成图

查看数据卷：

```bash
docker volume inspect maolao-image-studio_maolao_data
```

`docker compose down` 默认不会删除历史记录。不要执行 `docker compose -f compose.prod.yml down -v`，除非确定要永久删除所有对话、上传图片和生成图片。
