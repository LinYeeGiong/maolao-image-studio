# 16 Reference Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持每轮最多 16 张参考图，并将删除确认和重命名改成站内 Modal。

**Architecture:** 复用现有 `images` 表保存多张 reference 记录，以 multipart 重复字段贯通浏览器、FastAPI 和 MaolaoAPI。前端在现有单页组件中把单文件状态升级为文件数组，并用一个受控 Modal 状态替换浏览器原生弹框。

**Tech Stack:** React 19、TypeScript、FastAPI、SQLite、httpx、pytest、Vite

## Global Constraints

- 新上传参考图数量必须为 1–16 张。
- 单图上游字段为 `image`，多图上游字段为重复的 `image[]`。
- 不修改现有数据库 schema。
- 不调用 `window.confirm`、`window.prompt` 或 `window.alert`。
- 保持无图文生图、`n=1..10` 和单张历史生成图继承行为。

---

### Task 1: 上游多图请求契约

**Files:**
- Modify: `backend/unit_tests/test_image_gateway.py`
- Modify: `backend/app/api/routes/images.py`

**Interfaces:**
- Consumes: `reference_images: list[tuple[str, BytesIO, str]]`
- Produces: `MaolaoRequest.files: list[tuple[str, tuple[str, BytesIO, str]]] | None`

- [ ] **Step 1: Write the failing tests**

新增断言：单图文件键为 `image`；两图文件键依次为 `image[]`、`image[]`，并保留文件顺序。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q unit_tests/test_image_gateway.py`
Expected: FAIL，因为 `build_maolao_request` 仍只接收单个 `reference_image`。

- [ ] **Step 3: Write minimal implementation**

将请求对象的 `files` 改为 tuple 列表；将构造函数参数改为 `reference_images`；单图与多图选择对应字段名；读取一轮的全部 reference 记录。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q unit_tests/test_image_gateway.py`
Expected: 所有 gateway 测试 PASS。

### Task 2: FastAPI 多文件保存

**Files:**
- Modify: `backend/app/api/routes/conversations.py`
- Test: `backend/unit_tests/test_reference_uploads.py`

**Interfaces:**
- Consumes: 重复的本地 multipart `images` 字段以及兼容字段 `image`
- Produces: 最多 16 条按 `position` 排序的 reference 记录

- [ ] **Step 1: Write the failing test**

测试纯校验函数接受 16 个图片上传，拒绝 17 个上传，并返回明确的 422 错误。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q unit_tests/test_reference_uploads.py`
Expected: FAIL，因为数量校验函数尚不存在。

- [ ] **Step 3: Write minimal implementation**

增加 `MAX_REFERENCE_IMAGES = 16` 和数量校验函数；端点接收 `images: list[UploadFile]`，与兼容字段合并；先校验并读取，再统一落盘和插入数据库；异常时清理全部本轮文件。

- [ ] **Step 4: Run backend tests**

Run: `pytest -q`
Expected: 全部 PASS。

### Task 3: 前端多图选择与展示

**Files:**
- Modify: `frontend/src/ImageGenerator.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: 浏览器 `FileList`
- Produces: 最多 16 个 FormData `images` 字段及对应缩略图

- [ ] **Step 1: Replace single-file state**

使用 `File[]` 与对象 URL 数组，文件选择器开启 `multiple`；新增文件时只保留剩余容量，超限显示站内错误。

- [ ] **Step 2: Update submission and history rendering**

逐个追加 `images`；提交后清空数组；消息记录用 `filter` 展示全部 reference 图片。

- [ ] **Step 3: Add compact thumbnail grid**

输入区展示数量、缩略图和逐张删除按钮；桌面与移动端保持可滚动且不挤压输入框。

### Task 4: 站内 Modal

**Files:**
- Modify: `frontend/src/ImageGenerator.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Produces: `rename` 与 `delete` 两种受控 Modal

- [ ] **Step 1: Replace native dialogs**

删除按钮只打开确认 Modal；重命名按钮打开预填标题的输入 Modal；确认后执行原 API 调用。

- [ ] **Step 2: Add accessibility and styling**

加入 `role="dialog"`、`aria-modal="true"`、可见标题、取消/确认按钮、危险按钮样式和响应式遮罩。

### Task 5: 完整验证

**Files:**
- Verify: `backend`
- Verify: `frontend`

- [ ] **Step 1: Run backend tests**

Run: `pytest -q`
Expected: 0 failed。

- [ ] **Step 2: Run frontend build**

Run: `npm run build`
Expected: TypeScript 与 Vite 均 exit 0。

- [ ] **Step 3: Verify native dialogs are gone**

Run: `rg -n "window\\.(confirm|prompt|alert)|\\b(confirm|prompt|alert)\\(" frontend/src`
Expected: 无匹配。

- [ ] **Step 4: Review diff**

Run: `git diff --check`
Expected: 无空白错误；确认没有 `.env`、密钥或 Docker 数据文件进入 diff。

