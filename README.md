# maolao-image-studio

基于 FastAPI 官方全栈模板改造的简易异步生图前端，固定使用 `gpt-image-2-4k`。

- 不上传参考图：调用图片生成任务（`generations`）
- 上传参考图：自动调用图片编辑任务（`edits`）
- 前端自动轮询任务状态，并展示、下载生成结果
- API Key 仅由 FastAPI 服务端读取，不暴露给浏览器

## 一键启动

1. 编辑项目根目录的 `.env`，填写：

   ```env
   MAOLAO_API_KEY=sk-你的API_KEY
   ```

2. 在项目根目录运行：

   ```bash
   docker-compose up --build
   ```

3. 打开 <http://localhost:5173>。

停止服务：

```bash
docker-compose down
```

## 服务结构

- React 前端：输入 prompt、选择可选参考图、轮询并展示结果
- FastAPI 后端：安全代理 MaolaoAPI 的提交、查询与图片内容接口
- Nginx：提供静态前端，并把 `/api/` 转发到 FastAPI
